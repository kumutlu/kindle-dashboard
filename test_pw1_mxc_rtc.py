#!/usr/bin/env python3
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MXC = ROOT / "kindle_scripts" / "mxc-rtc-scheduler.sh"
STOP_LEGACY = ROOT / "kindle_scripts" / "stop_legacy.sh"
START = ROOT / "kindle_scripts" / "start.sh"
STOP = ROOT / "kindle_scripts" / "stop.sh"
MXC_INSTALLER = ROOT / "kindle_scripts" / "install-mxc-rtc.sh"
KRON_INSTALLER = ROOT / "kindle_scripts" / "install-kindlecron.sh"


class PW1MXCRTCContractTests(unittest.TestCase):
    def test_required_scripts_exist_and_are_posix_shell(self):
        for script in (MXC, STOP_LEGACY, START, STOP, MXC_INSTALLER, KRON_INSTALLER):
            self.assertTrue(script.exists(), script)
            subprocess.run(["sh", "-n", str(script)], check=True)

    def test_scheduler_waits_for_commit_after_activation_ack_before_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            rtc = d / "wakeup_enable"
            power = d / "power_state"
            rtc.write_text("0", encoding="utf-8")
            power.write_text("standby mem\n", encoding="utf-8")
            calls = d / "calls.log"
            refresh = d / "refresh-once.sh"
            refresh.write_text(
                '#!/bin/sh\necho refresh >> "$DASHBOARD_DIR/calls.log"\nexit 0\n',
                encoding="utf-8",
            )
            refresh.chmod(0o755)
            mock_bin = d / "bin"
            mock_bin.mkdir()
            sync = mock_bin / "sync"
            sync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sync.chmod(0o755)

            env = {
                **os.environ,
                "PATH": f"{mock_bin}:{os.environ.get('PATH', '')}",
                "DASHBOARD_DIR": str(d),
                "RTC_WAKEUP": str(rtc),
                "POWER_STATE": str(power),
                "REFRESH_INTERVAL_MINUTES": "5",
            }
            proc = subprocess.Popen(
                ["sh", str(MXC)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            try:
                deadline = time.time() + 3
                pid_file = d / "mxc-rtc-scheduler.pid"
                while time.time() < deadline and not pid_file.exists():
                    time.sleep(0.05)
                self.assertTrue(pid_file.exists())
                time.sleep(0.25)
                self.assertFalse(calls.exists(), "standby must not refresh")
                self.assertEqual(rtc.read_text(encoding="utf-8"), "0")
                self.assertEqual(power.read_text(encoding="utf-8"), "standby mem\n")

                pid = int(pid_file.read_text(encoding="utf-8").strip())
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
                starttime = stat.rsplit(") ", 1)[1].split()[19]
                token = f"{pid} {starttime}"
                (d / "mxc-rtc.activate").write_text(token + "\n", encoding="utf-8")

                deadline = time.time() + 3
                active = d / "mxc-rtc.active"
                while time.time() < deadline and not active.exists():
                    time.sleep(0.05)
                self.assertTrue(active.exists(), "activation must produce ACK")
                self.assertEqual(active.read_text(encoding="utf-8").strip(), token)
                time.sleep(0.25)
                self.assertFalse(calls.exists(), "ACK alone must not start cadence")
                self.assertEqual(rtc.read_text(encoding="utf-8"), "0")
                self.assertEqual(power.read_text(encoding="utf-8"), "standby mem\n")

                (d / "mxc-rtc.commit").write_text(token + "\n", encoding="utf-8")
                deadline = time.time() + 3
                committed = d / "mxc-rtc.committed"
                while time.time() < deadline and not calls.exists():
                    time.sleep(0.05)
                self.assertTrue(calls.exists(), "commit must start cadence")
                self.assertIn("refresh", calls.read_text(encoding="utf-8"))
                self.assertTrue(committed.exists(), "commit must produce committed ACK")
                self.assertEqual(committed.read_text(encoding="utf-8").strip(), token)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                if proc.stdout:
                    proc.stdout.close()

    def test_active_cadence_uses_native_rtc_and_mem_suspend_primitives(self):
        text = MXC.read_text(encoding="utf-8")
        self.assertIn('printf \'%s\' "$INTERVAL_SECS" > "$RTC_WAKEUP"', text)
        self.assertIn('VERIFIED_SECS=$(cat "$RTC_WAKEUP"', text)
        self.assertIn('echo mem > "$POWER_STATE"', text)
        self.assertNotIn("rtcWakeup", text)
        self.assertNotIn("readyToSuspend", text)
        self.assertNotIn("wakeupFromSuspend", text)

    def test_stop_legacy_owns_refresh_loop_as_well_as_old_watchdog_loops(self):
        text = STOP_LEGACY.read_text(encoding="utf-8")
        self.assertIn("refresh.pid", text)
        self.assertIn("refresh.sh", text)
        self.assertIn("watchdog.pid", text)
        self.assertIn("dashboard_loop.pid", text)
        self.assertIn("get_start_time", text)

    def test_reboot_start_script_uses_same_identity_bound_protocol(self):
        text = START.read_text(encoding="utf-8")
        self.assertIn("mxc-rtc-scheduler.pid", text)
        self.assertIn("get_start_time", text)
        self.assertIn("mxc-rtc.activate", text)
        self.assertIn("mxc-rtc.active", text)
        self.assertIn("mxc-rtc.commit", text)
        self.assertIn("mxc-rtc.committed", text)
        self.assertIn("stop_legacy.sh", text)
        self.assertIn("NOAUTOSTART", text)

    def test_stop_script_validates_identity_before_terminating_native_scheduler(self):
        text = STOP.read_text(encoding="utf-8")
        self.assertIn("get_start_time", text)
        self.assertIn("mxc-rtc-scheduler.sh", text)
        self.assertIn('PROC_DIR="${PROC_DIR:-/proc}"', text)
        self.assertIn("mxc-rtc.active", text)
        self.assertIn("mxc-rtc.commit", text)
        self.assertIn("mxc-rtc.committed", text)

    def test_native_installer_installs_persistence_before_committing_cadence(self):
        text = MXC_INSTALLER.read_text(encoding="utf-8")
        ordered = [
            "Starting mxc-rtc-scheduler.sh in standby mode",
            "Standby identity verified",
            "Stopping legacy scheduler",
            "Legacy shutdown verified",
            "Activating mxc scheduler",
            "Activation ACK verified",
            "reboot persistence enabled",
            "Committing mxc scheduler cadence",
            "Activation successful",
        ]
        positions = [text.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        for token in (
            "mxc-rtc.active",
            "mxc-rtc.activate",
            "mxc-rtc.commit",
            "mxc-rtc.committed",
            "/mnt/us/dashboard/start.sh",
            "ENABLE_MXC_RTC_AUTOSTART",
        ):
            self.assertIn(token, text)
        self.assertNotIn("KindleCron v0.2.0", text)

    def test_kindlecron_installer_remains_native_rtc_free(self):
        text = KRON_INSTALLER.read_text(encoding="utf-8")
        for token in (
            "mxc-rtc-scheduler",
            "mxc-rtc.activate",
            "mxc-rtc.active",
            "mxc-rtc.commit",
            "mxc-rtc.committed",
            "start on started lab126",
            "ENABLE_MXC_RTC_AUTOSTART",
        ):
            self.assertNotIn(token, text)
        self.assertIn("persistent startup is not configured", text)


if __name__ == "__main__":
    unittest.main()

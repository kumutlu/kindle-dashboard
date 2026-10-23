#!/usr/bin/env python3
import os
import shutil
import signal
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
            shutil.copy(ROOT / "kindle_scripts/mxc-rtc-common.sh", d / "mxc-rtc-common.sh")
            (d / "ENABLE_MXC_RTC_AUTOSTART").write_text("mxc-rtc-v1\n")
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
                "MXC_SCHEDULER_LOCK": str(d / "scheduler.lock"),
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


class PW1FailureInjectionTests(unittest.TestCase):
    """Execute production shell scripts; only device/OS boundaries are substituted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        for path in (MXC, STOP, START, STOP_LEGACY, MXC_INSTALLER):
            shutil.copy(path, self.d / path.name)
            (self.d / path.name).chmod(0o755)
        common = ROOT / 'kindle_scripts/mxc-rtc-common.sh'
        if common.exists():
            shutil.copy(common, self.d / common.name)
        self.bin = self.d / 'bin'
        self.bin.mkdir()
        self.upstart = self.d / 'upstart'
        self.upstart.mkdir()
        self.rtc = self.d / 'rtc'
        self.rtc.write_text('0')
        self.power = self.d / 'power'
        self.power.write_text('mem\n')
        self.script('refresh-once.sh', 'echo refresh >> "$DASHBOARD_DIR/events"\n/bin/sleep .03')
        self.script('refresh.sh', 'while :; do /bin/sleep .03; done')
        self.script('start-dashboard.sh',
                    'echo legacy-start >> "$DASHBOARD_DIR/events"\n'
                    '"$DASHBOARD_DIR/refresh.sh" >/dev/null 2>&1 &\n'
                    'echo $! > "$DASHBOARD_DIR/refresh.pid"')
        self.script('sleep', 'exec /bin/sleep .03', self.bin)
        self.script('sync',
                    'echo sync >> "$DASHBOARD_DIR/events"\n'
                    'if [ -f "$DASHBOARD_DIR/block-sync" ]; then\n'
                    ' touch "$DASHBOARD_DIR/sync-entered"\n'
                    ' while [ -f "$DASHBOARD_DIR/block-sync" ]; do /bin/sleep .02; done\n'
                    'fi\n[ ! -f "$DASHBOARD_DIR/fail-sync" ]', self.bin)
        self.script('mntroot', 'echo "mntroot-$1" >> "$DASHBOARD_DIR/events"\nexit 0', self.bin)
        self.env = {**os.environ, 'DASHBOARD_DIR': str(self.d),
                    'RTC_WAKEUP': str(self.rtc), 'POWER_STATE': str(self.power),
                    'UPSTART_DIR': str(self.upstart),
                    'MNTROOT_BIN': str(self.bin / 'mntroot'),
                    'SLEEP_BIN': str(self.bin / 'sleep'),
                    'MXC_CONTROL_LOCK': str(self.d / 'control.lock'),
                    'MXC_SCHEDULER_LOCK': str(self.d / 'scheduler.lock'),
                    'PATH': f'{self.bin}:{os.environ["PATH"]}'}
        self.processes = []
        self.addCleanup(self.stop_processes)

    def script(self, name, body, directory=None):
        path = (directory or self.d) / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
        return path

    def stop_processes(self):
        for proc in self.processes:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            if proc.stdout:
                proc.stdout.close()

    def launch(self, name, *args):
        proc = subprocess.Popen(['sh', str(self.d / name), *args], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        self.processes.append(proc)
        return proc

    def wait_for(self, predicate, message='condition not reached'):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail(message)

    def events(self):
        path = self.d / 'events'
        return path.read_text() if path.exists() else ''

    def activate(self):
        proc = self.launch('mxc-rtc-scheduler.sh')
        self.wait_for(lambda: (self.d / 'mxc-rtc-scheduler.pid').exists())
        pid = int((self.d / 'mxc-rtc-scheduler.pid').read_text())
        st = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]
        token = f'{pid} {st}'
        (self.d / 'mxc-rtc.activate').write_text(token + '\n')
        self.wait_for(lambda: (self.d / 'mxc-rtc.active').exists())
        return proc, token

    def persist(self):
        (self.d / 'ENABLE_MXC_RTC_AUTOSTART').write_text('mxc-rtc-v1\n')

    def test_upstart_failure_restores_legacy_after_native_is_stopped(self):
        self.script('mntroot', 'exit 1', self.bin)
        proc = self.launch('install-mxc-rtc.sh')
        output = proc.communicate(timeout=6)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertIn('legacy-start', self.events(), output)
        self.assertFalse((self.d / 'mxc-rtc-scheduler.pid').exists())
        self.assertFalse((self.d / 'ENABLE_MXC_RTC_AUTOSTART').exists())

    def test_stop_failure_does_not_launch_legacy(self):
        self.persist()
        self.script('stop.sh', 'exit 1')
        proc = self.launch('start.sh')
        output = proc.communicate(timeout=6)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertNotIn('legacy-start', self.events())
        self.assertNotIn('refresh', self.events())

    def test_commit_rejects_missing_persistence_receipt(self):
        proc, token = self.activate()
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        proc.wait(timeout=4)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertNotIn('refresh', self.events())
        self.assertEqual(self.power.read_text(), 'mem\n')

    def test_commit_ack_waits_for_sync_and_verified_rtc(self):
        self.persist()
        proc, token = self.activate()
        (self.d / 'block-sync').touch()
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        self.wait_for(lambda: (self.d / 'sync-entered').exists())
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertNotIn('refresh', self.events())
        (self.d / 'block-sync').unlink()
        self.wait_for(lambda: (self.d / 'mxc-rtc.committed').exists())
        self.assertEqual(self.rtc.read_text(), '3600')
        self.assertEqual((self.d / 'mxc-rtc.committed').read_text().strip(), token)

    def test_rtc_arm_failure_never_acknowledges_commit_or_suspends(self):
        self.persist()
        proc, token = self.activate()
        self.rtc.unlink()
        self.rtc.mkdir()
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        proc.wait(timeout=4)
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertNotIn('refresh', self.events())
        self.assertEqual(self.power.read_text(), 'mem\n')

    def test_missing_legacy_startup_aborts_before_stopping_legacy(self):
        (self.d / 'start-dashboard.sh').unlink()
        self.script('stop_legacy.sh', 'echo legacy-stop >> "$DASHBOARD_DIR/events"')
        self.script('mntroot', 'exit 1', self.bin)
        proc = self.launch('install-mxc-rtc.sh')
        proc.communicate(timeout=6)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn('legacy-stop', self.events())

    def test_boot_failure_disables_native_autostart(self):
        self.persist()
        self.script('mxc-rtc-scheduler.sh', 'exit 1')
        proc = self.launch('start.sh')
        proc.communicate(timeout=6)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.d / 'ENABLE_MXC_RTC_AUTOSTART').exists())
        self.assertIn('legacy-start', self.events())

    def test_busy_handoff_lock_does_not_touch_existing_state(self):
        self.persist()
        (self.d / 'control.lock').mkdir()
        (self.d / 'mxc-rtc.active').write_text('existing-owner')
        proc = self.launch('start.sh')
        proc.communicate(timeout=6)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual((self.d / 'mxc-rtc.active').read_text(), 'existing-owner')

    def test_second_scheduler_cannot_overwrite_first_identity(self):
        first, token = self.activate()
        second = self.launch('mxc-rtc-scheduler.sh')
        second.wait(timeout=3)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual((self.d / 'mxc-rtc-scheduler.pid').read_text().strip(), token.split()[0])
        self.assertIsNone(first.poll())


    def test_successful_install_publishes_persistence_before_commit(self):
        proc = self.launch('install-mxc-rtc.sh')
        output = proc.communicate(timeout=8)[0]
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual((self.d / 'ENABLE_MXC_RTC_AUTOSTART').read_text(), 'mxc-rtc-v1\n')
        self.assertTrue((self.upstart / 'kindle-dashboard-native.conf').exists())
        self.assertIn('Activation successful', output)
        self.assertNotIn('legacy-start', self.events())
        self.assertTrue((self.d / 'mxc-rtc.committed').exists())

    def test_sync_failure_never_acknowledges_or_suspends(self):
        self.persist()
        proc, token = self.activate()
        (self.d / 'fail-sync').touch()
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        proc.wait(timeout=4)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertEqual(self.rtc.read_text(), '0')
        self.assertEqual(self.power.read_text(), 'mem\n')

    def test_rtc_readback_mismatch_never_acknowledges_or_suspends(self):
        self.persist()
        proc, token = self.activate()
        self.script('cat', 'if [ "$1" = "$RTC_WAKEUP" ]; then echo stale; else exec /bin/cat "$@"; fi', self.bin)
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        proc.wait(timeout=4)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertEqual(self.power.read_text(), 'mem\n')

    def test_stale_commit_token_does_not_start_cadence(self):
        self.persist()
        proc, token = self.activate()
        (self.d / 'mxc-rtc.commit').write_text('999999 0\n')
        self.wait_for(lambda: not (self.d / 'mxc-rtc.commit').exists())
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())
        self.assertNotIn('refresh', self.events())
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        self.wait_for(lambda: 'refresh' in self.events())

    def test_stop_recovers_missing_pid_and_stale_lock(self):
        (self.d / 'scheduler.lock').mkdir()
        (self.d / 'mxc-rtc.committed').write_text('999999 0\n')
        proc = self.launch('stop.sh')
        output = proc.communicate(timeout=4)[0]
        self.assertEqual(proc.returncode, 0, output)
        self.assertFalse((self.d / 'scheduler.lock').exists())
        self.assertFalse((self.d / 'mxc-rtc.committed').exists())

    def test_partial_legacy_shutdown_restarts_legacy_once(self):
        self.script('stop_legacy.sh',
                    'if [ ! -f "$DASHBOARD_DIR/partial-stop" ]; then\n'
                    ' touch "$DASHBOARD_DIR/partial-stop"; exit 1\nfi\nexit 0')
        proc = self.launch('install-mxc-rtc.sh')
        output = proc.communicate(timeout=8)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertEqual(self.events().count('legacy-start'), 1, output)
        self.assertFalse((self.d / 'mxc-rtc-scheduler.pid').exists())

    def test_failed_legacy_restart_is_reported(self):
        self.persist()
        self.script('mxc-rtc-scheduler.sh', 'exit 1')
        self.script('start-dashboard.sh', 'exit 1')
        proc = self.launch('start.sh')
        proc.communicate(timeout=6)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('ROLLBACK FAILED', (self.d / 'mxc-rtc.log').read_text())

    def test_reinstall_standby_failure_recovers_displaced_native_owner(self):
        self.persist()
        first, token = self.activate()
        (self.d / 'mxc-rtc.commit').write_text(token + '\n')
        self.wait_for(lambda: (self.d / 'mxc-rtc.committed').exists())
        self.script('mxc-rtc-scheduler.sh', 'exit 1')
        proc = self.launch('install-mxc-rtc.sh')
        output = proc.communicate(timeout=8)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertIn('legacy-start', self.events(), output)
        self.assertFalse((self.d / 'mxc-rtc-scheduler.pid').exists())

    def test_receipt_unlink_failure_still_attempts_native_shutdown(self):
        # An actual directory makes rm -f fail even when tests run as root.
        (self.d / 'ENABLE_MXC_RTC_AUTOSTART').mkdir()
        self.script('stop.sh', 'echo native-stop >> "$DASHBOARD_DIR/events"')
        common = self.d / 'mxc-rtc-common.sh'
        self.script('exercise-rollback.sh',
                    f'. "{common}"\nlog_msg() {{ echo "$1"; }}\nrollback_to_legacy')
        proc = self.launch('exercise-rollback.sh')
        proc.communicate(timeout=4)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('native-stop', self.events())

    def test_signal_during_persistence_install_rolls_back(self):
        self.script('mntroot',
                    'if [ "$1" = rw ]; then touch "$DASHBOARD_DIR/install-paused"; /bin/sleep .5; fi')
        proc = self.launch('install-mxc-rtc.sh')
        self.wait_for(lambda: (self.d / 'install-paused').exists())
        proc.terminate()
        output = proc.communicate(timeout=8)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertIn('legacy-start', self.events(), output)
        self.assertFalse((self.d / 'mxc-rtc-scheduler.pid').exists())
        self.assertFalse((self.d / 'ENABLE_MXC_RTC_AUTOSTART').exists())

    def test_supervisor_recovers_scheduler_death_after_commit(self):
        self.persist()
        proc = self.launch('start.sh', '--supervise')
        self.wait_for(lambda: (self.d / 'mxc-rtc.committed').exists())
        pid = int((self.d / 'mxc-rtc-scheduler.pid').read_text())
        os.kill(pid, signal.SIGKILL)
        output = proc.communicate(timeout=8)[0]
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertIn('legacy-start', self.events())
        self.assertFalse((self.d / 'ENABLE_MXC_RTC_AUTOSTART').exists())

    def test_installer_keeps_recovery_monitor_after_success(self):
        proc = self.launch('install-mxc-rtc.sh')
        output = proc.communicate(timeout=8)[0]
        self.assertEqual(proc.returncode, 0, output)
        pid = int((self.d / 'mxc-rtc-scheduler.pid').read_text())
        os.kill(pid, signal.SIGKILL)
        self.wait_for(lambda: 'legacy-start' in self.events())
        self.assertFalse((self.d / 'ENABLE_MXC_RTC_AUTOSTART').exists())


    def test_supervisor_respects_deliberate_stop(self):
        self.persist()
        proc = self.launch('start.sh', '--supervise')
        self.wait_for(lambda: (self.d / 'mxc-rtc.committed').exists())
        stop = self.launch('stop.sh')
        output = stop.communicate(timeout=6)[0]
        self.assertEqual(stop.returncode, 0, output)
        proc.communicate(timeout=6)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn('legacy-start', self.events())


if __name__ == "__main__":
    unittest.main()

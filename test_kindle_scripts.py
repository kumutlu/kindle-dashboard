#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REFRESH_SH = PROJECT_DIR / "kindle_scripts" / "refresh.sh"
REFRESH_ONCE_SH = PROJECT_DIR / "kindle_scripts" / "refresh-once.sh"
SEND_STATUS_SH = PROJECT_DIR / "kindle_scripts" / "send-status.sh"
INSTALL_KRON_SH = PROJECT_DIR / "kindle_scripts" / "install-kindlecron.sh"


class KindleScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tempdir.name)
        self.bin_dir = self.sandbox / "bin"
        self.bin_dir.mkdir(parents=True)
        shutil.copy2(REFRESH_ONCE_SH, self.sandbox / "refresh-once.sh")
        (self.sandbox / "refresh-once.sh").chmod(0o755)
        shutil.copy2(SEND_STATUS_SH, self.sandbox / "send-status.sh")
        (self.sandbox / "send-status.sh").chmod(0o755)
        
        self.create_mock_bin("wget", (
            "#!/bin/sh\n"
            "echo \"wget $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "if echo \"$@\" | grep -q \"config\"; then\n"
            "  echo '{\"refresh_interval_minutes\":30,\"kindle_frontlight\":12,\"wifi_power_save\":true,\"update_only_if_changed\":true}'\n"
            "  exit 0\n"
            "fi\n"
            "OUT=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"-O\" ]; then shift; OUT=\"$1\"; fi\n"
            "  shift\n"
            "done\n"
            "if [ -n \"$OUT\" ] && [ \"$OUT\" != \"-\" ]; then echo image > \"$OUT\"; fi\n"
        ))
        self.create_mock_bin("curl", (
            "#!/bin/sh\n"
            "echo \"curl $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "HDR=''\n"
            "OUT=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"-D\" ]; then shift; HDR=\"$1\"; fi\n"
            "  if [ \"$1\" = \"-o\" ]; then shift; OUT=\"$1\"; fi\n"
            "  if [ \"$1\" = \"-H\" ]; then shift; echo \"curl-header $1\" >> \"$DASHBOARD_DIR/calls.log\"; fi\n"
            "  if [ \"$1\" = \"--data\" ] || [ \"$1\" = \"--data-binary\" ] || [ \"$1\" = \"-d\" ]; then shift; echo \"curl-data $1\" >> \"$DASHBOARD_DIR/calls.log\"; fi\n"
            "  shift\n"
            "done\n"
            "if [ -n \"$HDR\" ]; then\n"
            "  if [ \"${MOCK_CURL_MODE:-ok}\" = \"not_modified\" ]; then\n"
            "    printf 'HTTP/1.1 304 Not Modified\\nETag: test-etag\\nLast-Modified: Wed, 10 Jul 2026 10:00:00 GMT\\n\\n' > \"$HDR\"\n"
            "    : > \"$OUT\"\n"
            "    exit 0\n"
            "  fi\n"
            "  printf 'HTTP/1.1 200 OK\\nETag: test-etag\\nLast-Modified: Wed, 10 Jul 2026 10:00:00 GMT\\n\\n' > \"$HDR\"\n"
            "fi\n"
            "if [ -n \"$OUT\" ]; then printf '%b' \"${MOCK_IMAGE_CONTENT:-\\211PNG\\r\\n\\032\\nimage-bytes}\" > \"$OUT\"; fi\n"
        ))
        self.create_mock_bin("ip", (
            "#!/bin/sh\n"
            "echo '192.168.68.167 via 192.168.68.1 dev wlan0 src 192.168.68.119'\n"
        ))
        self.create_mock_bin("date", (
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-u\" ]; then echo '2026-07-06T10:30:00Z'; else /bin/date \"$@\"; fi\n"
        ))
        self.create_mock_bin("lipc-set-prop", (
            "#!/bin/sh\n"
            "echo \"lipc-set-prop $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "if [ \"${MOCK_POWERBUTTON_FAIL:-0}\" = \"1\" ] && echo \"$@\" | grep -q \"powerButton\"; then\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        ))
        self.create_mock_bin("eips", "#!/bin/sh\necho \"eips $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("sleep", (
            "#!/bin/sh\n"
            "echo \"sleep $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "if [ \"$1\" = \"5\" ]; then exit 0; fi\n"
            "GPID=$(cat \"$DASHBOARD_DIR/dashboard_loop.pid\" 2>/dev/null)\n"
            "if [ -n \"$GPID\" ]; then\n"
            "  kill -s TERM $GPID\n"
            "fi\n"
            "exit 0\n"
        ))

        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["DASHBOARD_DIR"] = str(self.sandbox)
        self.env["EIPS_BIN"] = str(self.bin_dir / "eips")
        self.env["SLEEP_BIN"] = "sleep"
        self.env["MOCK_CURL_MODE"] = "ok"

    def tearDown(self):
        self.tempdir.cleanup()

    def create_mock_bin(self, name, content):
        bin_path = self.bin_dir / name
        bin_path.write_text(content, encoding="utf-8")
        bin_path.chmod(0o755)

    def run_script(self, script_path, timeout=5):
        try:
            res = subprocess.run(
                ["sh", str(script_path)],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as exc:
            return -1, exc.output or "", exc.stderr or ""

    def test_scripts_syntax(self):
        for script in (REFRESH_SH, REFRESH_ONCE_SH, SEND_STATUS_SH):
            res = subprocess.run(["sh", "-n", str(script)], check=True)
            self.assertEqual(res.returncode, 0)

    def generated_installer(self):
        import settings_server

        class FakeDevice:
            id = "kindle-131"
            type = "kindle_pw1"
            name = "Test Kindle"
            resolution = (758, 1024)
            enabled = True

        return settings_server.kindle_installer_script(
            FakeDevice(),
            {"status_token": "fake-token"},
            "192.168.68.167",
            8765,
            8767,
        )

    def test_refresh_sh_is_single_cycle_compatibility_shim(self):
        script = REFRESH_SH.read_text(encoding="utf-8")
        for token in (
            "while true",
            "RTC_SYS_DIR",
            "/sys/class/rtc",
            "wakealarm",
            "rtcWakeup",
            "powerButton",
            "NATIVE_RTC_SCHEDULER",
            "REFRESH_INTERVAL_MINUTES",
        ):
            self.assertNotIn(token, script)
        self.assertIn('exec "$REFRESH_ONCE_SH"', script)

    def test_refresh_once_has_no_scheduler_or_suspend_primitives(self):
        script = REFRESH_ONCE_SH.read_text(encoding="utf-8")
        for token in (
            "while true",
            "RTC_SYS_DIR",
            "/sys/class/rtc",
            "/sys/power/state",
            "wakealarm",
            "rtcWakeup",
            "powerButton",
            "watchdog.sh",
            "REFRESH_INTERVAL_MINUTES",
        ):
            self.assertNotIn(token, script)

    def test_refresh_once_preserves_proven_best_effort_network_wait(self):
        script = REFRESH_ONCE_SH.read_text(encoding="utf-8")
        self.assertIn("wait_for_network || true", script)
        self.assertNotIn("cmState", script)
        self.assertNotIn("curl -I", script)
        self.assertNotIn("curl --head", script)
        self.assertNotIn('fail "network/server not ready', script)

    def test_refresh_sh_runs_refresh_once_exactly_once_and_propagates_status(self):
        calls = self.sandbox / "refresh-once.calls"
        refresh_once = self.sandbox / "refresh-once.sh"
        refresh_once.write_text(
            f'#!/bin/sh\necho call >> "{calls}"\nexit 7\n',
            encoding="utf-8",
        )
        refresh_once.chmod(0o755)
        result = subprocess.run(
            ["sh", str(REFRESH_SH)],
            env={**self.env, "DASHBOARD_DIR": str(self.sandbox)},
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            calls.read_text(encoding="utf-8").splitlines(),
            ["call"],
        )
        self.assertIn("KindleCron owns scheduling", result.stdout)

    def test_generated_device_env_disables_legacy_schedulers(self):
        installer = self.generated_installer()
        self.assertIn('LOW_POWER_MODE="0"', installer)
        self.assertIn('NATIVE_RTC_SCHEDULER="0"', installer)

    def test_generated_installer_embeds_and_runs_kindlecron_integration(self):
        installer = self.generated_installer()
        self.assertIn(
            'cat <<\'EOF\' > "$DASHBOARD_DIR/install-kindlecron.sh"',
            installer,
        )
        self.assertIn(
            '"$DASHBOARD_DIR/install-kindlecron.sh" install',
            installer,
        )
        self.assertNotIn(
            '"$DASHBOARD_DIR/install-kindlecron.sh" install || true',
            installer,
        )

    def test_generated_installer_has_no_legacy_scheduler_autostart(self):
        installer = self.generated_installer()
        self.assertNotIn(
            "cat <<'UPSTART' > /etc/upstart/dashboard.conf",
            installer,
        )
        self.assertNotIn("start on started lab126", installer)
        self.assertNotIn(
            '"$DASHBOARD_DIR/watchdog.sh" >/dev/null 2>&1 &',
            installer,
        )

    def test_generated_installer_does_not_own_kmc_emergency_hook(self):
        installer = self.generated_installer()
        self.assertNotIn("/mnt/us/emergency.sh", installer)
        self.assertNotIn("/var/local/kmc", installer)
        self.assertNotIn("framework_ready", installer)

    def test_generated_installer_executes_verified_kindlecron_integration(self):
        kron_calls = self.sandbox / "generated-kron.calls"
        kron = self.sandbox / "kron"
        kron.write_text(
            "#!/bin/sh\n"
            f'printf "<%s>" "$@" >> "{kron_calls}"\n'
            "printf '\\n' >> \"" + str(kron_calls) + "\"\n"
            'case "$1" in\n'
            "  version) echo 'KindleCron v0.2.0 (test)' ;;\n"
            "  list) echo 'dashboard every 1h' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        kron.chmod(0o755)
        daemon_state = self.sandbox / "daemon.state"
        daemon_state.write_text("0\n", encoding="utf-8")
        result = subprocess.run(
            ["sh", "-c", self.generated_installer()],
            env={
                **self.env,
                "KRON_BIN": str(kron),
                "KRON_DAEMON_STATE_FILE": str(daemon_state),
                "KRON_DAEMON_LOG": str(self.sandbox / "kron.log"),
                "UPSTART_CONF": str(self.sandbox / "dashboard.conf"),
                "MNTROOT_BIN": "true",
                "SLEEP_BIN": "true",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = kron_calls.read_text(encoding="utf-8")
        self.assertIn(
            f"<add><-timeout><2m><dashboard><every 1h><{self.sandbox / 'refresh-once.sh'}>",
            calls,
        )
        self.assertIn("<daemon>", calls)
        self.assertIn("<list>", calls)

    def test_refresh_once_missing_device_id_uses_default(self):
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        
        calls_log = self.sandbox / "calls.log"
        self.assertTrue(calls_log.exists())
        calls = calls_log.read_text(encoding="utf-8")
        self.assertIn("http://192.168.68.167:8767/api/device/default-kindle/config", calls)
        self.assertIn("http://192.168.68.167:8765/device/default-kindle/image.png", calls)

    def test_refresh_once_valid_device_id_builds_correct_url(self):
        (self.sandbox / "device-id").write_text("kitchen-kindle\n")
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("http://192.168.68.167:8767/api/device/kitchen-kindle/config", calls)
        self.assertIn("http://192.168.68.167:8765/device/kitchen-kindle/image.png", calls or "")
        self.assertIn("http://192.168.68.167:8767/api/device/kitchen-kindle/status", calls)

    def test_refresh_once_toggles_wifi_and_saves_conditional_headers(self):
        code, _, _ = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)

        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.wifid enable 1", calls)
        self.assertIn("lipc-set-prop com.lab126.wifid enable 0", calls)
        self.assertEqual(
            (self.sandbox / "image.etag").read_text(encoding="utf-8").strip(),
            "test-etag",
        )
        self.assertEqual(
            (self.sandbox / "image.last_modified").read_text(
                encoding="utf-8"
            ).strip(),
            "Wed, 10 Jul 2026 10:00:00 GMT",
        )
        self.assertTrue((self.sandbox / "image.png").exists())

    def test_refresh_once_skips_display_refresh_when_server_returns_304(self):
        first_image = "\\211PNG\\r\\n\\032\\nfirst-image"
        self.env["MOCK_IMAGE_CONTENT"] = first_image
        code, _, _ = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.sandbox / "image.png").read_bytes(),
            first_image.encode("latin-1").decode("unicode_escape").encode("latin-1"),
        )

        (self.sandbox / "calls.log").unlink(missing_ok=True)
        self.env["MOCK_CURL_MODE"] = "not_modified"
        code, _, _ = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)

        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("curl-header If-None-Match: test-etag", calls)
        self.assertIn(
            "curl-header If-Modified-Since: Wed, 10 Jul 2026 10:00:00 GMT",
            calls,
        )
        self.assertNotIn("eips -g", calls)
        self.assertEqual(
            (self.sandbox / "image.png").read_bytes(),
            first_image.encode("latin-1").decode("unicode_escape").encode("latin-1"),
        )


    def test_send_status_posts_available_battery_json(self):
        power = self.sandbox / "power_supply" / "battery"
        power.mkdir(parents=True)
        (power / "capacity").write_text("82\n", encoding="utf-8")
        (power / "status").write_text("Discharging\n", encoding="utf-8")

        self.env["POWER_SUPPLY_DIR"] = str(self.sandbox / "power_supply")
        self.env["SERVER_HOST"] = "dashboard.local"
        self.env["DEVICE_ID"] = "kitchen-kindle"
        code, stdout, stderr = self.run_script(SEND_STATUS_SH)

        self.assertEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("http://dashboard.local:8767/api/device/kitchen-kindle/status", calls)
        self.assertIn('"battery_percent":82', calls)
        self.assertIn('"charging":false', calls)
        self.assertIn('"ip_address":"192.168.68.119"', calls)
        self.assertIn('"firmware_version":"kindle-refresh-1.0"', calls)
        self.assertIn('"last_refresh_at":"2026-07-06T10:30:00Z"', calls)

    def test_send_status_uses_optional_bearer_token_without_logging_value_elsewhere(self):
        self.env["STATUS_TOKEN"] = "secret-status-token"
        code, stdout, stderr = self.run_script(SEND_STATUS_SH)

        self.assertEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("Authorization: Bearer secret-status-token", calls)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_refresh_once_invalid_device_id_falls_back(self):
        for invalid in ("../../x", "bad;rm -rf", "a/b", ""):
            with self.subTest(invalid=invalid):
                calls_log = self.sandbox / "calls.log"
                calls_log.unlink(missing_ok=True)
                
                (self.sandbox / "device-id").write_text(invalid + "\n")
                code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
                self.assertEqual(code, 0)
                self.assertTrue(calls_log.exists(), msg=f"stdout={stdout} stderr={stderr}")
                calls = calls_log.read_text(encoding="utf-8")
                self.assertIn("/device/default-kindle/image.png", calls)


    def test_generated_legacy_entry_points_are_non_scheduling_shims(self):
        import re

        installer_script = self.generated_installer()

        def extract_script(name):
            match = re.search(
                f"cat <<'EOF' > \"\\$DASHBOARD_DIR/{name}\"\n(.*?)\nEOF",
                installer_script,
                re.DOTALL,
            )
            self.assertIsNotNone(match, msg=f"Marker for {name} not found")
            return match.group(1)

        loop_src = extract_script("dashboard_loop.sh")
        watchdog_src = extract_script("watchdog.sh")
        start_src = extract_script("start.sh")

        for source in (loop_src, watchdog_src, start_src):
            self.assertNotIn("while true", source)
        self.assertIn('exec "$DASHBOARD_DIR/refresh.sh"', loop_src)
        self.assertIn("KindleCron owns scheduling", watchdog_src)
        self.assertNotIn('&', watchdog_src)
        self.assertIn(
            'exec "$DASHBOARD_DIR/install-kindlecron.sh" start-daemon',
            start_src,
        )

        for name, source in (
            ("dashboard_loop.sh", loop_src),
            ("watchdog.sh", watchdog_src),
            ("start.sh", start_src),
        ):
            path = self.sandbox / name
            path.write_text(source + "\n", encoding="utf-8")
            path.chmod(0o755)

        calls = self.sandbox / "shim.calls"
        refresh = self.sandbox / "refresh.sh"
        refresh.write_text(
            f'#!/bin/sh\necho refresh >> "{calls}"\nexit 7\n',
            encoding="utf-8",
        )
        refresh.chmod(0o755)
        integration = self.sandbox / "install-kindlecron.sh"
        integration.write_text(
            f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 9\n',
            encoding="utf-8",
        )
        integration.chmod(0o755)

        loop = subprocess.run(
            ["sh", str(self.sandbox / "dashboard_loop.sh")],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(loop.returncode, 7)

        watchdog = subprocess.run(
            ["sh", str(self.sandbox / "watchdog.sh")],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(watchdog.returncode, 0)
        self.assertIn("KindleCron owns scheduling", watchdog.stdout)

        start = subprocess.run(
            ["sh", str(self.sandbox / "start.sh")],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(start.returncode, 9)
        self.assertEqual(
            calls.read_text(encoding="utf-8").splitlines(),
            ["refresh", "start-daemon"],
        )

    def test_prevent_screensaver_lifecycle_success(self):
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 1", calls)
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)
        idx1 = calls.index("lipc-set-prop com.lab126.powerd preventScreenSaver 1")
        idx0 = calls.index("lipc-set-prop com.lab126.powerd preventScreenSaver 0")
        self.assertLess(idx1, idx0)

    def test_prevent_screensaver_download_failure(self):
        self.create_mock_bin("curl", "#!/bin/sh\nexit 1")
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)

    def test_prevent_screensaver_invalid_png(self):
        self.create_mock_bin("curl", (
            "#!/bin/sh\n"
            "HDR=''\n"
            "OUT=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"-D\" ]; then shift; HDR=\"$1\"; fi\n"
            "  if [ \"$1\" = \"-o\" ]; then shift; OUT=\"$1\"; fi\n"
            "  shift\n"
            "done\n"
            "if [ -n \"$HDR\" ]; then printf 'HTTP/1.1 200 OK\\n\\n' > \"$HDR\"; fi\n"
            "if [ -n \"$OUT\" ]; then printf 'CORRUPTED_NOT_PNG' > \"$OUT\"; fi\n"
            "exit 0\n"
        ))
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)

    def test_prevent_screensaver_eips_failure(self):
        self.create_mock_bin("eips", "#!/bin/sh\nexit 1")
        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)

    def test_prevent_screensaver_signals(self):
        import signal, time
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=sig):
                self.create_mock_bin("curl", "#!/bin/sh\nsleep 10\nexit 0\n")
                proc = subprocess.Popen(
                    ["/bin/sh", str(REFRESH_ONCE_SH)],
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                time.sleep(0.3)
                proc.send_signal(sig)
                proc.wait(timeout=5)
                self.assertNotEqual(proc.returncode, 0)

                calls_file = self.sandbox / "calls.log"
                calls = calls_file.read_text(encoding="utf-8") if calls_file.exists() else ""
                self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)
                self.assertIn("lipc-set-prop com.lab126.wifid enable 0", calls)

    def test_early_exit_duplicate_lock_cleanup_safety(self):
        lock_file = Path("/tmp/kindle-refresh.lock")
        active_pid = str(os.getpid())
        lock_file.write_text(f"{active_pid}\n", encoding="utf-8")
        try:
            code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
            self.assertEqual(code, 0)
            self.assertTrue(lock_file.exists())
            self.assertEqual(lock_file.read_text(encoding="utf-8").strip(), active_pid)
            calls_file = self.sandbox / "calls.log"
            calls = calls_file.read_text(encoding="utf-8") if calls_file.exists() else ""
            self.assertNotIn("lipc-set-prop com.lab126.powerd preventScreenSaver 1", calls)
            self.assertNotIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls)
            self.assertNotIn("lipc-set-prop com.lab126.wifid enable 0", calls)
        finally:
            lock_file.unlink(missing_ok=True)

    def test_ownership_duplicate_process_non_mutation(self):
        lock_file = Path("/tmp/kindle-refresh.lock")
        parent_pid = str(os.getppid())
        lock_file.write_text(f"{parent_pid}\n", encoding="utf-8")
        try:
            code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
            self.assertEqual(code, 0)
            self.assertEqual(lock_file.read_text(encoding="utf-8").strip(), parent_pid)
            calls_file = self.sandbox / "calls.log"
            calls = calls_file.read_text(encoding="utf-8") if calls_file.exists() else ""
            self.assertNotIn("preventScreenSaver", calls)
            self.assertNotIn("wifid", calls)
        finally:
            lock_file.unlink(missing_ok=True)


    def test_successful_changed_image_refresh_synchronizes_overlay(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        self.env["LINKSS_SS"] = str(overlay_target)

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertTrue((self.sandbox / "image.png").exists())
        self.assertTrue(overlay_target.exists())
        self.assertEqual(
            (self.sandbox / "image.png").read_bytes(),
            overlay_target.read_bytes(),
        )

    def test_refresh_once_does_not_invoke_overlay_script(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        self.env["LINKSS_SS"] = str(overlay_target)

        overlay_script = self.sandbox / "apply-screensaver-overlay.sh"
        overlay_script.write_text(
            "#!/bin/sh\n"
            "echo \"SHOULD_NOT_BE_CALLED\" >> \"$DASHBOARD_DIR/calls.log\"\n",
            encoding="utf-8",
        )
        overlay_script.chmod(0o755)

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        calls = (self.sandbox / "calls.log").read_text(encoding="utf-8") if (self.sandbox / "calls.log").exists() else ""
        self.assertNotIn("SHOULD_NOT_BE_CALLED", calls)
        self.assertEqual(
            (self.sandbox / "image.png").read_bytes(),
            overlay_target.read_bytes(),
        )

    def test_refresh_once_contains_no_mount_or_overlay_script_calls(self):
        script_text = REFRESH_ONCE_SH.read_text(encoding="utf-8")
        self.assertNotIn("apply-screensaver-overlay", script_text)
        self.assertNotIn("mount", script_text)
        self.assertNotIn("umount", script_text)

    def test_invalid_png_download_does_not_update_overlay(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        overlay_target.write_bytes(b"old-valid-overlay-content")
        self.env["LINKSS_SS"] = str(overlay_target)

        self.create_mock_bin("curl", (
            "#!/bin/sh\n"
            "HDR=''\n"
            "OUT=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"-D\" ]; then shift; HDR=\"$1\"; fi\n"
            "  if [ \"$1\" = \"-o\" ]; then shift; OUT=\"$1\"; fi\n"
            "  shift\n"
            "done\n"
            "if [ -n \"$HDR\" ]; then printf 'HTTP/1.1 200 OK\\n\\n' > \"$HDR\"; fi\n"
            "if [ -n \"$OUT\" ]; then printf 'CORRUPTED_NOT_PNG' > \"$OUT\"; fi\n"
            "exit 0\n"
        ))

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        self.assertEqual(overlay_target.read_bytes(), b"old-valid-overlay-content")

    def test_failed_download_does_not_update_overlay(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        overlay_target.write_bytes(b"old-valid-overlay-content")
        self.env["LINKSS_SS"] = str(overlay_target)

        self.create_mock_bin("curl", "#!/bin/sh\nexit 1")

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        self.assertEqual(overlay_target.read_bytes(), b"old-valid-overlay-content")

    def test_304_not_modified_repairs_stale_overlay(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        old_png = b"\x89PNG\r\n\x1a\nold-stale-overlay-content"
        new_png = b"\x89PNG\r\n\x1a\nnew-current-dashboard-content"
        (self.sandbox / "image.png").write_bytes(new_png)
        overlay_target.write_bytes(old_png)
        self.env["LINKSS_SS"] = str(overlay_target)
        (self.sandbox / "image.etag").write_text("test-etag\n", encoding="utf-8")
        (self.sandbox / "image.last_modified").write_text("Wed, 10 Jul 2026 10:00:00 GMT\n", encoding="utf-8")
        self.env["MOCK_CURL_MODE"] = "not_modified"

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertEqual((self.sandbox / "image.png").read_bytes(), new_png)
        self.assertEqual(overlay_target.read_bytes(), new_png)

    def test_unchanged_download_repairs_stale_overlay(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        old_png = b"\x89PNG\r\n\x1a\nold-stale-overlay-content"
        new_png = b"\x89PNG\r\n\x1a\ncurrent-dashboard-content"
        (self.sandbox / "image.png").write_bytes(new_png)
        overlay_target.write_bytes(old_png)
        self.env["LINKSS_SS"] = str(overlay_target)
        self.env["MOCK_IMAGE_CONTENT"] = r"\211PNG\r\n\032\ncurrent-dashboard-content"

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertEqual((self.sandbox / "image.png").read_bytes(), new_png)
        self.assertEqual(overlay_target.read_bytes(), new_png)

    def test_already_synchronized_overlay_is_noop(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        current_png = b"\x89PNG\r\n\x1a\nsame-dashboard-content"
        (self.sandbox / "image.png").write_bytes(current_png)
        overlay_target.write_bytes(current_png)
        self.env["LINKSS_SS"] = str(overlay_target)
        self.env["MOCK_IMAGE_CONTENT"] = r"\211PNG\r\n\032\nsame-dashboard-content"

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertEqual(overlay_target.read_bytes(), current_png)

    def test_overlay_sync_happens_before_prevent_screensaver_released(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        self.env["LINKSS_SS"] = str(overlay_target)

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        calls = stdout
        self.assertIn("screensaver overlay synchronized", calls)
        calls_log = (self.sandbox / "calls.log").read_text(encoding="utf-8")
        self.assertIn("lipc-set-prop com.lab126.powerd preventScreenSaver 0", calls_log)

    def test_final_cmp_verification_proves_img_equals_linkss(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        self.env["LINKSS_SS"] = str(overlay_target)

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.sandbox / "image.png").read_bytes(),
            overlay_target.read_bytes(),
        )

    def test_atomic_temp_file_cleaned_on_failure(self):
        linkss_dir = self.sandbox / "linkss" / "screensavers"
        linkss_dir.mkdir(parents=True)
        overlay_target = linkss_dir / "bg_ss00.png"
        self.env["LINKSS_SS"] = str(overlay_target)

        self.create_mock_bin("curl", "#!/bin/sh\nexit 1\n")

        code, stdout, stderr = self.run_script(REFRESH_ONCE_SH)
        self.assertNotEqual(code, 0)
        temp_files = list(linkss_dir.glob("*.tmp.*"))
        self.assertEqual(len(temp_files), 0)


class KindleCronInstallTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.dashboard = self.root / "dashboard"
        self.kron_dir = self.root / "kron"
        self.dashboard.mkdir()
        self.kron_dir.mkdir()
        self.calls = self.root / "kron.calls"
        self.job_file = self.root / "dashboard.job"
        self.stop_marker = self.root / "stop.called"
        self.daemon_state = self.root / "daemon.count"
        self.daemon_state.write_text("0\n", encoding="utf-8")
        self.kron = self.kron_dir / "kron"
        self.upstart = self.root / "dashboard.conf"
        (self.dashboard / "refresh-once.sh").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8"
        )
        (self.dashboard / "refresh-once.sh").chmod(0o755)
        (self.dashboard / "stop.sh").write_text(
            f'#!/bin/sh\necho stop > "{self.stop_marker}"\n',
            encoding="utf-8",
        )
        (self.dashboard / "stop.sh").chmod(0o755)
        self.env = {
            **os.environ,
            "DASHBOARD_DIR": str(self.dashboard),
            "KRON_BIN": str(self.kron),
            "KRON_CALLS": str(self.calls),
            "KRON_JOB_FILE": str(self.job_file),
            "KRON_DAEMON_STATE_FILE": str(self.daemon_state),
            "KRON_DAEMON_LOG": str(self.root / "kron.log"),
            "UPSTART_CONF": str(self.upstart),
            "MNTROOT_BIN": "/usr/bin/true",
            "SLEEP_BIN": "/usr/bin/true",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def write_fake_kron(self, version="KindleCron v0.2.0 (test), go test linux/arm"):
        self.kron.write_text(
            "#!/bin/sh\n"
            "CMD=${1:-}\n"
            "for ARG in \"$@\"; do printf '<%s>' \"$ARG\" >> \"$KRON_CALLS\"; done\n"
            "printf '\\n' >> \"$KRON_CALLS\"\n"
            "case \"$CMD\" in\n"
            f"  version) echo '{version}' ;;\n"
            "  add) printf '%s\\n' \"$*\" > \"$KRON_JOB_FILE\" ;;\n"
            "  list) [ ! -f \"$KRON_JOB_FILE\" ] || cat \"$KRON_JOB_FILE\" ;;\n"
            "  daemon) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        self.kron.chmod(0o755)

    def write_device_env(self, minutes):
        (self.dashboard / "device.env").write_text(
            f'REFRESH_INTERVAL_MINUTES="{minutes}"\n', encoding="utf-8"
        )

    def run_installer(self, action="install", **overrides):
        return subprocess.run(
            ["sh", str(INSTALL_KRON_SH), action],
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_missing_kron_fails_before_legacy_stop(self):
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required KindleCron binary is missing", result.stderr)
        self.assertFalse(self.stop_marker.exists())

    def test_wrong_version_fails_before_legacy_stop(self):
        self.write_fake_kron("KindleCron v0.1.0")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected KindleCron v0.2.0", result.stderr)
        self.assertFalse(self.stop_marker.exists())

    def test_supported_intervals_use_exact_verified_argument_order(self):
        expected = {
            "5": "every 5m",
            "10": "every 10m",
            "15": "every 15m",
            "30": "every 30m",
            "60": "every 1h",
            "999": "every 1h",
        }
        for minutes, schedule in expected.items():
            with self.subTest(minutes=minutes):
                self.calls.unlink(missing_ok=True)
                self.job_file.unlink(missing_ok=True)
                self.stop_marker.unlink(missing_ok=True)
                self.daemon_state.write_text("0\n", encoding="utf-8")
                self.write_fake_kron()
                self.write_device_env(minutes)
                result = self.run_installer()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    f"<add><-timeout><2m><dashboard><{schedule}>"
                    f"<{self.dashboard / 'refresh-once.sh'}>",
                    self.calls.read_text(encoding="utf-8"),
                )

    def test_install_preserves_emergency_and_kmc_sentinels(self):
        self.write_fake_kron()
        emergency = self.root / "emergency.sh"
        kmc_conf = self.root / "kmc.conf"
        emergency.write_bytes(b"user recovery bytes\n")
        kmc_conf.write_bytes(b"user KMC config bytes\n")
        emergency.chmod(0o700)
        kmc_conf.chmod(0o600)
        before = (
            emergency.read_bytes(), emergency.stat().st_mode,
            kmc_conf.read_bytes(), kmc_conf.stat().st_mode,
        )
        result = self.run_installer(
            EMERGENCY_SH=str(emergency), KMC_CONF=str(kmc_conf)
        )
        after = (
            emergency.read_bytes(), emergency.stat().st_mode,
            kmc_conf.read_bytes(), kmc_conf.stat().st_mode,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)

    def test_install_logs_unresolved_boot_persistence(self):
        self.write_fake_kron()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        log = (self.dashboard / "kindlecron-install.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("persistent startup is not configured", log)

    def test_signal_after_rootfs_rw_restores_read_only_state(self):
        self.write_fake_kron()
        self.upstart.write_text(
            "exec /mnt/us/dashboard/start.sh\n", encoding="utf-8"
        )
        mntroot_calls = self.root / "mntroot.calls"
        mntroot = self.root / "mntroot"
        mntroot.write_text(
            "#!/bin/sh\n"
            f'echo "$1" >> "{mntroot_calls}"\n'
            "if [ \"$1\" = \"rw\" ]; then\n"
            "  ( /bin/sleep 0.1; kill -TERM $PPID ) &\n"
            "  /bin/sleep 2\n"
            "fi\n",
            encoding="utf-8",
        )
        mntroot.chmod(0o755)

        result = self.run_installer(MNTROOT_BIN=str(mntroot))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            mntroot_calls.read_text(encoding="utf-8").splitlines(),
            ["rw", "ro"],
        )

    def test_second_install_starts_one_daemon_and_replaces_one_job(self):
        self.write_fake_kron()
        first = self.run_installer()
        second = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        calls = self.calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("<daemon>" in line for line in calls), 1)
        self.assertEqual(
            sum("<add><-timeout><2m><dashboard>" in line for line in calls),
            2,
        )
        self.assertEqual(
            self.job_file.read_text(encoding="utf-8").split()[3],
            "dashboard",
        )



if __name__ == "__main__":
    unittest.main()

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


    def test_process_manager_scenarios(self):
        import settings_server

        class FakeDevice:
            id = "kindle-131"
            type = "kindle_pw1"
            name = "Test Kindle"
            resolution = (758, 1024)
            enabled = True

        device = FakeDevice()
        config = {"status_token": "fake-token"}
        installer_script = settings_server.kindle_installer_script(device, config, "192.168.68.167", 8767, 8767)

        def extract_script(name):
            import re
            match = re.search(f'cat <<\'EOF\' > "\\$DASHBOARD_DIR/{name}"\\n(.*?)\\nEOF', installer_script, re.DOTALL)
            if not match:
                raise ValueError(f"Marker for {name} not found")
            return match.group(1)

        watchdog_src = extract_script("watchdog.sh")
        loop_src = extract_script("dashboard_loop.sh")
        stop_src = extract_script("stop.sh")
        start_src = extract_script("start.sh")

        # Setup mock /proc directory
        proc_dir = self.sandbox / "proc"
        proc_dir.mkdir(parents=True, exist_ok=True)
        self.env["PROC_DIR"] = str(proc_dir)

        kill_func = (
            "kill() {\n"
            "  echo \"kill $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "  if [ \"$1\" = \"-0\" ]; then\n"
            "    PID=\"$2\"\n"
            "    if [ -d \"$PROC_DIR/$PID\" ]; then return 0; else return 1; fi\n"
            "  fi\n"
            "  if [ \"$1\" = \"-s\" ]; then\n"
            "    SIG=\"$2\"; PID=\"$3\"\n"
            "    echo \"signal $SIG sent to $PID\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "    if [ \"$SIG\" = \"TERM\" ]; then\n"
            "      if [ -f \"$DASHBOARD_DIR/proc_remove_on_term\" ]; then\n"
            "        rm -rf \"$PROC_DIR/$PID\"\n"
            "      fi\n"
            "      if [ -f \"$DASHBOARD_DIR/proc_change_on_term\" ]; then\n"
            "        echo \"0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 99999\" > \"$PROC_DIR/$PID/stat\"\n"
            "      fi\n"
            "    fi\n"
            "    return 0\n"
            "  fi\n"
            "  return 0\n"
            "}\n"
        )

        (self.sandbox / "watchdog.sh").write_text(kill_func + watchdog_src, encoding="utf-8")
        (self.sandbox / "watchdog.sh").chmod(0o755)
        (self.sandbox / "dashboard_loop.sh").write_text(kill_func + loop_src, encoding="utf-8")
        (self.sandbox / "dashboard_loop.sh").chmod(0o755)

        sleep_func = "sleep() { echo \"sleep $@\" >> \"$DASHBOARD_DIR/calls.log\"; }\n"
        (self.sandbox / "stop.sh").write_text(kill_func + sleep_func + stop_src, encoding="utf-8")
        (self.sandbox / "stop.sh").chmod(0o755)
        (self.sandbox / "start.sh").write_text(kill_func + start_src, encoding="utf-8")
        (self.sandbox / "start.sh").chmod(0o755)

        # Write dummy refresh.sh
        (self.sandbox / "refresh.sh").write_text("#!/bin/sh\necho refresh run\n", encoding="utf-8")
        (self.sandbox / "refresh.sh").chmod(0o755)

        # Helper to setup mock process in /proc
        def setup_proc(pid, cmdline, start_time="12345", comm="watchdog.sh"):
            pdir = proc_dir / str(pid)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / "cmdline").write_text(cmdline, encoding="utf-8")
            remaining_fields = ["0"] * 19 + [start_time]
            stat_content = f"{pid} ({comm}) " + " ".join(remaining_fields) + "\n"
            (pdir / "stat").write_text(stat_content, encoding="utf-8")

        def clean_proc(pid):
            shutil.rmtree(proc_dir / str(pid), ignore_errors=True)

        calls_log = self.sandbox / "calls.log"

        # ----------------------------------------------------
        # Scenario 1: dashboard_loop.pid before exec (cmdline has dashboard_loop.sh)
        # ----------------------------------------------------
        setup_proc(100, f"/bin/sh {self.sandbox}/dashboard_loop.sh")
        (self.sandbox / "dashboard_loop.pid").write_text("100\n", encoding="utf-8")

        # Running dashboard_loop.sh again should exit immediately (status 0)
        code, stdout, stderr = self.run_script(self.sandbox / "dashboard_loop.sh")
        self.assertEqual(code, 0)
        self.assertTrue((self.sandbox / "dashboard_loop.pid").exists())
        self.assertEqual((self.sandbox / "dashboard_loop.pid").read_text(encoding="utf-8").strip(), "100")

        # ----------------------------------------------------
        # Scenario 2: dashboard_loop.pid after exec (cmdline has refresh.sh)
        # ----------------------------------------------------
        setup_proc(100, f"/bin/sh {self.sandbox}/refresh.sh")
        code, stdout, stderr = self.run_script(self.sandbox / "dashboard_loop.sh")
        self.assertEqual(code, 0)
        self.assertEqual((self.sandbox / "dashboard_loop.pid").read_text(encoding="utf-8").strip(), "100")

        # ----------------------------------------------------
        # Scenario 3: Unrelated process with word "refresh" outside DASHBOARD_DIR
        # ----------------------------------------------------
        setup_proc(100, "/bin/sh /some/other/path/refresh.sh")
        # Run stop.sh, should NOT kill PID 100 because it is outside self.sandbox
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        self.assertEqual(code, 0)
        calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
        self.assertNotIn("signal TERM sent to 100", calls)
        self.assertTrue((self.sandbox / "dashboard_loop.pid").exists()) # Untouched since it is unrelated

        # ----------------------------------------------------
        # Scenario 4: Stale PID
        # ----------------------------------------------------
        clean_proc(100)
        # Run stop.sh, should clean up dashboard_loop.pid
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        self.assertEqual(code, 0)
        self.assertFalse((self.sandbox / "dashboard_loop.pid").exists())

        # ----------------------------------------------------
        # Scenario 5: Malformed PID
        # ----------------------------------------------------
        (self.sandbox / "dashboard_loop.pid").write_text("abc\n", encoding="utf-8")
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        self.assertEqual(code, 0)
        self.assertFalse((self.sandbox / "dashboard_loop.pid").exists())

        # ----------------------------------------------------
        # Scenario 6: Valid watchdog PID
        # ----------------------------------------------------
        setup_proc(200, f"/bin/sh {self.sandbox}/watchdog.sh")
        (self.sandbox / "watchdog.pid").write_text("200\n", encoding="utf-8")
        # Run watchdog.sh again, should exit immediately
        code, stdout, stderr = self.run_script(self.sandbox / "watchdog.sh")
        self.assertEqual(code, 0)

        # ----------------------------------------------------
        # Scenario 7: Unrelated reused watchdog PID
        # ----------------------------------------------------
        setup_proc(200, "/bin/sh /some/other/watchdog.sh")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        self.assertEqual(code, 0)
        calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
        self.assertNotIn("signal TERM sent to 200", calls)
        self.assertTrue((self.sandbox / "watchdog.pid").exists())

        # ----------------------------------------------------
        # Scenario 8: TERM success
        # ----------------------------------------------------
        setup_proc(200, f"/bin/sh {self.sandbox}/watchdog.sh")
        (self.sandbox / "proc_remove_on_term").write_text("", encoding="utf-8")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        self.assertEqual(code, 0)
        calls = calls_log.read_text(encoding="utf-8")
        self.assertIn("signal TERM sent to 200", calls)
        self.assertNotIn("kill -s KILL 200", calls)
        self.assertFalse((self.sandbox / "watchdog.pid").exists())

        # ----------------------------------------------------
        # Scenario 9: TERM timeout followed by KILL
        # ----------------------------------------------------
        shutil.rmtree(proc_dir, ignore_errors=True)
        proc_dir.mkdir(parents=True)
        setup_proc(300, f"/bin/sh {self.sandbox}/watchdog.sh")
        (self.sandbox / "watchdog.pid").write_text("300\n", encoding="utf-8")
        (self.sandbox / "proc_remove_on_term").unlink(missing_ok=True)
        (self.sandbox / "proc_change_on_term").unlink(missing_ok=True)
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        calls = calls_log.read_text(encoding="utf-8")
        self.assertIn("signal TERM sent to 300", calls)
        self.assertIn("kill -s KILL 300", calls)

        # ----------------------------------------------------
        # Scenario 10: PID reuse after TERM (process start time changes)
        # ----------------------------------------------------
        shutil.rmtree(proc_dir, ignore_errors=True)
        proc_dir.mkdir(parents=True)
        setup_proc(400, f"/bin/sh {self.sandbox}/watchdog.sh", start_time="12345")
        (self.sandbox / "watchdog.pid").write_text("400\n", encoding="utf-8")
        (self.sandbox / "proc_change_on_term").write_text("", encoding="utf-8")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        calls = calls_log.read_text(encoding="utf-8")
        # TERM was sent
        self.assertIn("signal TERM sent to 400", calls)
        # KILL must NOT be sent to 400 because its start time changed
        self.assertNotIn("kill -s KILL 400", calls)
        # The PID file watchdog.pid must NOT be deleted because it is unsafe
        self.assertTrue((self.sandbox / "watchdog.pid").exists())
        (self.sandbox / "proc_change_on_term").unlink(missing_ok=True)

        # ----------------------------------------------------
        # Scenario 11: Single-writer ownership verification
        # ----------------------------------------------------
        # Verify statically that start.sh doesn't write watchdog.pid
        self.assertNotIn("watchdog.pid", start_src)
        # Verify statically that watchdog.sh doesn't write dashboard_loop.pid
        self.assertNotIn('echo $! > "$PID_FILE"', watchdog_src)
        self.assertNotIn('echo $$ > "$PID_FILE"', watchdog_src)

        # ----------------------------------------------------
        # Scenario 12: process comm containing spaces in /proc/<pid>/stat
        # ----------------------------------------------------
        shutil.rmtree(proc_dir, ignore_errors=True)
        proc_dir.mkdir(parents=True)
        setup_proc(500, f"/bin/sh {self.sandbox}/watchdog.sh", start_time="98765", comm="watchdog.sh with spaces inside parentheses")
        (self.sandbox / "watchdog.pid").write_text("500\n", encoding="utf-8")
        (self.sandbox / "proc_remove_on_term").write_text("", encoding="utf-8")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        calls = calls_log.read_text(encoding="utf-8")
        self.assertIn("signal TERM sent to 500", calls)
        self.assertFalse((self.sandbox / "watchdog.pid").exists())
        (self.sandbox / "proc_remove_on_term").unlink(missing_ok=True)

        # ----------------------------------------------------
        # Scenario 13: regex-like path false positive
        # ----------------------------------------------------
        shutil.rmtree(proc_dir, ignore_errors=True)
        proc_dir.mkdir(parents=True)
        # Setup process with /mnt/us/dashboard/refresh.sh-fake
        setup_proc(600, f"/bin/sh {self.sandbox}/refresh.sh-fake")
        (self.sandbox / "dashboard_loop.pid").write_text("600\n", encoding="utf-8")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        # Since it is a false positive path, stop.sh must NOT send TERM
        calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
        self.assertNotIn("signal TERM sent to 600", calls)
        # The PID file must remain untouched
        self.assertTrue((self.sandbox / "dashboard_loop.pid").exists())

        # ----------------------------------------------------
        # Scenario 14: literal path match only
        # ----------------------------------------------------
        shutil.rmtree(proc_dir, ignore_errors=True)
        proc_dir.mkdir(parents=True)
        setup_proc(700, f"/bin/sh {self.sandbox}/refresh.sh")
        (self.sandbox / "dashboard_loop.pid").write_text("700\n", encoding="utf-8")
        (self.sandbox / "proc_remove_on_term").write_text("", encoding="utf-8")
        calls_log.unlink(missing_ok=True)
        code, stdout, stderr = self.run_script(self.sandbox / "stop.sh")
        calls = calls_log.read_text(encoding="utf-8")
        self.assertIn("signal TERM sent to 700", calls)
        self.assertFalse((self.sandbox / "dashboard_loop.pid").exists())
        (self.sandbox / "proc_remove_on_term").unlink(missing_ok=True)

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

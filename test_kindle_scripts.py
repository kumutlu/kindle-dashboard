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


class KindleScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tempdir.name)
        self.bin_dir = self.sandbox / "bin"
        self.bin_dir.mkdir(parents=True)
        
        self.create_mock_bin("wget", (
            "#!/bin/sh\n"
            "echo \"wget $@\" >> \"$DASHBOARD_DIR/calls.log\"\n"
            "if echo \"$@\" | grep -q \"config\"; then\n"
            "  echo '{\"refresh_interval_minutes\":30,\"kindle_frontlight\":12}'\n"
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
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--data\" ] || [ \"$1\" = \"--data-binary\" ] || [ \"$1\" = \"-d\" ]; then shift; echo \"curl-data $1\" >> \"$DASHBOARD_DIR/calls.log\"; fi\n"
            "  shift\n"
            "done\n"
        ))
        self.create_mock_bin("ip", (
            "#!/bin/sh\n"
            "echo '192.168.68.167 via 192.168.68.1 dev wlan0 src 192.168.68.119'\n"
        ))
        self.create_mock_bin("date", (
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-u\" ]; then echo '2026-07-06T10:30:00Z'; else /bin/date \"$@\"; fi\n"
        ))
        self.create_mock_bin("lipc-set-prop", "#!/bin/sh\necho \"lipc-set-prop $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("eips", "#!/bin/sh\necho \"eips $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("sleep", "#!/bin/sh\necho \"sleep $@\" >> \"$DASHBOARD_DIR/calls.log\"")

        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["DASHBOARD_DIR"] = str(self.sandbox)
        self.env["EIPS_BIN"] = str(self.bin_dir / "eips")

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
                
                calls = calls_log.read_text(encoding="utf-8")
                self.assertIn("default-kindle", calls)
                if invalid:
                    self.assertNotIn(f"/device/{invalid}/", calls)

    def test_static_analysis_for_process_leaks_and_sleep(self):
        refresh_content = REFRESH_SH.read_text(encoding="utf-8")
        once_content = REFRESH_ONCE_SH.read_text(encoding="utf-8")

        self.assertIn("/bin/sleep", refresh_content)
        for line in once_content.splitlines():
            line = line.strip()
            if "&" in line:
                clean_line = line.replace("&&", "").replace(">&", "")
                if "&" in clean_line:
                    self.fail(f"Background process leak detected in line: {line}")

        self.assertIn("LEGACY_LOCAL_URL=", refresh_content)
        self.assertIn("LEGACY_CONFIG_URL=", refresh_content)
        self.assertIn("LEGACY_LOCAL_URL=", once_content)
        self.assertIn("LEGACY_CONFIG_URL=", once_content)
        self.assertIn('SERVER_HOST="${SERVER_HOST:-192.168.68.167}"', refresh_content)
        self.assertIn('DEVICE_ID="${DEVICE_ID:-default-kindle}"', refresh_content)
        self.assertIn('SERVER_HOST="${SERVER_HOST:-192.168.68.167}"', once_content)
        self.assertIn('DEVICE_ID="${DEVICE_ID:-default-kindle}"', once_content)
        self.assertIn("send-status.sh", refresh_content)
        self.assertIn("send-status.sh", once_content)


if __name__ == "__main__":
    unittest.main()

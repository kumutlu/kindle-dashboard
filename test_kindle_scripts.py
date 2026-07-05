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
            "fi"
        ))
        self.create_mock_bin("curl", "#!/bin/sh\necho \"curl $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("lipc-set-prop", "#!/bin/sh\necho \"lipc-set-prop $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("eips", "#!/bin/sh\necho \"eips $@\" >> \"$DASHBOARD_DIR/calls.log\"")
        self.create_mock_bin("sleep", "#!/bin/sh\necho \"sleep $@\" >> \"$DASHBOARD_DIR/calls.log\"")

        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["DASHBOARD_DIR"] = str(self.sandbox)

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
        for script in (REFRESH_SH, REFRESH_ONCE_SH):
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


if __name__ == "__main__":
    unittest.main()

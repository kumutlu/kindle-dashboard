#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REFRESH_ONCE = ROOT / "kindle_scripts" / "refresh-once.sh"
SERVE_IMAGE = ROOT / "serve_image.py"


class ImagePipelineContractTests(unittest.TestCase):
    def test_refresh_script_has_no_shared_image_fallback_and_is_cache_safe(self):
        text = REFRESH_ONCE.read_text(encoding="utf-8")
        self.assertNotIn("weather.png", text)
        self.assertNotIn("PUBLIC_URL", text)
        self.assertIn("?t=", text)
        self.assertIn("Cache-Control: no-cache", text)
        self.assertIn("-fL", text)
        self.assertIn('EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"', text)
        self.assertIn('"$EIPS_BIN" -g "$IMG"', text)
        self.assertIn("sha256sum", text)

    def test_failed_device_download_returns_nonzero_and_preserves_no_display_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bindir = root / "bin"
            bindir.mkdir()
            (root / "image.png").write_bytes(b"old")
            (root / "device.env").write_text(
                "DEVICE_ID=default-kindle\n"
                "IMAGE_URL=http://server/device/default-kindle/image.png\n"
                "WIFI_POWER_SAVE=0\n",
                encoding="utf-8",
            )

            def tool(name, body):
                path = bindir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            tool("curl", "#!/bin/sh\necho 'network failed' >&2\nexit 22\n")
            tool("wget", "#!/bin/sh\necho '{\"kindle_frontlight\":8}'\n")
            tool("lipc-set-prop", "#!/bin/sh\nexit 0\n")
            tool("eips", "#!/bin/sh\nprintf 'eips called\\n' >> \"$DASHBOARD_DIR/eips.log\"\n")
            tool("ifconfig", "#!/bin/sh\nprintf 'inet addr:192.168.1.2\\n'\n")
            tool("ip", "#!/bin/sh\nexit 1\n")
            tool("sleep", "#!/bin/sh\nexit 0\n")
            tool("sha256sum", "/usr/bin/sha256sum \"$@\"\n")

            env = dict(os.environ)
            env.update(
                PATH=f"{bindir}:{os.environ.get('PATH', '')}",
                DASHBOARD_DIR=str(root),
                SERVER_HOST="server",
                DEVICE_ID="default-kindle",
                WIFI_POWER_SAVE="0",
            )
            result = subprocess.run(
                ["sh", str(REFRESH_ONCE)],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("network failed", result.stderr)
            self.assertEqual((root / "image.png").read_bytes(), b"old")
            self.assertFalse((root / "eips.log").exists())

    def test_server_advertises_image_sha256(self):
        text = SERVE_IMAGE.read_text(encoding="utf-8")
        self.assertIn("X-Image-SHA256", text)
        self.assertIn("hashlib.sha256", text)


if __name__ == "__main__":
    unittest.main()

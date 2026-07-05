#!/usr/bin/env python3
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import serve_image
import weather_image
from device_registry import DeviceRegistry


class DeviceImageServerTests(unittest.TestCase):
    PNG_BYTES = b"\x89PNG\r\n\x1a\ncheckpoint-two-image"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "dashboard_config.json").write_text(
            json.dumps(weather_image.DEFAULT_CONFIG),
            encoding="utf-8",
        )
        self.legacy_image = self.root / "kindle_weather.png"
        self.legacy_image.write_bytes(self.PNG_BYTES)
        self.registry = DeviceRegistry(self.root)
        self.registry.get("default-kindle")
        self.server = serve_image.make_server(
            host="127.0.0.1",
            port=0,
            registry=self.registry,
            legacy_image_path=self.legacy_image,
            battery_file=self.root / "battery.txt",
            access_log_path=self.root / "access.log",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, method="GET"):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, headers, body

    def test_default_device_image_returns_png_for_get_and_head(self):
        status, headers, body = self.request(
            "/device/default-kindle/image.png"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body, self.PNG_BYTES)

        status, headers, body = self.request(
            "/device/default-kindle/image.png",
            method="HEAD",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(
            int(headers["Content-Length"]),
            len(self.PNG_BYTES),
        )
        self.assertEqual(body, b"")

    def test_weather_png_remains_the_live_default_alias(self):
        newer = b"\x89PNG\r\n\x1a\nnewer-legacy-image"
        self.legacy_image.write_bytes(newer)

        status, headers, body = self.request("/weather.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body, newer)

        status, _, device_body = self.request(
            "/device/default-kindle/image.png"
        )
        self.assertEqual(status, 200)
        self.assertEqual(device_body, newer)

    def test_invalid_unknown_and_traversal_device_paths_are_404(self):
        for path in (
            "/device/missing/image.png",
            "/device/../image.png",
            "/device/%2e%2e/image.png",
            "/device/default-kindle/../../dashboard_config.json",
            "/device/default-kindle/image.png/extra",
            "/device/UPPERCASE/image.png",
        ):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(
                    str(self.root).encode("utf-8"),
                    body,
                )

    def test_weather_battery_query_behavior_is_preserved(self):
        status, _, _ = self.request("/weather.png?batt=73")
        self.assertEqual(status, 200)
        self.assertEqual(
            (self.root / "battery.txt").read_text(encoding="utf-8"),
            "73",
        )


if __name__ == "__main__":
    unittest.main()

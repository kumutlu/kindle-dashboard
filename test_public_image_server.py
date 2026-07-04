#!/usr/bin/env python3
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import public_image_server


class PublicImageServerTests(unittest.TestCase):
    TOKEN = "test-token-that-is-not-used-outside-this-test"
    PNG_BYTES = b"\x89PNG\r\n\x1a\nisolated-test-image"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.tempdir.name) / "kindle_weather.png"
        self.image_path.write_bytes(self.PNG_BYTES)
        
        # Patch load_config to default to home_dashboard to bypass Maarif checks in non-Maarif tests
        self.load_config_patcher = mock.patch("weather_image.load_config")
        self.mock_load_config = self.load_config_patcher.start()
        self.mock_load_config.return_value = {"theme": "home_dashboard"}
        
        self.server = public_image_server.make_server(
            image_path=self.image_path,
            token=self.TOKEN,
            host="127.0.0.1",
            port=0,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.load_config_patcher.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, token=None, method="GET"):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=2,
        )
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    def test_missing_token_returns_403(self):
        status, _, body = self.request("/weather.png")
        self.assertEqual(status, 403)
        self.assertNotIn(self.TOKEN.encode(), body)

    def test_wrong_token_returns_403(self):
        status, _, body = self.request("/weather.png", "wrong-token")
        self.assertEqual(status, 403)
        self.assertNotIn(self.TOKEN.encode(), body)

    def test_correct_token_returns_exact_png(self):
        status, headers, body = self.request("/weather.png", self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, self.PNG_BYTES)

    def test_head_request_returns_no_body(self):
        status, headers, body = self.request("/weather.png", self.TOKEN, method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Length"], str(len(self.PNG_BYTES)))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, b"")

    def test_root_returns_404_even_with_token(self):
        status, _, _ = self.request("/", self.TOKEN)
        self.assertEqual(status, 404)

    def test_project_file_path_returns_404_even_with_token(self):
        status, _, _ = self.request("/serve_image.py", self.TOKEN)
        self.assertEqual(status, 404)

    @mock.patch("weather_image.load_config")
    @mock.patch("weather_image.should_regenerate_maarif")
    @mock.patch("weather_image.generate_dashboard_safe")
    def test_weather_request_triggers_maarif_regeneration_if_needed(self, mock_gen, mock_should, mock_load):
        # 1. Maarif Calendar theme with date change triggers regeneration
        mock_load.return_value = {"theme": "maarif_calendar", "timezone": "Europe/London"}
        mock_should.return_value = True
        
        status, _, _ = self.request("/weather.png", self.TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(mock_should.called)
        self.assertTrue(mock_gen.called)
        
        # Reset mocks
        mock_gen.reset_mock()
        mock_should.reset_mock()
        
        # 2. Maarif Calendar theme with same date does NOT trigger regeneration
        mock_should.return_value = False
        status, _, _ = self.request("/weather.png", self.TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(mock_should.called)
        self.assertFalse(mock_gen.called)

        # Reset mocks
        mock_gen.reset_mock()
        mock_should.reset_mock()

        # 3. Non-maarif theme does not call should_regenerate_maarif
        mock_load.return_value = {"theme": "home_dashboard"}
        status, _, _ = self.request("/weather.png", self.TOKEN)
        self.assertEqual(status, 200)
        self.assertFalse(mock_should.called)
        self.assertFalse(mock_gen.called)


if __name__ == "__main__":
    unittest.main()

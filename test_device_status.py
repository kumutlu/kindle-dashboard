#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from device_registry import DeviceRegistry
import device_status


class DeviceStatusTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.registry = DeviceRegistry(self.root)
        self.default = self.registry.get("default-kindle")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_validate_allows_missing_battery_fields_and_adds_last_seen(self):
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        status = device_status.validate_status_update(
            {"ip_address": "192.168.68.50"},
            now=now,
        )

        self.assertIsNone(status["battery_percent"])
        self.assertEqual(status["ip_address"], "192.168.68.50")
        self.assertEqual(status["last_seen"], "2026-07-06T12:00:00+00:00")

    def test_status_is_written_and_read_from_device_folder(self):
        saved = device_status.save_status(
            self.default,
            {
                "battery_percent": 88,
                "charging": True,
                "firmware_version": "5.6.1.1",
            },
            now=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        )

        status_path = self.root / "devices/default-kindle/status.json"
        self.assertTrue(status_path.exists())
        self.assertEqual(saved["battery_percent"], 88)
        self.assertEqual(
            device_status.load_status(self.default)["firmware_version"],
            "5.6.1.1",
        )

    def test_offline_logic_uses_device_specific_thresholds(self):
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        kindle_recent = {"last_seen": (now - timedelta(minutes=89)).isoformat()}
        kindle_old = {"last_seen": (now - timedelta(minutes=91)).isoformat()}
        esp_recent = {"last_seen": (now - timedelta(minutes=29)).isoformat()}
        esp_old = {"last_seen": (now - timedelta(minutes=31)).isoformat()}

        self.assertTrue(
            device_status.status_summary(
                self.default,
                kindle_recent,
                now=now,
            )["online"]
        )
        self.assertFalse(
            device_status.status_summary(
                self.default,
                kindle_old,
                now=now,
            )["online"]
        )
        esp = self.registry.add({
            "id": "office-esp32",
            "name": "Office ESP32",
            "type": "esp32_epaper",
            "resolution": [800, 480],
            "enabled": True,
            "config_path": "devices/office-esp32/config.json",
            "image_path": "devices/office-esp32/image.png",
            "connection": {"method": "http", "host": "192.168.68.51"},
        })
        self.assertTrue(
            device_status.status_summary(
                esp,
                esp_recent,
                now=now,
            )["online"]
        )
        self.assertFalse(
            device_status.status_summary(
                esp,
                esp_old,
                now=now,
            )["online"]
        )

    def test_config_token_is_read_without_exposing_other_config_fields(self):
        raw = {"status_token": "secret-device-token", "theme": "home_dashboard"}
        self.default.config_path.write_text(
            json.dumps(raw),
            encoding="utf-8",
        )

        self.assertEqual(
            device_status.read_status_token(self.default),
            "secret-device-token",
        )


if __name__ == "__main__":
    unittest.main()

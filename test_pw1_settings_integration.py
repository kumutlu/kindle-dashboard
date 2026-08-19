#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import settings_server
import weather_image
from device_registry import DeviceRegistry


class PW1SettingsIntegrationTests(unittest.TestCase):
    def render(self, *, device_id="default-kindle", device_type="kindle_pw1", scheduler=None):
        device = SimpleNamespace(id=device_id, type=device_type)
        config = {
            "status_token": "test-status-token",
            "refresh_interval_minutes": 60,
            "wifi_power_save": True,
            "update_only_if_changed": True,
        }
        if scheduler is not None:
            config["scheduler"] = scheduler
        return settings_server.kindle_installer_script(
            device,
            config,
            "192.168.68.167",
            8765,
            8767,
        )

    def test_default_kindlecron_generation_remains_native_rtc_free(self):
        script = self.render(device_id="kindle-131", device_type="kindle_kt4")
        self.assertIn('"$DASHBOARD_DIR/install-kindlecron.sh" install', script)
        self.assertIn('NATIVE_RTC_SCHEDULER="0"', script)
        self.assertNotIn("install-mxc-rtc.sh", script)
        self.assertNotIn("mxc-rtc-scheduler.sh", script)
        self.assertNotIn("ENABLE_MXC_RTC_AUTOSTART", script)
        self.assertNotIn("start on started lab126", script)

    def test_pw1_mxc_generation_bundles_only_native_scheduler_stack(self):
        script = self.render(scheduler="mxc_rtc")
        self.assertIn('SCHEDULER_BACKEND="mxc_rtc"', script)
        self.assertIn('NATIVE_RTC_SCHEDULER="1"', script)
        for name in (
            "mxc-rtc-scheduler.sh",
            "stop_legacy.sh",
            "start.sh",
            "stop.sh",
            "install-mxc-rtc.sh",
        ):
            self.assertIn(f'$DASHBOARD_DIR/{name}', script)
        self.assertIn('"$DASHBOARD_DIR/install-mxc-rtc.sh" install', script)
        self.assertNotIn('"$DASHBOARD_DIR/install-kindlecron.sh" install', script)
        self.assertNotIn("KindleCron v0.2.0", script)

    def test_mxc_scheduler_survives_supported_config_validation(self):
        config = dict(weather_image.DEFAULT_CONFIG)
        config["scheduler"] = "mxc_rtc"

        validated = weather_image.validate_config(config)

        self.assertEqual(validated["scheduler"], "mxc_rtc")
        script = self.render(scheduler=validated["scheduler"])
        self.assertIn('SCHEDULER_BACKEND="mxc_rtc"', script)
        self.assertIn('"$DASHBOARD_DIR/install-mxc-rtc.sh" install', script)

    def test_invalid_scheduler_is_rejected_by_config_schema(self):
        config = dict(weather_image.DEFAULT_CONFIG)
        config["scheduler"] = "not-a-backend"

        with self.assertRaisesRegex(ValueError, "scheduler"):
            weather_image.validate_config(config)

    def test_settings_update_preserves_native_scheduler_when_form_omits_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy_path = root / "dashboard_config.json"
            settings_server.atomic_write_config(
                legacy_path,
                weather_image.DEFAULT_CONFIG,
            )
            registry = DeviceRegistry(root)
            device = registry.get("default-kindle")
            native_config = dict(weather_image.DEFAULT_CONFIG)
            native_config["scheduler"] = "mxc_rtc"
            settings_server.atomic_write_config(device.config_path, native_config)

            candidate = dict(weather_image.DEFAULT_CONFIG)
            candidate.pop("scheduler")
            candidate["theme"] = "minimal_weather"
            settings_server.update_device_config(
                registry,
                "default-kindle",
                legacy_path,
                candidate,
                lambda _device_id: None,
            )

            saved = json.loads(device.config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["scheduler"], "mxc_rtc")

    def test_public_device_config_exposes_scheduler_backend(self):
        device = SimpleNamespace(
            id="default-kindle",
            name="Default Kindle",
            type="kindle_pw1",
            resolution=(758, 1024),
            enabled=True,
        )
        config = dict(weather_image.DEFAULT_CONFIG)
        config["scheduler"] = "mxc_rtc"

        payload = settings_server.public_device_config(device, config)

        self.assertEqual(payload["scheduler"], "mxc_rtc")

    def test_invalid_scheduler_falls_back_to_kindlecron(self):
        script = self.render(scheduler="not-a-backend")
        self.assertIn('"$DASHBOARD_DIR/install-kindlecron.sh" install', script)
        self.assertNotIn("install-mxc-rtc.sh", script)


if __name__ == "__main__":
    unittest.main()

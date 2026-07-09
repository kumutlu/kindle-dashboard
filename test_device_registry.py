#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from device_registry import (
    DeviceNotFoundError,
    DeviceRegistry,
    RegistryValidationError,
)


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.legacy_config = {
            "title": "MIGRATED",
            "theme": "family_dashboard",
        }
        (self.root / "dashboard_config.json").write_text(
            json.dumps(self.legacy_config),
            encoding="utf-8",
        )
        (self.root / "kindle_weather.png").write_bytes(
            b"\x89PNG\r\n\x1a\nlegacy-image"
        )
        self.registry = DeviceRegistry(
            self.root,
            config_validator=lambda value: dict(value),
            default_config={"title": "DEFAULT", "theme": "home_dashboard"},
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def read_registry(self):
        return json.loads(
            (self.root / "devices.json").read_text(encoding="utf-8")
        )

    def test_missing_registry_creates_default_and_migrates_legacy_files(self):
        device = self.registry.get("default-kindle")

        self.assertEqual(device.id, "default-kindle")
        self.assertEqual(device.type, "kindle_pw1")
        self.assertEqual(device.resolution, (758, 1024))
        self.assertEqual(
            json.loads(device.config_path.read_text(encoding="utf-8")),
            self.legacy_config,
        )
        self.assertEqual(
            device.image_path.read_bytes(),
            b"\x89PNG\r\n\x1a\nlegacy-image",
        )
        stored = self.read_registry()["devices"][0]
        self.assertEqual(
            stored["config_path"],
            "devices/default-kindle/config.json",
        )
        self.assertEqual(
            stored["image_path"],
            "devices/default-kindle/image.png",
        )
        self.assertEqual(
            stored["connection"],
            {
                "host": "192.168.68.119",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
        )

    def test_missing_legacy_files_use_defaults_without_creating_fake_image(self):
        (self.root / "dashboard_config.json").unlink()
        (self.root / "kindle_weather.png").unlink()

        device = self.registry.get("default-kindle")

        self.assertEqual(
            json.loads(device.config_path.read_text(encoding="utf-8")),
            {"title": "DEFAULT", "theme": "home_dashboard"},
        )
        self.assertFalse(device.image_path.exists())

    def test_existing_device_files_are_not_overwritten(self):
        device_dir = self.root / "devices" / "default-kindle"
        device_dir.mkdir(parents=True)
        device_config = {"title": "KEEP", "theme": "compact_dashboard"}
        (device_dir / "config.json").write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )
        (device_dir / "image.png").write_bytes(b"keep-image")

        device = self.registry.get("default-kindle")

        self.assertEqual(
            json.loads(device.config_path.read_text(encoding="utf-8")),
            device_config,
        )
        self.assertEqual(device.image_path.read_bytes(), b"keep-image")

    def test_invalid_or_duplicate_device_ids_are_rejected(self):
        duplicate = self.read_registry_after_default()
        duplicate["devices"].append(dict(duplicate["devices"][0]))
        with self.assertRaisesRegex(
            RegistryValidationError,
            "duplicate device id",
        ):
            self.registry.validate_registry(duplicate)

        for invalid in (
            "",
            "Kitchen Kindle",
            "../kindle",
            "UPPERCASE",
            "-leading",
            "a" * 65,
        ):
            candidate = self.default_record()
            candidate["id"] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(RegistryValidationError):
                    self.registry.validate_registry({"devices": [candidate]})

    def test_traversal_and_mismatched_paths_are_rejected(self):
        for field, value in (
            ("config_path", "../dashboard_config.json"),
            ("image_path", "/tmp/image.png"),
            ("config_path", "devices/other/config.json"),
            ("image_path", "devices/default-kindle/other.png"),
        ):
            candidate = self.default_record()
            candidate[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(RegistryValidationError):
                    self.registry.validate_registry({"devices": [candidate]})

    def test_unknown_and_secret_connection_fields_are_rejected(self):
        for forbidden in (
            "key_path",
            "known_hosts",
            "password",
            "token",
            "private_key",
            "unexpected",
        ):
            candidate = self.default_record()
            candidate["connection"][forbidden] = "do-not-store"
            with self.subTest(field=forbidden):
                with self.assertRaises(RegistryValidationError):
                    self.registry.validate_registry({"devices": [candidate]})

    def test_connection_schema_is_type_specific(self):
        esp32 = {
            "id": "kitchen-panel",
            "name": "Kitchen Panel",
            "type": "esp32_epaper",
            "resolution": [800, 480],
            "enabled": True,
            "config_path": "devices/kitchen-panel/config.json",
            "image_path": "devices/kitchen-panel/image.png",
            "connection": {
                "method": "http",
                "host": "192.168.68.150",
                "port": 80,
            },
        }
        records = self.registry.validate_registry({"devices": [esp32]})
        self.assertEqual(records[0].connection["method"], "http")

        esp32["connection"]["ssh_profile"] = "kindle_dashboard"
        with self.assertRaises(RegistryValidationError):
            self.registry.validate_registry({"devices": [esp32]})

    def test_public_records_contain_only_non_secret_metadata(self):
        public = self.registry.public_records()[0]
        serialized = json.dumps(public)

        self.assertEqual(public["id"], "default-kindle")
        self.assertEqual(
            set(public["connection"]),
            {"host", "user", "ssh_profile", "port"},
        )
        self.assertNotIn("key_path", serialized)
        self.assertNotIn("known_hosts", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("token", serialized)

    def test_kindle_overlay_flag_is_validated_and_public(self):
        candidate = self.default_record()
        candidate["use_screensaver_overlay"] = True

        records = self.registry.validate_registry({"devices": [candidate]})

        self.assertTrue(records[0].use_screensaver_overlay)
        self.registry.write_registry({"devices": [candidate]})
        self.assertTrue(
            self.registry.public_records()[0]["use_screensaver_overlay"]
        )

        candidate["use_screensaver_overlay"] = "yes"
        with self.assertRaisesRegex(
            RegistryValidationError,
            "screensaver overlay",
        ):
            self.registry.validate_registry({"devices": [candidate]})

    def test_unknown_or_disabled_device_is_not_servable(self):
        self.registry.get("default-kindle")
        with self.assertRaises(DeviceNotFoundError):
            self.registry.get("missing", require_enabled=True)

        registry_data = self.read_registry()
        registry_data["devices"][0]["enabled"] = False
        self.registry.write_registry(registry_data)
        with self.assertRaises(DeviceNotFoundError):
            self.registry.get("default-kindle", require_enabled=True)
        self.assertFalse(
            self.registry.get("default-kindle").enabled
        )

    def test_add_and_update_use_atomic_validated_registry_writes(self):
        self.registry.get("default-kindle")
        kitchen = {
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.120",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            },
        }

        added = self.registry.add(kitchen)
        self.assertEqual(added.id, "kitchen-kindle")
        updated = dict(kitchen, name="Kitchen Display", enabled=False)
        changed = self.registry.update("kitchen-kindle", updated)

        self.assertEqual(changed.name, "Kitchen Display")
        self.assertFalse(changed.enabled)
        self.assertEqual(len(self.read_registry()["devices"]), 2)
        self.assertEqual(
            list(self.root.glob(".devices.json.*.tmp")),
            [],
        )

    def default_record(self):
        self.registry.get("default-kindle")
        return dict(self.read_registry()["devices"][0])

    def read_registry_after_default(self):
        self.registry.get("default-kindle")
        return self.read_registry()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import weather_image
from device_registry import (
    DeviceNotFoundError,
    DeviceRegistry,
)


class DeviceRendererTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.legacy_config = dict(weather_image.DEFAULT_CONFIG)
        self.legacy_config["title"] = "LEGACY DEFAULT"
        (self.root / "dashboard_config.json").write_text(
            json.dumps(self.legacy_config),
            encoding="utf-8",
        )
        self.registry = DeviceRegistry(self.root)
        self.default_device = self.registry.get("default-kindle")
        self.rendered_titles = []

    def tearDown(self):
        self.tempdir.cleanup()

    def fake_renderer(self, config):
        self.rendered_titles.append(config["title"])
        image = Image.new(
            "L",
            (weather_image.W, weather_image.H),
            255,
        )
        weather_image.save_dashboard(
            image,
            {
                "ph": {"queries": 0},
                "ts": {"online": 0, "total": 0},
            },
        )

    def test_legacy_generate_command_keeps_global_config_and_output(self):
        legacy_output = self.root / "kindle_weather.png"
        with (
            mock.patch.object(
                weather_image,
                "load_config",
                return_value=self.legacy_config,
            ),
            mock.patch.object(weather_image, "OUT", legacy_output),
            mock.patch.dict(
                weather_image.THEME_RENDERERS,
                {"home_dashboard": self.fake_renderer},
                clear=True,
            ),
        ):
            weather_image.generate_dashboard()

        self.assertEqual(self.rendered_titles, ["LEGACY DEFAULT"])
        self.assertTrue(legacy_output.exists())
        with Image.open(legacy_output) as generated:
            self.assertEqual(generated.size, (758, 1024))

    def test_render_default_device_writes_device_and_legacy_images(self):
        device_config = dict(self.legacy_config)
        device_config["title"] = "DEVICE DEFAULT"
        self.default_device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": self.fake_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        legacy_output = self.root / "kindle_weather.png"
        self.assertEqual(self.rendered_titles, ["DEVICE DEFAULT"])
        self.assertEqual(
            result["output_path"],
            self.default_device.image_path,
        )
        self.assertEqual(result["resolution"], [758, 1024])
        self.assertEqual(result["theme"], "home_dashboard")
        self.assertTrue(self.default_device.image_path.exists())
        self.assertTrue(legacy_output.exists())
        self.assertEqual(
            self.default_device.image_path.read_bytes(),
            legacy_output.read_bytes(),
        )
        with Image.open(self.default_device.image_path) as generated:
            self.assertEqual(generated.size, (758, 1024))

    def test_default_device_inherits_global_theme_when_device_theme_missing(self):
        self.legacy_config["theme"] = "maarif_calendar"
        (self.root / "dashboard_config.json").write_text(
            json.dumps(self.legacy_config),
            encoding="utf-8",
        )
        device_config = dict(self.legacy_config)
        device_config.pop("theme")
        device_config["title"] = "DEVICE DEFAULT"
        self.default_device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )

        rendered_themes = []

        def fake_maarif_renderer(config):
            rendered_themes.append(config["theme"])
            self.fake_renderer(config)

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"maarif_calendar": fake_maarif_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(rendered_themes, ["maarif_calendar"])
        self.assertEqual(result["theme"], "maarif_calendar")

    def test_default_device_explicit_theme_overrides_global_theme(self):
        self.legacy_config["theme"] = "maarif_calendar"
        (self.root / "dashboard_config.json").write_text(
            json.dumps(self.legacy_config),
            encoding="utf-8",
        )
        device_config = dict(self.legacy_config)
        device_config["theme"] = "compact_dashboard"
        device_config["title"] = "DEVICE DEFAULT"
        self.default_device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )

        rendered_themes = []

        def fake_compact_renderer(config):
            rendered_themes.append(config["theme"])
            self.fake_renderer(config)

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"compact_dashboard": fake_compact_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(rendered_themes, ["compact_dashboard"])
        self.assertEqual(result["theme"], "compact_dashboard")

    def test_legacy_theme_alias_is_normalized_for_device_rendering(self):
        device_config = dict(self.legacy_config)
        device_config.pop("theme")
        device_config["dashboard_mode"] = "maarif_calendar"
        self.default_device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )

        rendered_themes = []

        def fake_maarif_renderer(config):
            rendered_themes.append(config["theme"])
            self.fake_renderer(config)

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"maarif_calendar": fake_maarif_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(rendered_themes, ["maarif_calendar"])
        self.assertEqual(result["theme"], "maarif_calendar")

    def test_render_named_device_uses_isolated_config_and_output(self):
        kitchen = self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
        })
        kitchen_config = dict(weather_image.DEFAULT_CONFIG)
        kitchen_config.update({
            "title": "KITCHEN",
            "theme": "minimal_weather",
        })
        kitchen.config_path.write_text(
            json.dumps(kitchen_config),
            encoding="utf-8",
        )
        legacy_before = (self.root / "dashboard_config.json").read_bytes()
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"minimal_weather": self.fake_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "kitchen-kindle",
                registry=self.registry,
            )

        self.assertEqual(self.rendered_titles, ["KITCHEN"])
        self.assertEqual(result["output_path"], kitchen.image_path)
        self.assertTrue(kitchen.image_path.exists())
        self.assertEqual(
            (self.root / "dashboard_config.json").read_bytes(),
            legacy_before,
        )

    def test_default_missing_device_config_falls_back_to_legacy(self):
        self.default_device.config_path.unlink()
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": self.fake_renderer},
            clear=True,
        ):
            weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )
        self.assertEqual(self.rendered_titles, ["LEGACY DEFAULT"])

    def test_cli_routes_legacy_and_device_modes(self):
        with (
            mock.patch.object(
                weather_image,
                "generate_dashboard_safe",
            ) as legacy,
            mock.patch.object(
                weather_image,
                "render_device",
            ) as device,
        ):
            self.assertEqual(weather_image.main([]), 0)
            legacy.assert_called_once_with()
            device.assert_not_called()

            self.assertEqual(
                weather_image.main(
                    ["--device", "default-kindle"],
                    registry=self.registry,
                ),
                0,
            )
            device.assert_called_once_with(
                "default-kindle",
                registry=self.registry,
            )

    def test_invalid_device_is_a_clean_error(self):
        with self.assertRaises(DeviceNotFoundError):
            weather_image.render_device(
                "../dashboard_config",
                registry=self.registry,
            )
        with mock.patch.object(
            weather_image,
            "render_device",
            side_effect=DeviceNotFoundError("missing"),
        ):
            self.assertNotEqual(
                weather_image.main(
                    ["--device", "missing"],
                    registry=self.registry,
                ),
                0,
            )

    def test_fixed_layout_rejects_unsupported_resolution(self):
        panel = self.registry.add({
            "id": "kitchen-panel",
            "name": "Kitchen Panel",
            "type": "generic_png",
            "resolution": [800, 480],
            "enabled": True,
            "config_path": "devices/kitchen-panel/config.json",
            "image_path": "devices/kitchen-panel/image.png",
        })
        with self.assertRaisesRegex(
            ValueError,
            "758x1024",
        ):
            weather_image.render_device(
                panel.id,
                registry=self.registry,
            )


if __name__ == "__main__":
    unittest.main()

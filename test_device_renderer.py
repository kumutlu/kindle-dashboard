#!/usr/bin/env python3
import base64
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import special_events
import weather_image
from device_registry import (
    DeviceNotFoundError,
    DeviceRegistry,
)
from providers.local_task_provider import LocalTaskProvider


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

    def test_atomic_rendered_image_has_world_readable_permissions(self):
        output_path = self.root / "rendered.png"

        weather_image._save_rendered_theme_image(
            Image.new("1", (600, 800), 1),
            output_path,
        )

        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o644)

    def create_test_event(self, start_date, end_date):
        return special_events.create_event(
            self.root,
            {
                "title": "Celebration",
                "start_date": start_date,
                "end_date": end_date,
                "image_data": (
                    "data:image/png;base64,"
                    + base64.b64encode(
                        (
                            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00"
                            b"\x3a\x7e\x9b\x55\x00\x00\x00\x0bIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01"
                            b"\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                        )
                    ).decode("ascii")
                ),
                "devices": ["default-kindle"],
                "enabled": True,
            },
            ["default-kindle"],
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

    def test_deprecated_device_theme_is_normalized_for_rendering(self):
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

        def fake_home_renderer(config):
            rendered_themes.append(config["theme"])
            self.fake_renderer(config)

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": fake_home_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(rendered_themes, ["home_dashboard"])
        self.assertEqual(result["theme"], "home_dashboard")

    def test_unknown_persisted_device_theme_falls_back_to_base_theme(self):
        self.legacy_config["theme"] = "family_dashboard"
        (self.root / "dashboard_config.json").write_text(
            json.dumps(self.legacy_config),
            encoding="utf-8",
        )
        device_config = dict(self.legacy_config)
        device_config.update({
            "title": "DEVICE TITLE",
            "theme": "unknown-theme",
        })
        self.default_device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )

        loaded = weather_image.load_effective_device_config(
            self.default_device,
            self.registry,
        )

        self.assertEqual(loaded["theme"], "family_dashboard")
        self.assertEqual(loaded["title"], "DEVICE TITLE")

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

    def test_active_special_event_overrides_normal_rendering(self):
        event = self.create_test_event("2000-01-01", "2100-01-01")
        special_events.save_events(self.root, [event])
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": self.fake_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(self.rendered_titles, [])
        self.assertEqual(result["output_path"], self.default_device.image_path)
        self.assertTrue(self.default_device.image_path.exists())

    def test_expired_special_event_falls_back_to_normal_rendering(self):
        event = self.create_test_event("2026-07-08", "2026-07-08")
        special_events.save_events(self.root, [event])
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": self.fake_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )

        self.assertEqual(self.rendered_titles, ["LEGACY DEFAULT"])
        self.assertEqual(result["theme"], "home_dashboard")

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

    def test_minimal_weather_renders_600x800_kindle_device(self):
        kt4 = self.registry.add({
            "id": "kindle-131",
            "name": "Kindle 131",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-131/config.json",
            "image_path": "devices/kindle-131/image.png",
            "connection": {
                "host": "192.168.68.131",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            },
            "use_screensaver_overlay": True,
        })
        config = dict(weather_image.DEFAULT_CONFIG)
        config.update({
            "title": "KINDLE 131",
            "theme": "minimal_weather",
        })
        kt4.config_path.write_text(
            json.dumps(config),
            encoding="utf-8",
        )

        with mock.patch.object(weather_image, "collect_dashboard_data", return_value={
            "now": mock.Mock(
                strftime=lambda fmt: {
                    "%A": "Friday",
                    "%d %B %Y": "10 July 2026",
                    "%H:%M": "12:00",
                }.get(fmt, "Friday"),
            ),
            "current": {"weatherCode": "113"},
            "temp": 20,
            "desc": "Clear",
            "weather_desc_localized": "Clear",
            "feels": 18,
            "hi": 24,
            "lo": 12,
            "humidity": 45,
            "wind": 9,
            "wind_dir": "W",
            "pressure": 1012,
            "sunrise": "04:52",
            "sunset": "21:28",
            "days": [
                {
                    "date": "2026-07-10",
                    "maxtempC": 24,
                    "mintempC": 12,
                    "hourly": [{"weatherCode": "113", "chanceofrain": 10}],
                }
            ] * 3,
            "ph": {"queries": 0, "blocked": 0, "clients": 0},
            "ts": {"online": 0, "total": 0},
        }):
            result = weather_image.render_device(
                kt4.id,
                registry=self.registry,
            )

        self.assertEqual(result["resolution"], [600, 800])
        self.assertEqual(result["theme"], "minimal_weather")
        with Image.open(kt4.image_path) as generated:
            self.assertEqual(generated.size, (600, 800))

    def test_family_dashboard_renders_600x800_kindle_device(self):
        kt4 = self.registry.add({
            "id": "kindle-131",
            "name": "Kindle 131",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-131/config.json",
            "image_path": "devices/kindle-131/image.png",
            "connection": {
                "host": "192.168.68.131",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            },
        })
        config = dict(weather_image.DEFAULT_CONFIG)
        config["theme"] = "family_dashboard"
        kt4.config_path.write_text(json.dumps(config), encoding="utf-8")

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"family_dashboard": self.fake_renderer},
            clear=True,
        ):
            result = weather_image.render_device(
                kt4.id,
                registry=self.registry,
            )

        self.assertEqual(result["resolution"], [600, 800])
        self.assertEqual(result["theme"], "family_dashboard")
        with Image.open(kt4.image_path) as generated:
            self.assertEqual(generated.size, (600, 800))

    def test_all_canonical_weather_themes_render_600x800_kindle_device(self):
        kt4 = self.registry.add({
            "id": "kindle-131",
            "name": "Kindle 131",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-131/config.json",
            "image_path": "devices/kindle-131/image.png",
        })
        weather_themes = {
            "home_dashboard",
            "server_monitor",
            "maarif_calendar",
        }

        for theme in weather_themes:
            with self.subTest(theme=theme):
                config = dict(weather_image.DEFAULT_CONFIG, theme=theme)
                kt4.config_path.write_text(
                    json.dumps(config),
                    encoding="utf-8",
                )
                with mock.patch.dict(
                    weather_image.THEME_RENDERERS,
                    {theme: self.fake_renderer},
                    clear=True,
                ):
                    result = weather_image.render_device(
                        kt4.id,
                        registry=self.registry,
                    )

                self.assertEqual(result["theme"], theme)
                with Image.open(kt4.image_path) as generated:
                    self.assertEqual(generated.size, (600, 800))

    def test_todo_theme_renders_device_tasks_without_weather_fetches(self):
        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {"home_dashboard": self.fake_renderer},
            clear=True,
        ):
            weather_image.render_device(
                "default-kindle",
                registry=self.registry,
            )
        with Image.open(self.default_device.image_path) as home_image:
            home_properties = (home_image.mode, home_image.size)

        kitchen = self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
        })
        config = dict(weather_image.DEFAULT_CONFIG)
        config["theme"] = "todo"
        kitchen.config_path.write_text(json.dumps(config), encoding="utf-8")
        provider = LocalTaskProvider(self.root)
        provider.create_task(kitchen.id, "Buy milk")
        provider.create_task(kitchen.id, "Call GP")

        with mock.patch.object(weather_image, "collect_dashboard_data") as weather:
            result = weather_image.render_device(kitchen.id, registry=self.registry)

        weather.assert_not_called()
        self.assertEqual(result["theme"], "todo")
        with Image.open(kitchen.image_path) as generated:
            self.assertEqual((generated.mode, generated.size), home_properties)
            self.assertEqual(generated.mode, "L")

    def test_todo_theme_renders_600x800_without_weather_theme_restriction(self):
        kt4 = self.registry.add({
            "id": "kindle-todo",
            "name": "Todo Kindle",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-todo/config.json",
            "image_path": "devices/kindle-todo/image.png",
        })
        config = dict(weather_image.DEFAULT_CONFIG)
        config["theme"] = "todo"
        kt4.config_path.write_text(json.dumps(config), encoding="utf-8")

        result = weather_image.render_device(kt4.id, registry=self.registry)

        self.assertEqual(result["resolution"], [600, 800])
        with Image.open(kt4.image_path) as generated:
            self.assertEqual(generated.size, (600, 800))
            self.assertEqual(generated.mode, "L")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import unittest

import dashboard_themes
import weather_image


ALL_VISIBLE = {
    "show_weather": True,
    "show_forecast": True,
    "show_server": True,
    "show_pihole": True,
    "show_tailscale": True,
}


class ThemeRegistryTests(unittest.TestCase):
    def test_registry_contains_four_implemented_themes_and_placeholder(self):
        self.assertEqual(
            list(dashboard_themes.THEMES),
            [
                "home_dashboard",
                "minimal_weather",
                "server_monitor",
                "travel_weather",
                "maarif_calendar",
            ],
        )
        self.assertFalse(
            dashboard_themes.THEMES["maarif_calendar"]["implemented"]
        )

    def test_home_dashboard_preserves_visibility_flags(self):
        flags = dict(ALL_VISIBLE)
        flags["show_forecast"] = False
        self.assertEqual(
            dashboard_themes.effective_visibility("home_dashboard", flags),
            flags,
        )

    def test_minimal_and_travel_force_weather_only(self):
        expected = {
            "show_weather": True,
            "show_forecast": True,
            "show_server": False,
            "show_pihole": False,
            "show_tailscale": False,
        }
        for theme in ("minimal_weather", "travel_weather"):
            with self.subTest(theme=theme):
                self.assertEqual(
                    dashboard_themes.effective_visibility(theme, ALL_VISIBLE),
                    expected,
                )

    def test_server_monitor_forces_all_server_cards(self):
        self.assertEqual(
            dashboard_themes.effective_visibility(
                "server_monitor",
                {key: False for key in ALL_VISIBLE},
            ),
            {
                "show_weather": False,
                "show_forecast": False,
                "show_server": True,
                "show_pihole": True,
                "show_tailscale": True,
            },
        )

    def test_placeholder_and_unknown_themes_are_rejected(self):
        for theme in ("maarif_calendar", "unknown"):
            with self.subTest(theme=theme):
                with self.assertRaises(ValueError):
                    dashboard_themes.validate_theme(theme)

    def test_dashboard_config_accepts_only_implemented_themes(self):
        for theme in (
            "home_dashboard",
            "minimal_weather",
            "server_monitor",
            "travel_weather",
        ):
            with self.subTest(theme=theme):
                config = dict(weather_image.DEFAULT_CONFIG)
                config["theme"] = theme
                self.assertEqual(
                    weather_image.validate_config(config)["theme"],
                    theme,
                )
        config = dict(weather_image.DEFAULT_CONFIG)
        config["theme"] = "maarif_calendar"
        with self.assertRaises(ValueError):
            weather_image.validate_config(config)

    def test_every_implemented_theme_has_a_renderer(self):
        self.assertEqual(
            set(weather_image.THEME_RENDERERS),
            {
                "home_dashboard",
                "minimal_weather",
                "server_monitor",
                "travel_weather",
            },
        )

    def test_theme_layout_policies_override_stored_flags(self):
        minimal = dict(weather_image.DEFAULT_CONFIG, theme="minimal_weather")
        minimal_layout = weather_image.build_layout(minimal)
        self.assertIsNotNone(minimal_layout["weather_top"])
        self.assertIsNotNone(minimal_layout["forecast_heading"])
        self.assertIsNone(minimal_layout["server_heading"])

        server = dict(
            weather_image.DEFAULT_CONFIG,
            theme="server_monitor",
            show_pihole=False,
            show_tailscale=False,
        )
        server_layout = weather_image.build_layout(server)
        self.assertIsNone(server_layout["weather_top"])
        self.assertIsNone(server_layout["forecast_heading"])
        self.assertEqual(
            server_layout["server_card_names"],
            ["CPU", "RAM", "DISK", "PI-HOLE", "QUERIES", "TAILSCALE"],
        )


if __name__ == "__main__":
    unittest.main()

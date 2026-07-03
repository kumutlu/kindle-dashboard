#!/usr/bin/env python3
import unittest

import weather_image


def config(**overrides):
    value = dict(weather_image.DEFAULT_CONFIG)
    value.update(overrides)
    return value


class VisibilityLayoutTests(unittest.TestCase):
    def test_all_enabled_preserves_original_coordinates_and_cards(self):
        layout = weather_image.build_layout(config())
        self.assertEqual(layout["weather_top"], 140)
        self.assertEqual(layout["forecast_heading"], 415)
        self.assertEqual(layout["forecast_cards"], 455)
        self.assertEqual(layout["server_heading"], 668)
        self.assertEqual(layout["server_cards"], 708)
        self.assertEqual(
            layout["server_card_names"],
            ["CPU", "RAM", "DISK", "PI-HOLE", "QUERIES", "TAILSCALE"],
        )

    def test_forecast_disabled_moves_server_up(self):
        layout = weather_image.build_layout(config(show_forecast=False))
        self.assertIsNone(layout["forecast_heading"])
        self.assertLess(layout["server_heading"], 668)

    def test_server_disabled_removes_server_section(self):
        layout = weather_image.build_layout(config(show_server=False))
        self.assertIsNone(layout["server_heading"])
        self.assertEqual(layout["server_card_names"], [])

    def test_pihole_disabled_hides_only_pihole_cards(self):
        layout = weather_image.build_layout(config(show_pihole=False))
        self.assertEqual(
            layout["server_card_names"],
            ["CPU", "RAM", "DISK", "TAILSCALE"],
        )

    def test_tailscale_disabled_hides_only_tailscale_card(self):
        layout = weather_image.build_layout(config(show_tailscale=False))
        self.assertEqual(
            layout["server_card_names"],
            ["CPU", "RAM", "DISK", "PI-HOLE", "QUERIES"],
        )

    def test_pihole_and_tailscale_disabled_keeps_system_cards(self):
        layout = weather_image.build_layout(
            config(show_pihole=False, show_tailscale=False)
        )
        self.assertEqual(layout["server_card_names"], ["CPU", "RAM", "DISK"])

    def test_weather_only(self):
        layout = weather_image.build_layout(
            config(show_forecast=False, show_server=False)
        )
        self.assertEqual(layout["weather_top"], 140)
        self.assertIsNone(layout["forecast_heading"])
        self.assertIsNone(layout["server_heading"])

    def test_server_only_starts_near_top(self):
        layout = weather_image.build_layout(
            config(show_weather=False, show_forecast=False)
        )
        self.assertIsNone(layout["weather_top"])
        self.assertIsNone(layout["forecast_heading"])
        self.assertEqual(layout["server_heading"], 148)
        self.assertEqual(layout["server_cards"], 188)

    def test_missing_or_invalid_config_defaults_everything_visible(self):
        self.assertTrue(all(
            weather_image.DEFAULT_CONFIG[key]
            for key in weather_image.BOOLEAN_FIELDS
        ))


if __name__ == "__main__":
    unittest.main()

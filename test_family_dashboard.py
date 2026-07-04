#!/usr/bin/env python3
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import dashboard_themes
import weather_image
import settings_server


class FamilyDashboardTests(unittest.TestCase):
    def setUp(self):
        # Set up a clean environment for testing notes filtering
        self.sample_notes = {
            "items": [
                {
                    "id": "1",
                    "enabled": True,
                    "category": "BIN",
                    "title": "General waste",
                    "priority": "high",
                    "date": None,
                    "recurrence": {"type": "weekly", "days": ["MON", "WED"]},
                },
                {
                    "id": "2",
                    "enabled": True,
                    "category": "SCHOOL",
                    "title": "Ali PE Kit",
                    "priority": "normal",
                    "date": "2026-07-04",
                    "recurrence": None,
                },
                {
                    "id": "3",
                    "enabled": False,
                    "category": "TODO",
                    "title": "Disabled task",
                    "priority": "low",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "4",
                    "enabled": True,
                    "category": "APPT",
                    "title": "Dentist",
                    "priority": "normal",
                    "date": "2026-07-05",
                    "recurrence": None,
                },
                {
                    "id": "5",
                    "enabled": True,
                    "category": "NOTE",
                    "title": "Always active reminder",
                    "priority": "low",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "6",
                    "enabled": True,
                    "category": "HOME",
                    "title": "Clean kitchen",
                    "priority": "high",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "7",
                    "enabled": True,
                    "category": "NOTE",
                    "title": "Expired reminder",
                    "priority": "normal",
                    "date": None,
                    "recurrence": None,
                    "expires_after_date": "2026-07-03",
                }
            ]
        }

    def test_theme_registration_and_visibility(self):
        self.assertIn("family_dashboard", dashboard_themes.THEMES)
        visibility = dashboard_themes.effective_visibility(
            "family_dashboard",
            {
                "show_weather": True,
                "show_forecast": True,
                "show_server": True,
                "show_pihole": True,
                "show_tailscale": True,
            }
        )
        self.assertTrue(visibility["show_weather"])
        self.assertTrue(visibility["show_forecast"])
        self.assertFalse(visibility["show_server"])
        self.assertFalse(visibility["show_pihole"])
        self.assertFalse(visibility["show_tailscale"])

    def test_active_reminders_filtering_saturday(self):
        # 2026-07-04 is a Saturday (SAT)
        active = weather_image.get_active_reminders(self.sample_notes, "2026-07-04")
        
        # Expected active IDs:
        # - ID 2 (one-off date is today: 2026-07-04)
        # - ID 5 (always active: no date or recurrence)
        # - ID 6 (always active: no date or recurrence)
        # ID 1 is Mon/Wed, ID 3 is disabled, ID 4 is July 5, ID 7 is expired
        active_ids = [item["id"] for item in active]
        self.assertIn("2", active_ids)
        self.assertIn("5", active_ids)
        self.assertIn("6", active_ids)
        self.assertNotIn("1", active_ids)
        self.assertNotIn("3", active_ids)
        self.assertNotIn("4", active_ids)
        self.assertNotIn("7", active_ids)

        # Priority/Category sorting test:
        # High priority items first: ID 6 (high, HOME), then ID 2 (normal, SCHOOL), then ID 5 (low, NOTE).
        self.assertEqual(active[0]["id"], "6")
        self.assertEqual(active[1]["id"], "2")
        self.assertEqual(active[2]["id"], "5")

    def test_active_reminders_filtering_monday(self):
        # 2026-07-06 is a Monday (MON)
        active = weather_image.get_active_reminders(self.sample_notes, "2026-07-06")
        active_ids = [item["id"] for item in active]
        # Expected active IDs:
        # - ID 1 (recurring MON/WED)
        # - ID 5 (always active)
        # - ID 6 (always active)
        self.assertIn("1", active_ids)
        self.assertIn("5", active_ids)
        self.assertIn("6", active_ids)
        self.assertNotIn("2", active_ids)

    def test_notes_crud_endpoints(self):
        # Verify note load/save functions
        settings_server.DAILY_NOTES_PATH = Path("/tmp/test_daily_notes.json")
        if settings_server.DAILY_NOTES_PATH.exists():
            settings_server.DAILY_NOTES_PATH.unlink()

        try:
            # Check empty file fallback
            notes = settings_server.load_daily_notes()
            self.assertEqual(notes, {"items": []})

            # Check save
            settings_server.save_daily_notes(self.sample_notes)
            loaded = settings_server.load_daily_notes()
            self.assertEqual(len(loaded["items"]), 7)
        finally:
            if settings_server.DAILY_NOTES_PATH.exists():
                settings_server.DAILY_NOTES_PATH.unlink()

    @patch("weather_image.collect_dashboard_data")
    def test_rendering(self, mock_collect):
        # Mock weather data to avoid external api calls
        mock_collect.return_value = {
            "now": datetime(2026, 7, 4, 12, 0),
            "temp": 22,
            "feels": 22,
            "desc": "Sunny",
            "humidity": 50,
            "wind": 5,
            "wind_dir": "N",
            "pressure": 1015,
            "hi": 24,
            "lo": 14,
            "sunrise": "04:52",
            "sunset": "21:30",
            "cpu": 5,
            "ram": 10,
            "disk": 20,
            "ph": {"blocked": 0, "queries": 0},
            "ts": {"online": 0, "total": 0},
            "day_name_localized": "Saturday",
            "weather_desc_localized": "Sunny",
            "current": {"weatherCode": 1000},
            "days": [
                {
                    "date": "2026-07-04",
                    "maxtempC": 24,
                    "mintempC": 14,
                    "hourly": [{"chanceofrain": 0}] * 8
                }
            ]
        }

        # Render family_dashboard using default config
        config = dict(weather_image.DEFAULT_CONFIG, theme="family_dashboard")
        test_out = Path(__file__).resolve().parent / "test_family_out.png"
        
        with patch("weather_image.OUT", test_out):
            if test_out.exists():
                test_out.unlink()
            try:
                weather_image.render_family_dashboard(config)
                self.assertTrue(test_out.exists())
                # Verify PNG dimensions
                from PIL import Image
                with Image.open(test_out) as img:
                    self.assertEqual(img.size, (758, 1024))
            finally:
                if test_out.exists():
                    test_out.unlink()

    @patch("weather_image.collect_dashboard_data")
    def test_rendering_worst_case_layout(self, mock_collect):
        # worst-case weather metrics values for layout overflow validation
        mock_collect.return_value = {
            "now": datetime(2026, 7, 4, 12, 0),
            "temp": 29,
            "feels": 32,
            "desc": "Heavy Rain",
            "humidity": 100,
            "wind": 18,
            "wind_dir": "WSW",
            "pressure": 1029,
            "hi": 29,
            "lo": 18,
            "sunrise": "04:31",
            "sunset": "21:59",
            "cpu": 99,
            "ram": 99,
            "disk": 99,
            "ph": {"blocked": 99999, "queries": 999999},
            "ts": {"online": 99, "total": 99},
            "day_name_localized": "Saturday",
            "weather_desc_localized": "Heavy Rain",
            "current": {"weatherCode": 1243},
            "days": [
                {
                    "date": "2026-07-04",
                    "maxtempC": 29,
                    "mintempC": 18,
                    "hourly": [{"chanceofrain": 90}] * 8
                }
            ]
        }

        config = dict(weather_image.DEFAULT_CONFIG, theme="family_dashboard")
        test_out = Path(__file__).resolve().parent / "test_family_worst_case.png"
        
        with patch("weather_image.OUT", test_out):
            if test_out.exists():
                test_out.unlink()
            try:
                weather_image.render_family_dashboard(config)
                self.assertTrue(test_out.exists())
                from PIL import Image
                with Image.open(test_out) as img:
                    self.assertEqual(img.size, (758, 1024))
            finally:
                if test_out.exists():
                    test_out.unlink()


if __name__ == "__main__":
    unittest.main()

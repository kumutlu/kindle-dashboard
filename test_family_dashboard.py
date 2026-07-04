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

    def test_start_date_and_extended_recurrence(self):
        # 1. start_date hides reminder before start date
        # 2. start_date allows reminder on start date
        # 3. start_date allows reminder after start date
        notes = {
            "items": [
                {
                    "id": "start-future",
                    "enabled": True,
                    "title": "Future task",
                    "start_date": "2026-07-10",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "start-today",
                    "enabled": True,
                    "title": "Today task",
                    "start_date": "2026-07-04",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "start-past",
                    "enabled": True,
                    "title": "Past task",
                    "start_date": "2026-07-01",
                    "date": None,
                    "recurrence": None,
                },
                {
                    "id": "weekly-future",
                    "enabled": True,
                    "title": "Weekly future",
                    "start_date": "2026-07-10",
                    "date": None,
                    "recurrence": {"type": "weekly", "days": ["SAT"]},
                },
                {
                    "id": "weekly-active",
                    "enabled": True,
                    "title": "Weekly active",
                    "start_date": "2026-07-01",
                    "date": None,
                    "recurrence": {"type": "weekly", "days": ["SAT"]},
                },
                {
                    "id": "fortnightly-ok",
                    "enabled": True,
                    "title": "Fortnightly",
                    "date": None,
                    "recurrence": {
                        "type": "fortnightly",
                        "days": ["MON"],
                        "anchor_date": "2026-07-06"
                    }
                },
                {
                    "id": "fortnightly-start",
                    "enabled": True,
                    "title": "Fortnightly start restricted",
                    "start_date": "2026-07-15",
                    "date": None,
                    "recurrence": {
                        "type": "fortnightly",
                        "days": ["MON"],
                        "anchor_date": "2026-07-06"
                    }
                },
                {
                    "id": "monthly-regular",
                    "enabled": True,
                    "title": "Monthly day 5",
                    "date": None,
                    "recurrence": {
                        "type": "monthly",
                        "day_of_month": 5
                    }
                },
                {
                    "id": "monthly-overflow",
                    "enabled": True,
                    "title": "Monthly day 31",
                    "date": None,
                    "recurrence": {
                        "type": "monthly",
                        "day_of_month": 31
                    }
                },
                {
                    "id": "expired-reminder",
                    "enabled": True,
                    "title": "Expired",
                    "date": None,
                    "recurrence": None,
                    "expires_after_date": "2026-07-03"
                },
                {
                    "id": "malformed-start",
                    "enabled": True,
                    "title": "Malformed start",
                    "start_date": "invalid-date-string",
                    "date": None,
                    "recurrence": None,
                }
            ]
        }

        # SAT July 4, 2026
        active_04 = weather_image.get_active_reminders(notes, "2026-07-04")
        ids_04 = [item["id"] for item in active_04]
        self.assertIn("start-today", ids_04)
        self.assertIn("start-past", ids_04)
        self.assertIn("weekly-active", ids_04)
        self.assertIn("malformed-start", ids_04)
        self.assertNotIn("start-future", ids_04)
        self.assertNotIn("weekly-future", ids_04)
        self.assertNotIn("expired-reminder", ids_04)

        # MON July 6, 2026 (Fortnightly anchor, Monday)
        active_06 = weather_image.get_active_reminders(notes, "2026-07-06")
        ids_06 = [item["id"] for item in active_06]
        self.assertIn("fortnightly-ok", ids_06)
        self.assertNotIn("fortnightly-start", ids_06)

        # MON July 13, 2026 (Fortnightly off-week Monday)
        active_13 = weather_image.get_active_reminders(notes, "2026-07-13")
        ids_13 = [item["id"] for item in active_13]
        self.assertNotIn("fortnightly-ok", ids_13)

        # MON July 20, 2026 (Fortnightly on-week Monday after start date)
        active_20 = weather_image.get_active_reminders(notes, "2026-07-20")
        ids_20 = [item["id"] for item in active_20]
        self.assertIn("fortnightly-ok", ids_20)
        self.assertIn("fortnightly-start", ids_20)

        # MON July 5, 2026 (Monthly test day 5)
        active_05 = weather_image.get_active_reminders(notes, "2026-07-05")
        ids_05 = [item["id"] for item in active_05]
        self.assertIn("monthly-regular", ids_05)

        # THU April 30, 2026 (Monthly test day 31 overflow to last day of April)
        active_apr30 = weather_image.get_active_reminders(notes, "2026-04-30")
        ids_apr30 = [item["id"] for item in active_apr30]
        self.assertIn("monthly-overflow", ids_apr30)

        # FRI May 1, 2026 (Should NOT match monthly day 31 overflow)
        active_may01 = weather_image.get_active_reminders(notes, "2026-05-01")
        ids_may01 = [item["id"] for item in active_may01]
        self.assertNotIn("monthly-overflow", ids_may01)

    def test_settings_server_html_contains_recurrence_options(self):
        html_content = settings_server.render_settings(weather_image.DEFAULT_CONFIG, "test-csrf-token")
        self.assertIn("Start Date", html_content)
        self.assertIn("Fortnightly Repeat", html_content)
        self.assertIn("Monthly Repeat", html_content)


if __name__ == "__main__":
    unittest.main()

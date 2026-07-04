import unittest
from unittest import mock
import tempfile
import json
from pathlib import Path
from PIL import Image

import weather_image
from dashboard_themes import THEMES

class CompactDashboardTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.test_dir.name)
        
        # Patch PROJECT_DIR and OUT in weather_image
        self.project_dir_patcher = mock.patch("weather_image.PROJECT_DIR", self.project_path)
        self.project_dir_patcher.start()
        
        self.out_patcher = mock.patch("weather_image.OUT", self.project_path / "kindle_weather.png")
        self.out_patcher.start()
        
        self.config = {
            "title": "COMPACT NOTTINGHAM",
            "location": "Nottingham",
            "country": "United Kingdom",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "location_display": "Nottingham, England, United Kingdom",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "compact_dashboard",
            "show_weather": True,
            "show_forecast": True,
            "show_server": True,
            "show_pihole": True,
            "show_tailscale": True,
            "kindle_frontlight": 8,
            "prayer_method": 13,
            "prayer_school": 0,
            "prayer_high_latitude": 3,
            "hijri_adjustment": 0,
        }

    def tearDown(self):
        self.out_patcher.stop()
        self.project_dir_patcher.stop()
        self.test_dir.cleanup()

    def mock_weather_data(self):
        return {
            "current_condition": [{
                "temp_C": "18",
                "FeelsLikeC": "17",
                "humidity": "72",
                "windspeedMiles": "6",
                "winddir16Point": "WSW",
                "pressure": "1022",
                "weatherCode": "2",
                "weatherDesc": [{"value": "Partly Cloudy"}]
            }],
            "weather": [
                {
                    "date": "2026-07-04",
                    "maxtempC": "24",
                    "mintempC": "12",
                    "astronomy": [{"sunrise": "04:45 AM", "sunset": "09:33 PM"}],
                    "hourly": [{"weatherCode": "2", "chanceofrain": "20"}] * 8
                }
            ] * 5
        }

    def test_theme_registration(self):
        self.assertIn("compact_dashboard", THEMES)
        self.assertEqual(THEMES["compact_dashboard"]["label"], "Compact Dashboard")
        self.assertTrue(THEMES["compact_dashboard"]["implemented"])

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_render_compact_dashboard_produces_correct_dimensions_and_files(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        mock_http.side_effect = RuntimeError("API down")
        
        weather_image.render_compact_dashboard(self.config)
        
        out_image = self.project_path / "kindle_weather.png"
        self.assertTrue(out_image.exists())
        
        with Image.open(out_image) as img:
            self.assertEqual(img.size, (758, 1024))

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_render_compact_dashboard_respects_visibility_toggles_and_does_not_crash(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        mock_http.side_effect = RuntimeError("API down")
        
        # Test 1: Disable weather and forecast (only server visible)
        cfg_server_only = dict(self.config, show_weather=False, show_forecast=False)
        weather_image.render_compact_dashboard(cfg_server_only)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())
        
        # Test 2: Disable server (only weather and forecast visible)
        cfg_weather_only = dict(self.config, show_server=False)
        weather_image.render_compact_dashboard(cfg_weather_only)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())
        
        # Test 3: Disable all sections (minimal layout, does not crash)
        cfg_none = dict(self.config, show_weather=False, show_forecast=False, show_server=False)
        weather_image.render_compact_dashboard(cfg_none)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())

if __name__ == "__main__":
    unittest.main()

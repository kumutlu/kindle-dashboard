import unittest
from unittest import mock
import tempfile
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

import weather_image

class MaarifReliabilityTests(unittest.TestCase):
    def setUp(self):
        # Create temp dir for testing cache and config
        self.test_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.test_dir.name)
        
        # Patch PROJECT_DIR in weather_image to point to our temp dir
        self.project_dir_patcher = mock.patch("weather_image.PROJECT_DIR", self.project_path)
        self.project_dir_patcher.start()
        
        # Patch OUT in weather_image to point to our temp dir
        self.out_patcher = mock.patch("weather_image.OUT", self.project_path / "kindle_weather.png")
        self.out_patcher.start()
        
        # Set up a sample config
        self.config = {
            "title": "TEST CALENDAR",
            "location": "Nottingham",
            "country": "United Kingdom",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "location_display": "Nottingham, England, United Kingdom",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "maarif_calendar",
            "show_weather": True,
            "show_forecast": True,
            "show_server": False,
            "show_pihole": False,
            "show_tailscale": False,
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
            "weather": [{
                "maxtempC": "24",
                "mintempC": "12",
                "astronomy": [{
                    "sunrise": "04:45 AM",
                    "sunset": "09:33 PM"
                }],
                "hourly": []
            }]
        }

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_timezone_local_date_and_coordinates_in_api_request(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        
        def mock_http_side_effect(url, **kwargs):
            if "api.aladhan.com" in url:
                return {"code": 200, "data": {
                    "timings": {
                        "Fajr": "02:58",
                        "Sunrise": "04:45",
                        "Dhuhr": "13:10",
                        "Asr": "17:30",
                        "Maghrib": "21:30",
                        "Isha": "23:05",
                        "Imsak": "02:48"
                    },
                    "date": {
                        "hijri": {
                            "day": "18",
                            "month": {"number": 1},
                            "year": "1448"
                        }
                    }
                }}
            return {}

        mock_http.side_effect = mock_http_side_effect
        
        # Call collect_dashboard_data
        data = weather_image.collect_dashboard_data(self.config)
        
        # Verify coordinates from config are used directly
        # Verify http_json was called with local formatted date
        self.assertTrue(mock_http.called)
        called_urls = [call.args[0] for call in mock_http.call_args_list]
        aladhan_call = next(url for url in called_urls if "api.aladhan.com" in url)
        
        # Date should be today's date in Europe/London timezone
        now_tz = datetime.now(ZoneInfo("Europe/London"))
        expected_date = now_tz.strftime("%d-%m-%Y")
        
        self.assertIn(f"timings/{expected_date}", aladhan_call)
        self.assertIn("latitude=52.9536", aladhan_call)
        self.assertIn("longitude=-1.1505", aladhan_call)
        self.assertIn("method=13", aladhan_call)
        self.assertIn("school=0", aladhan_call)
        self.assertIn("latitudeAdjustmentMethod=3", aladhan_call)

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_cache_is_written_on_successful_api(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        
        def mock_http_side_effect(url, **kwargs):
            if "api.aladhan.com" in url:
                return {"code": 200, "data": {
                    "timings": {
                        "Fajr": "02:58",
                        "Sunrise": "04:45",
                        "Dhuhr": "13:10",
                        "Asr": "17:30",
                        "Maghrib": "21:30",
                        "Isha": "23:05",
                        "Imsak": "02:48"
                    },
                    "date": {
                        "hijri": {
                            "day": "18",
                            "month": {"number": 1},
                            "year": "1448"
                        }
                    }
                }}
            return {}

        mock_http.side_effect = mock_http_side_effect
        
        # Generate cache directory path
        cache_dir = self.project_path / "cache" / "prayer_times"
        self.assertFalse(cache_dir.exists())
        
        weather_image.collect_dashboard_data(self.config)
        
        # Cache directory and cache file should now exist
        self.assertTrue(cache_dir.exists())
        cache_files = list(cache_dir.glob("*.json"))
        self.assertEqual(len(cache_files), 1)
        
        # Read and check cache content
        cache_content = json.loads(cache_files[0].read_text(encoding="utf-8"))
        self.assertEqual(cache_content["fajr"], "02:58")
        self.assertEqual(cache_content["sunrise"], "04:45")
        self.assertEqual(cache_content["hijri_day"], 18)

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_cache_is_used_when_api_fails(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        
        # First pre-populate the cache
        cache_dir = self.project_path / "cache" / "prayer_times"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        local_date = datetime.now(ZoneInfo("Europe/London")).date().isoformat()
        tz_safe = "Europe_London"
        cache_filename = f"{local_date}_52.9536_-1.1505_{tz_safe}_13_0_3.json"
        
        cache_data = {
            "date": local_date,
            "location_display": "Nottingham, UK",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "timezone": "Europe/London",
            "method": 13,
            "school": 0,
            "high_latitude_adjustment": 3,
            "fajr": "02:59",
            "sunrise": "04:46",
            "dhuhr": "13:11",
            "asr": "17:31",
            "maghrib": "21:31",
            "isha": "23:06",
            "imsak": "02:49",
            "hijri_day": 19,
            "hijri_month_num": 1,
            "hijri_year": 1448,
            "source": "aladhan",
            "fetched_at": "2026-07-03 12:00:00"
        }
        (cache_dir / cache_filename).write_text(json.dumps(cache_data), encoding="utf-8")
        
        # Mock API to raise an exception (simulate failure)
        mock_http.side_effect = RuntimeError("API down")
        
        data = weather_image.collect_dashboard_data(self.config)
        
        # Verify timings and Hijri date were loaded from the cache
        self.assertEqual(data["timings"]["Fajr"], "02:59")
        self.assertEqual(data["timings"]["Sunrise"], "04:46")
        self.assertEqual(data["hijri_day"], 19)

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_invalid_prayer_time_order_is_rejected(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        
        # Isha is earlier than Maghrib, which is invalid
        def mock_http_side_effect(url, **kwargs):
            if "api.aladhan.com" in url:
                return {"code": 200, "data": {
                    "timings": {
                        "Fajr": "02:58",
                        "Sunrise": "04:45",
                        "Dhuhr": "13:10",
                        "Asr": "17:30",
                        "Maghrib": "21:30",
                        "Isha": "20:00", 
                        "Imsak": "02:48"
                    },
                    "date": {
                        "hijri": {
                            "day": "18",
                            "month": {"number": 1},
                            "year": "1448"
                        }
                    }
                }}
            return {}

        mock_http.side_effect = mock_http_side_effect
        
        data = weather_image.collect_dashboard_data(self.config)
        
        # Since API response is invalid and no cache exists, timings should be None
        self.assertIsNone(data["timings"])

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_missing_api_and_missing_cache_does_not_crash_but_sets_timings_to_none(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        mock_http.side_effect = RuntimeError("API down")
        
        # Call collect_dashboard_data should not raise exception
        data = weather_image.collect_dashboard_data(self.config)
        self.assertIsNone(data["timings"])

    def test_hijri_adjustment_values(self):
        # adjust_hijri_date(year, month, day, offset)
        # Standard case
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 18, 0), (1448, 1, 18))
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 18, 1), (1448, 1, 19))
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 18, -1), (1448, 1, 17))
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 18, 2), (1448, 1, 20))
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 18, -2), (1448, 1, 16))
        
        # Month rollover backward: 1 Muharram -> last day of Dhul-Hijjah (month 12)
        # Dhul-Hijjah standard length is 29 (except leap year 30). y=1447 is leap.
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 1, -1), (1447, 12, 30))
        
        # Month rollover forward: 30 Muharram -> 1 Safar
        self.assertEqual(weather_image.adjust_hijri_date(1448, 1, 30, 1), (1448, 2, 1))

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_render_maarif_calendar_renders_with_timings_unavailable_msg(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        mock_http.side_effect = RuntimeError("API down")
        
        # Generate with English locale
        config_en = dict(self.config, location_label="London, UK")
        
        # Should not crash during render
        weather_image.render_maarif_calendar(config_en)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())
        
        # Generate with Turkish locale
        config_tr = dict(self.config, location_label="Istanbul, Turkey")
        weather_image.render_maarif_calendar(config_tr)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())

    @mock.patch("weather_image.get_now")
    def test_get_local_date_calculation(self, mock_now):
        # 1. Europe/London local date calculation
        # 2026-07-03 23:59:50 BST (UTC+1)
        mock_now.return_value = datetime(2026, 7, 3, 23, 59, 50, tzinfo=ZoneInfo("Europe/London"))
        
        cfg = {"timezone": "Europe/London"}
        self.assertEqual(weather_image.get_local_date(cfg), "2026-07-03")
        
        # 00:00:10 next day
        mock_now.return_value = datetime(2026, 7, 4, 0, 0, 10, tzinfo=ZoneInfo("Europe/London"))
        self.assertEqual(weather_image.get_local_date(cfg), "2026-07-04")

        # 2. Timezone fallback if config timezone invalid/missing
        mock_now.side_effect = lambda tz: datetime(2026, 7, 5, 12, 0, 0, tzinfo=tz) if tz else datetime(2026, 7, 5, 12, 0, 0)
        
        cfg_invalid = {"timezone": "Invalid/Timezone"}
        self.assertEqual(weather_image.get_local_date(cfg_invalid), "2026-07-05")
        
        cfg_missing = {}
        self.assertEqual(weather_image.get_local_date(cfg_missing), "2026-07-05")

    @mock.patch("weather_image.get_local_date")
    def test_maarif_regenerates_when_local_date_changes(self, mock_local_date):
        cfg = dict(self.config)
        
        # If image/state file does not exist, should regenerate
        self.assertTrue(weather_image.should_regenerate_maarif(cfg))
        
        # Write image and initial state
        (self.project_path / "kindle_weather.png").write_text("fake image", encoding="utf-8")
        
        mock_local_date.return_value = "2026-07-03"
        state = {
            "theme": "maarif_calendar",
            "timezone": "Europe/London",
            "local_date": "2026-07-03",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "config_hash": weather_image.compute_config_hash(cfg),
            "rendered_at": "2026-07-03T23:59:00"
        }
        state_dir = self.project_path / "cache"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "render_state.json").write_text(json.dumps(state), encoding="utf-8")
        
        # When local date is the same, should NOT regenerate
        self.assertFalse(weather_image.should_regenerate_maarif(cfg))
        
        # When local date changes, should regenerate
        mock_local_date.return_value = "2026-07-04"
        self.assertTrue(weather_image.should_regenerate_maarif(cfg))

    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_prayer_cache_key_includes_date_and_timezone(self, mock_http, mock_fetch):
        mock_fetch.return_value = self.mock_weather_data()
        mock_http.return_value = {"code": 200, "data": {
            "timings": {
                "Fajr": "02:58",
                "Sunrise": "04:45",
                "Dhuhr": "13:10",
                "Asr": "17:30",
                "Maghrib": "21:30",
                "Isha": "23:05",
                "Imsak": "02:48"
            },
            "date": {
                "hijri": {
                    "day": "18",
                    "month": {"number": 1},
                    "year": "1448"
                }
            }
        }}
        
        cfg = dict(self.config)
        local_date = weather_image.get_local_date(cfg)
        
        # Call collect_dashboard_data
        weather_image.collect_dashboard_data(cfg)
        
        # Verify the created cache file has the YYYY-MM-DD_lat_lon_timezone_method_school_highlat.json structure
        cache_dir = self.project_path / "cache" / "prayer_times"
        self.assertTrue(cache_dir.exists())
        
        tz_safe = cfg["timezone"].replace("/", "_")
        expected_filename = f"{local_date}_52.9536_-1.1505_{tz_safe}_13_0_3.json"
        self.assertTrue((cache_dir / expected_filename).exists())

    def test_sunset_formatting_helper(self):
        self.assertEqual(weather_image.format_to_24h("09:32 PM"), "21:32")
        self.assertEqual(weather_image.format_to_24h("04:45 AM"), "04:45")
        self.assertEqual(weather_image.format_to_24h("21:32"), "21:32")
        self.assertEqual(weather_image.format_to_24h("12:15 PM"), "12:15")
        self.assertEqual(weather_image.format_to_24h("12:05 AM"), "00:05")
        self.assertEqual(weather_image.format_to_24h(""), "12:00")
        self.assertEqual(weather_image.format_to_24h(None), "12:00")

    @mock.patch("weather_image.get_now")
    @mock.patch("weather_image.fetch_weather")
    @mock.patch("weather_image.http_json")
    def test_maarif_after_midnight_date_rollover_and_name(self, mock_http, mock_fetch, mock_now):
        # 2026-07-05 00:05:00 BST (Europe/London)
        mock_now.return_value = datetime(2026, 7, 5, 0, 5, 0, tzinfo=ZoneInfo("Europe/London"))
        
        # Mock weather with AM/PM sunset/sunrise
        mock_fetch.return_value = {
            "current_condition": [{
                "temp_C": "20",
                "FeelsLikeC": "20",
                "humidity": "65",
                "windspeedMiles": "10",
                "winddir16Point": "W",
                "pressure": "1015",
                "weatherCode": "2",
                "weatherDesc": [{"value": "Partly Cloudy"}]
            }],
            "weather": [{
                "maxtempC": "23",
                "mintempC": "14",
                "astronomy": [{
                    "sunrise": "04:46 AM",
                    "sunset": "09:32 PM"
                }],
                "hourly": []
            }]
        }
        
        # Mock Aladhan API timings
        mock_http.return_value = {"code": 200, "data": {
            "timings": {
                "Fajr": "03:00",
                "Sunrise": "04:46",
                "Dhuhr": "13:12",
                "Asr": "17:32",
                "Maghrib": "21:32",
                "Isha": "23:07",
                "Imsak": "02:50"
            },
            "date": {
                "hijri": {
                    "day": "20",
                    "month": {"number": 1},
                    "year": "1448"
                }
            }
        }}
        
        # Verify date rollover and formatted sunset in collect_dashboard_data
        data = weather_image.collect_dashboard_data(self.config)
        self.assertEqual(data["now"].date().isoformat(), "2026-07-05")
        self.assertEqual(data["day_name_localized"], "SUNDAY") # July 5 2026 is Sunday (English locale)
        self.assertEqual(data["sunset"], "21:32") # Converted PM format correctly
        self.assertEqual(data["sunrise"], "04:46")
        
        # Render check
        weather_image.render_maarif_calendar(self.config)
        self.assertTrue((self.project_path / "kindle_weather.png").exists())


if __name__ == "__main__":
    unittest.main()

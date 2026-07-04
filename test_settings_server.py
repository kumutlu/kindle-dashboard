#!/usr/bin/env python3
import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import settings_server
import weather_image


class ConfigTests(unittest.TestCase):
    def test_missing_config_uses_nottingham_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config = weather_image.load_config(Path(directory) / "missing.json")
        self.assertEqual(config, weather_image.DEFAULT_CONFIG)

    def test_invalid_config_uses_nottingham_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard_config.json"
            path.write_text('{"timezone":"Not/AZone"}', encoding="utf-8")
            config = weather_image.load_config(path)
        self.assertEqual(config, weather_image.DEFAULT_CONFIG)

    def test_weather_query_is_url_encoded(self):
        self.assertEqual(
            weather_image.weather_url("Istanbul Türkiye"),
            "https://wttr.in/Istanbul%20T%C3%BCrkiye?format=j1",
        )

    def test_unknown_config_field_is_rejected(self):
        config = dict(weather_image.DEFAULT_CONFIG)
        config["secret"] = "not allowed"
        with self.assertRaises(ValueError):
            weather_image.validate_config(config)

    def test_legacy_config_is_upgraded_with_optional_location_fields(self):
        legacy = {
            key: value
            for key, value in weather_image.DEFAULT_CONFIG.items()
            if key not in {
                "location",
                "country",
                "latitude",
                "longitude",
                "location_display",
            }
        }
        upgraded = weather_image.validate_config(legacy)
        self.assertEqual(upgraded["location"], legacy["weather_query"])
        self.assertEqual(upgraded["location_display"], legacy["location_label"])
        self.assertIsNone(upgraded["latitude"])
        self.assertIsNone(upgraded["longitude"])


class PiholeSessionTests(unittest.TestCase):
    def test_close_session_uses_delete_with_sid_header(self):
        response = mock.MagicMock()
        response.__enter__.return_value.status = 204
        with mock.patch(
            "weather_image.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            weather_image.close_pihole_v6_sid("test-session-id")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(
            request.get_header("X-ftl-sid"),
            "test-session-id",
        )

    def test_successful_v6_fetch_always_closes_session(self):
        summary = {
            "queries": {"total": 123, "blocked": 45},
            "clients": {"active": 6},
        }
        with (
            mock.patch("weather_image.pihole_v6_sid", return_value="sid"),
            mock.patch("weather_image.http_json", side_effect=[{}, summary]),
            mock.patch("weather_image.close_pihole_v6_sid") as close,
        ):
            result = weather_image.get_pihole()

        self.assertEqual(result["queries"], 123)
        self.assertTrue(result["ok"])
        close.assert_called_once_with("sid")

    def test_failed_v6_fetch_still_closes_session(self):
        with (
            mock.patch("weather_image.pihole_v6_sid", return_value="sid"),
            mock.patch(
                "weather_image.http_json",
                side_effect=[{}, RuntimeError("controlled failure")],
            ),
            mock.patch("weather_image.close_pihole_v6_sid") as close,
        ):
            result = weather_image.get_pihole()

        self.assertFalse(result["ok"])
        close.assert_called_once_with("sid")


class SettingsServerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tempdir.name) / "dashboard_config.json"
        settings_server.atomic_write_config(
            self.config_path,
            weather_image.DEFAULT_CONFIG,
        )
        self.regeneration_calls = 0
        self.fail_regeneration = False
        self.device_calls = []
        self.settings_restart_calls = 0
        self.geocode_queries = []
        self.geocode_failure = False
        self.geocode_results = [{
            "city": "Nottingham",
            "region": "England",
            "country": "United Kingdom",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "timezone": "Europe/London",
            "display_name": "Nottingham, England, United Kingdom",
        }]

        class FakeDevice:
            def run_action(inner_self, action):
                self.device_calls.append(("action", action))
                return f"{action} complete"

            def push(inner_self):
                self.device_calls.append(("push",))
                return "Dashboard generated and pushed"

            def get_light(inner_self):
                self.device_calls.append(("get_light",))
                return 8

            def set_light(inner_self, level):
                if isinstance(level, bool) or not isinstance(level, int):
                    raise ValueError("brightness must be an integer")
                if level < 0 or level > 24:
                    raise ValueError("brightness must be between 0 and 24")
                self.device_calls.append(("set_light", level))
                return level

            def get_status(inner_self):
                self.device_calls.append(("status",))
                return {
                    "connected": True,
                    "autostart": "enabled",
                    "brightness": 8,
                }

            def get_log(inner_self):
                self.device_calls.append(("log",))
                return "safe dashboard log"

            def restart(inner_self, confirmation):
                if confirmation != "RESTART":
                    raise ValueError("restart confirmation is required")
                self.device_calls.append(("restart",))
                return "Kindle restart requested"

        self.device = FakeDevice()

        def regenerate():
            self.regeneration_calls += 1
            if self.fail_regeneration:
                raise RuntimeError("controlled regeneration failure")

        def restart_settings():
            self.settings_restart_calls += 1

        def geocode(query):
            self.geocode_queries.append(query)
            if self.geocode_failure:
                raise RuntimeError("controlled geocoding failure")
            return self.geocode_results

        self.server = settings_server.make_server(
            host="127.0.0.1",
            port=0,
            config_path=self.config_path,
            regenerate=regenerate,
            device=self.device,
            restart_settings=restart_settings,
            geocode=geocode,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_headers, data

    def csrf_token(self):
        _, _, body = self.request("GET", "/settings")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            body,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def test_binding_allows_remote_access(self):
        self.assertEqual(settings_server.BIND_HOST, "0.0.0.0")
        self.assertEqual(settings_server.PORT, 8767)

    def test_health_and_unknown_route(self):
        status, _, body = self.request("GET", "/health")
        self.assertEqual((status, body), (200, b"OK\n"))
        status, _, _ = self.request("GET", "/not-a-route")
        self.assertEqual(status, 404)

    def test_settings_form_contains_city_search_without_quick_shortcuts(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('name="title"', text)
        self.assertIn('name="weather_query"', text)
        self.assertIn('id="city-search"', text)
        self.assertIn('id="city-results"', text)
        self.assertIn("Advanced location settings", text)
        self.assertNotIn('data-preset="', text)
        self.assertNotIn('class="preset-grid"', text)

    def test_settings_page_has_mobile_app_metadata_and_navigation(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            text,
        )
        self.assertIn(
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            text,
        )
        self.assertIn(
            '<meta name="apple-mobile-web-app-title" content="Kindle Dash">',
            text,
        )
        self.assertIn('<meta name="theme-color" content="#111111">', text)
        for label in ("Settings", "Theme", "Device", "Status"):
            self.assertIn(f">{label}</a>", text)

    def test_settings_page_has_required_cards_and_safe_status(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        for section_id in (
            "location",
            "display",
            "theme",
            "device",
            "status",
        ):
            self.assertIn(f'id="{section_id}"', text)
        self.assertIn("NOTTINGHAM HOME", text)
        self.assertIn("Nottingham", text)
        self.assertIn("home_dashboard", text)
        self.assertNotIn("PIHOLE_PASSWORD", text)
        self.assertNotIn("public-token", text)

    def test_device_buttons_are_active_and_have_no_command_payload(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        for label in (
            "Start Dashboard",
            "Return Home",
            "Refresh Now",
            "Restart Kindle",
            "Light Off",
            "Light 1",
            "Light 4",
            "Light 8",
            "Light 12",
            "Light 18",
        ):
            self.assertIn(f">{label}</button>", text)
        self.assertIn('id="push-kindle"', text)
        self.assertNotIn("Coming soon — these controls", text)
        self.assertNotIn("lipc-", text)
        self.assertNotIn("ssh ", text)

    def test_display_flags_render_as_large_toggle_cards(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        for name in (
            "show_weather",
            "show_forecast",
            "show_server",
            "show_pihole",
            "show_tailscale",
        ):
            self.assertIn(f'class="toggle"><input type="checkbox" name="{name}"', text)
        self.assertIn("@media (min-width: 760px)", text)

    def test_theme_selector_lists_all_implemented_themes(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        for value in (
            "home_dashboard",
            "minimal_weather",
            "server_monitor",
            "travel_weather",
            "maarif_calendar",
            "compact_dashboard",
        ):
            self.assertIn(
                f'type="radio" name="theme" value="{value}"',
                text,
            )
            self.assertNotIn(
                f'type="radio" name="theme" value="{value}" disabled',
                text,
            )

    def test_location_card_uses_city_search_with_advanced_fallback(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('id="city-search"', text)
        self.assertIn('id="city-results"', text)
        self.assertIn('id="city-match"', text)
        self.assertIn('<details class="advanced">', text)
        self.assertIn("Advanced location settings", text)
        self.assertIn('name="weather_query"', text)
        self.assertIn('name="location_label"', text)
        self.assertIn('name="timezone"', text)
        for name in (
            "location", "country", "latitude", "longitude",
            "location_display",
        ):
            self.assertIn(f'name="{name}"', text)
        self.assertNotIn('<select id="country"', text)
        self.assertNotIn('id="timezone-select"', text)

    def test_geocode_endpoint_requires_query(self):
        for path in ("/api/geocode", "/api/geocode?q="):
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 400)
            self.assertFalse(json.loads(body)["ok"])

    def test_geocode_endpoint_returns_normalized_results(self):
        status, _, body = self.request(
            "GET",
            "/api/geocode?q=Nottingham",
        )
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.geocode_queries, ["Nottingham"])
        self.assertEqual(payload["results"], self.geocode_results)
        self.assertNotIn("raw", payload)

    def test_geocode_endpoint_handles_no_results_and_api_failure(self):
        self.geocode_results = []
        status, _, body = self.request("GET", "/api/geocode?q=Nowhere")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["results"], [])

        self.geocode_failure = True
        status, _, body = self.request("GET", "/api/geocode?q=Istanbul")
        self.assertEqual(status, 502)
        self.assertEqual(
            json.loads(body)["error"],
            "Location search is temporarily unavailable",
        )

    def test_selected_city_saves_coordinate_config(self):
        selected = dict(weather_image.DEFAULT_CONFIG)
        selected.update({
            "title": "NOTTINGHAM HOME",
            "location": "Nottingham",
            "country": "United Kingdom",
            "latitude": 52.9536,
            "longitude": -1.1505,
            "timezone": "Europe/London",
            "location_display": "Nottingham, England, United Kingdom",
            "weather_query": "Nottingham",
            "location_label": "Nottingham, England, United Kingdom",
        })
        status, _, body = self.request(
            "POST",
            "/api/config",
            body=json.dumps(selected),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["latitude"], 52.9536)
        self.assertEqual(saved["longitude"], -1.1505)
        self.assertEqual(saved["location_display"], selected["location_display"])

    def test_legacy_settings_form_save_still_works(self):
        csrf = self.csrf_token()
        form = {
            "csrf_token": csrf,
            "title": "LONDON DASHBOARD",
            "location_label": "London, UK",
            "weather_query": "London",
            "timezone": "Europe/London",
            "theme": "home_dashboard",
            "show_weather": "on",
            "show_forecast": "on",
            "show_server": "on",
            "show_pihole": "on",
            "show_tailscale": "on",
        }
        status, headers, _ = self.request(
            "POST",
            "/settings",
            body=urlencode(form),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/settings?status=saved")
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["weather_query"], "London")
        self.assertIsNone(saved["latitude"])
        self.assertEqual(self.regeneration_calls, 1)

    def test_settings_form_saves_registered_themes_and_regenerates(self):
        for theme in (
            "family_dashboard",
            "compact_dashboard",
            "maarif_calendar",
        ):
            with self.subTest(theme=theme):
                csrf = self.csrf_token()
                form = {
                    "csrf_token": csrf,
                    "title": "NOTTINGHAM HOME",
                    "location": "Nottingham",
                    "country": "United Kingdom",
                    "latitude": "52.9536",
                    "longitude": "-1.1505",
                    "location_display": "Nottingham, England, United Kingdom",
                    "location_label": "Nottingham, UK",
                    "weather_query": "Nottingham",
                    "timezone": "Europe/London",
                    "theme": theme,
                    "show_weather": "on",
                    "show_forecast": "on",
                    "show_server": "on",
                    "show_pihole": "on",
                    "show_tailscale": "on",
                    "prayer_method": "13",
                    "prayer_school": "1",
                    "prayer_high_latitude": "3",
                    "hijri_adjustment": "1",
                    "refresh_interval_minutes": "30",
                }
                calls_before = self.regeneration_calls
                status, headers, _ = self.request(
                    "POST",
                    "/settings",
                    body=urlencode(form),
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                    },
                )
                self.assertEqual(status, 303)
                self.assertEqual(
                    headers["Location"],
                    "/settings?status=saved",
                )
                saved = json.loads(
                    self.config_path.read_text(encoding="utf-8")
                )
                self.assertEqual(saved["theme"], theme)
                self.assertEqual(
                    self.regeneration_calls,
                    calls_before + 1,
                )
                self.assertEqual(saved["kindle_frontlight"], 8)
                self.assertEqual(saved["refresh_interval_minutes"], 30)
                self.assertEqual(saved["prayer_method"], 13)
                self.assertEqual(saved["prayer_school"], 1)
                self.assertEqual(saved["prayer_high_latitude"], 3)
                self.assertEqual(saved["hijri_adjustment"], 1)
                for key in (
                    "show_weather",
                    "show_forecast",
                    "show_server",
                    "show_pihole",
                    "show_tailscale",
                ):
                    self.assertTrue(saved[key])

    def test_settings_form_rejects_invalid_theme_without_changing_config(self):
        before = self.config_path.read_bytes()
        csrf = self.csrf_token()
        form = {
            "csrf_token": csrf,
            "title": "NOTTINGHAM HOME",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "not-a-theme",
            "show_weather": "on",
            "show_forecast": "on",
            "show_server": "on",
            "show_pihole": "on",
            "show_tailscale": "on",
        }
        status, headers, _ = self.request(
            "POST",
            "/settings",
            body=urlencode(form),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        self.assertEqual(status, 303)
        self.assertIn("unsupported%20theme", headers["Location"])
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.regeneration_calls, 0)

    def test_daily_notes_fields_do_not_block_main_settings_form(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        note_title = re.search(
            r'<input[^>]+id="note-title"[^>]*>',
            text,
        )
        self.assertIsNotNone(note_title)
        self.assertNotRegex(note_title.group(0), r"\brequired\b")

    def test_sticky_action_bar_and_push_are_present(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('class="action-bar"', text)
        self.assertIn('type="submit">Save &amp; Regenerate</button>', text)
        self.assertIn('<button type="button" id="push-kindle">Push to Kindle</button>', text)

    def test_device_get_endpoints_return_safe_data(self):
        status, _, body = self.request("GET", "/api/device/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["connected"])
        status, _, body = self.request("GET", "/api/device/light")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["brightness"], 8)
        status, _, body = self.request("GET", "/api/device/log")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["log"], "safe dashboard log")

    def test_device_mutation_requires_csrf(self):
        status, _, _ = self.request("POST", "/api/device/refresh")
        self.assertEqual(status, 403)
        self.assertEqual(self.device_calls, [])

    def test_device_action_and_light_use_whitelisted_routes(self):
        csrf = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/device/refresh",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200, body)
        self.assertIn(("action", "refresh"), self.device_calls)

        status, _, body = self.request(
            "POST",
            "/api/device/light",
            body=json.dumps({"level": 12}),
            headers={
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["brightness"], 12)

    def test_invalid_light_and_restart_confirmation_are_rejected(self):
        csrf = self.csrf_token()
        for payload in ({"level": 25}, {"level": "8"}, [], None):
            status, _, _ = self.request(
                "POST",
                "/api/device/light",
                body=json.dumps(payload),
                headers={
                    "X-CSRF-Token": csrf,
                    "Content-Type": "application/json",
                },
            )
            self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            "/api/device/restart",
            body=json.dumps({"confirm": "no"}),
            headers={
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 400)
        self.assertNotIn(("restart",), self.device_calls)

    def test_unknown_device_route_is_404(self):
        csrf = self.csrf_token()
        status, _, _ = self.request(
            "POST",
            "/api/device/arbitrary",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 404)

    def test_maintenance_restart_endpoint_requires_csrf(self):
        status, _, _ = self.request(
            "POST",
            "/api/maintenance/restart-settings",
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.settings_restart_calls, 0)

    def test_maintenance_restart_endpoint_is_registered(self):
        csrf = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/maintenance/restart-settings",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 202, body)
        self.assertEqual(self.settings_restart_calls, 1)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_unknown_maintenance_action_is_404(self):
        csrf = self.csrf_token()
        status, _, _ = self.request(
            "POST",
            "/api/maintenance/anything",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 404)

    def test_maintenance_ui_contains_restart_flow(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('id="maintenance"', text)
        self.assertIn("Advanced / Maintenance", text)
        self.assertIn('id="restart-settings-server"', text)
        self.assertIn(
            "Restarting the settings server will make this page "
            "unavailable for a few seconds. Continue?",
            text,
        )
        self.assertIn("Restarting settings server...", text)
        self.assertIn("Settings server restarted successfully.", text)
        self.assertIn(
            "Server is still restarting. Please refresh manually or check SSH.",
            text,
        )
        self.assertIn("/api/maintenance/restart-settings", text)

    def test_maarif_prayer_controls_are_implemented_and_functional(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('name="prayer_method"', text)
        self.assertIn('name="prayer_school"', text)
        self.assertIn('name="prayer_high_latitude"', text)
        self.assertIn('name="hijri_adjustment"', text)
        self.assertIn('<dt>Prayer data status</dt>', text)
        self.assertIn('<dt>Last prayer update</dt>', text)

    def test_status_includes_location_timezone_and_push_state(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn("<dt>Location label</dt>", text)
        self.assertIn("<dt>Timezone</dt>", text)
        self.assertIn("<dt>Last push</dt>", text)

    def test_api_accepts_implemented_theme_and_rejects_placeholder(self):
        minimal = dict(weather_image.DEFAULT_CONFIG)
        minimal["theme"] = "minimal_weather"
        status, _, body = self.request(
            "POST",
            "/api/config",
            body=json.dumps(minimal),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)

        placeholder = dict(weather_image.DEFAULT_CONFIG)
        placeholder["theme"] = "unknown"
        status, _, _ = self.request(
            "POST",
            "/api/config",
            body=json.dumps(placeholder),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)

    def test_get_api_returns_only_config(self):
        status, _, body = self.request("GET", "/api/config")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload, weather_image.DEFAULT_CONFIG)
        self.assertNotIn("password", body.decode("utf-8").lower())
        self.assertNotIn("token", body.decode("utf-8").lower())

    def test_post_api_saves_and_regenerates(self):
        london = dict(weather_image.DEFAULT_CONFIG)
        london.update({
            "title": "LONDON DASHBOARD",
            "location_label": "London, UK",
            "weather_query": "London",
        })
        status, _, body = self.request(
            "POST",
            "/api/config",
            body=json.dumps(london),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.regeneration_calls, 1)
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8")),
            london,
        )

    def test_failed_regeneration_restores_previous_config(self):
        before = self.config_path.read_text(encoding="utf-8")
        self.fail_regeneration = True
        london = dict(weather_image.DEFAULT_CONFIG)
        london["weather_query"] = "London"
        status, _, _ = self.request(
            "POST",
            "/api/config",
            body=json.dumps(london),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 500)
        self.assertEqual(
            self.config_path.read_text(encoding="utf-8"),
            before,
        )

    def test_tabbed_settings_ui_elements(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)

        # Tab button checks
        for tab in ("overview", "location", "theme", "display", "device", "maintenance", "status"):
            self.assertIn(f'data-tab="{tab}"', text)

        # Confirm tabs structure has active class/content
        self.assertIn('class="tabs-nav"', text)
        self.assertIn('class="tab-btn active" data-tab="overview"', text)

        # Confirm Overview tab info exists
        self.assertIn('id="overview"', text)
        self.assertIn('id="overview-push-kindle-btn"', text)

        # Re-check the critical UI elements are present
        self.assertIn('id="city-search"', text)
        self.assertIn('id="push-kindle"', text)
        self.assertIn('id="restart-settings-server"', text)
        self.assertIn('id="restart-kindle"', text)

    def test_persistent_frontlight_levels(self):
        # 1. Test POST /api/device/light with valid levels
        token = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/device/light",
            body=json.dumps({"level": 12}),
            headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        resp = json.loads(body.decode("utf-8"))
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["brightness"], 12)

        # Verify it was saved to config
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(config.get("kindle_frontlight"), 12)

        # Verify device set_light call
        self.assertIn(("set_light", 12), self.device_calls)

        # 2. Test POST with invalid levels (rejected with 400)
        for invalid_level in (5, "8", None, True, 25):
            status, _, _ = self.request(
                "POST",
                "/api/device/light",
                body=json.dumps({"level": invalid_level}),
                headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
            )
            self.assertEqual(status, 400)

        # 3. Test missing config does not crash and uses default 8
        self.config_path.unlink(missing_ok=True)
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        self.assertIn("Current saved default: <strong>8</strong>", body.decode("utf-8"))

    def test_refresh_interval_minutes_saving_and_ui(self):
        # 1. Default refresh interval is 10
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(config.get("refresh_interval_minutes"), 10)

        # 2. UI select presence
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('name="refresh_interval_minutes"', text)
        self.assertIn('Auto refresh interval', text)

        # 3. Save valid value via form submit
        token = self.csrf_token()
        payload = {
            "csrf_token": token,
            "title": "TEST REFRESH",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "home_dashboard",
            "location_label": "Nottingham, UK",
            "refresh_interval_minutes": "60",
        }
        status, _, _ = self.request(
            "POST",
            "/settings",
            body=urlencode(payload),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303) # redirect
        
        # Verify it was saved to config
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(config.get("refresh_interval_minutes"), 60)

        # 4. Reject invalid values (returns 400 or falls back to redirect error)
        # In validate_config we raise ValueError for invalid value
        # handle_form_post redirects with the error message in the status query param
        payload["refresh_interval_minutes"] = "25"
        status, headers, _ = self.request(
            "POST",
            "/settings",
            body=urlencode(payload),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        location = headers["Location"]
        self.assertIn("invalid%20value%20for%20refresh_interval_minutes", location)


if __name__ == "__main__":
    unittest.main()

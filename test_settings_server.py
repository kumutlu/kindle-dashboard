#!/usr/bin/env python3
import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

        self.server = settings_server.make_server(
            host="127.0.0.1",
            port=0,
            config_path=self.config_path,
            regenerate=regenerate,
            device=self.device,
            restart_settings=restart_settings,
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

    def test_settings_form_contains_fields_and_presets(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('name="title"', text)
        self.assertIn('name="weather_query"', text)
        self.assertIn("Nottingham", text)
        self.assertIn("Istanbul", text)

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
        ):
            self.assertIn(
                f'type="radio" name="theme" value="{value}"',
                text,
            )
            self.assertNotIn(
                f'type="radio" name="theme" value="{value}" disabled',
                text,
            )

    def test_location_card_has_city_country_timezone_and_advanced_fields(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('id="country"', text)
        self.assertIn('id="city-search"', text)
        self.assertIn('id="city-match"', text)
        self.assertIn('id="timezone-select"', text)
        self.assertIn('<details class="advanced">', text)
        self.assertIn('name="weather_query"', text)
        self.assertIn('name="location_label"', text)
        self.assertIn('name="timezone"', text)
        for city in (
            "Nottingham", "Leicester", "London", "Birmingham",
            "Manchester", "Oxford", "Reading", "Lincoln", "Istanbul",
            "Ankara", "Izmir", "Antalya", "Amsterdam",
        ):
            self.assertIn(city, text)
        for timezone in (
            "Europe/London", "Europe/Istanbul", "Europe/Amsterdam",
            "Europe/Berlin", "Europe/Paris", "UTC",
        ):
            self.assertIn(timezone, text)

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

    def test_future_prayer_location_controls_are_safe_and_disabled(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertIn('id="same-prayer-location" checked disabled', text)
        self.assertIn('id="prayer-location" disabled', text)
        self.assertIn('id="prayer-country" disabled', text)
        self.assertNotIn('name="prayer_location"', text)
        self.assertNotIn('name="prayer_country"', text)

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


if __name__ == "__main__":
    unittest.main()

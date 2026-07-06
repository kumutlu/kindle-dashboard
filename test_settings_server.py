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
from device_registry import DeviceRegistry


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

    def test_legacy_theme_alias_is_normalized(self):
        legacy = dict(weather_image.DEFAULT_CONFIG)
        legacy.pop("theme")
        legacy["dashboard_mode"] = "maarif_calendar"

        upgraded = weather_image.validate_config(legacy)

        self.assertEqual(upgraded["theme"], "maarif_calendar")


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
        self.rendered_device_ids = []
        self.rendered_device_themes = []
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
            def run_action(inner_self, action, *args, **kwargs):
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("action", action))
                else:
                    self.device_calls.append(("action", action, dev_id))
                return f"{action} complete"

            def push(inner_self, *args, **kwargs):
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("push",))
                else:
                    self.device_calls.append(("push", dev_id))
                return "Dashboard generated and pushed"

            def get_light(inner_self, *args, **kwargs):
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("get_light",))
                else:
                    self.device_calls.append(("get_light", dev_id))
                return 8

            def set_light(inner_self, level, *args, **kwargs):
                if isinstance(level, bool) or not isinstance(level, int):
                    raise ValueError("brightness must be an integer")
                if level < 0 or level > 24:
                    raise ValueError("brightness must be between 0 and 24")
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("set_light", level))
                else:
                    self.device_calls.append(("set_light", level, dev_id))
                return level

            def get_status(inner_self, *args, **kwargs):
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("status",))
                else:
                    self.device_calls.append(("status", dev_id))
                return {
                    "connected": True,
                    "autostart": "enabled",
                    "brightness": 8,
                }

            def get_log(inner_self, *args, **kwargs):
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("log",))
                else:
                    self.device_calls.append(("log", dev_id))
                return "safe dashboard log"

            def restart(inner_self, confirmation, *args, **kwargs):
                if confirmation != "RESTART":
                    raise ValueError("restart confirmation is required")
                dev_id = kwargs.get("device_id")
                if dev_id == "default-kindle" or dev_id is None:
                    self.device_calls.append(("restart",))
                else:
                    self.device_calls.append(("restart", dev_id))
                return "Kindle restart requested"

        self.device = FakeDevice()

        def regenerate():
            self.regeneration_calls += 1
            if self.fail_regeneration:
                raise RuntimeError("controlled regeneration failure")

        def render_selected(device_id):
            self.regeneration_calls += 1
            self.rendered_device_ids.append(device_id)
            device = self.registry.get(device_id, require_enabled=True)
            rendered_config = weather_image.load_effective_device_config(
                device,
                self.registry,
            )
            self.rendered_device_themes.append(
                rendered_config["theme"],
            )
            if self.fail_regeneration:
                raise RuntimeError("controlled regeneration failure")

        def restart_settings():
            self.settings_restart_calls += 1

        def geocode(query):
            self.geocode_queries.append(query)
            if self.geocode_failure:
                raise RuntimeError("controlled geocoding failure")
            return self.geocode_results

        self.registry = DeviceRegistry(Path(self.tempdir.name))
        self.server = settings_server.make_server(
            host="127.0.0.1",
            port=0,
            config_path=self.config_path,
            regenerate=regenerate,
            render_selected=render_selected,
            device=self.device,
            restart_settings=restart_settings,
            geocode=geocode,
            registry=self.registry,
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
        self.assertEqual(saved["theme"], "home_dashboard")
        self.assertEqual(self.regeneration_calls, 1)
        self.assertEqual(
            self.rendered_device_ids,
            ["default-kindle"],
        )
        device_config_path = self.registry.get(
            "default-kindle"
        ).config_path
        self.assertEqual(
            json.loads(device_config_path.read_text(encoding="utf-8")),
            saved,
        )

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
                    "selected_device_id": "default-kindle",
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
                self.assertEqual(
                    self.rendered_device_ids[-1],
                    "default-kindle",
                )
                self.assertEqual(self.rendered_device_themes[-1], theme)
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

    def test_selected_device_form_save_updates_only_selected_device(self):
        kitchen = self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
        })
        legacy_before = self.config_path.read_bytes()
        csrf = self.csrf_token()
        form = {
            "csrf_token": csrf,
            "selected_device_id": "kitchen-kindle",
            "title": "KITCHEN DASHBOARD",
            "location": "London",
            "country": "United Kingdom",
            "latitude": "51.5072",
            "longitude": "-0.1276",
            "location_display": "London, England, United Kingdom",
            "location_label": "London, UK",
            "weather_query": "London",
            "timezone": "Europe/London",
            "theme": "compact_dashboard",
            "show_weather": "on",
            "show_forecast": "on",
            "refresh_interval_minutes": "30",
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
        self.assertIn("saved", headers["Location"])
        saved = json.loads(
            kitchen.config_path.read_text(encoding="utf-8")
        )
        self.assertEqual(saved["theme"], "compact_dashboard")
        self.assertEqual(saved["weather_query"], "London")
        self.assertEqual(saved["refresh_interval_minutes"], 30)
        self.assertEqual(
            self.rendered_device_ids[-1],
            "kitchen-kindle",
        )
        self.assertEqual(
            self.rendered_device_themes[-1],
            "compact_dashboard",
        )
        self.assertEqual(self.config_path.read_bytes(), legacy_before)

    def test_invalid_selected_device_is_rejected_without_writes(self):
        before = self.config_path.read_bytes()
        csrf = self.csrf_token()
        base_form = {
            "csrf_token": csrf,
            "title": "DO NOT SAVE",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "home_dashboard",
        }
        for invalid in ("missing", "../escape", "UPPERCASE"):
            with self.subTest(device_id=invalid):
                form = dict(
                    base_form,
                    selected_device_id=invalid,
                )
                status, headers, body = self.request(
                    "POST",
                    "/settings",
                    body=urlencode(form),
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                    },
                )
                self.assertEqual(status, 303, body)
                self.assertIn(
                    "selected%20device%20is%20unavailable",
                    headers["Location"],
                )
                self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.rendered_device_ids, [])

    def test_disabled_selected_device_is_rejected(self):
        self.registry.get("default-kindle")
        registry_path = Path(self.tempdir.name) / "devices.json"
        stored = json.loads(registry_path.read_text(encoding="utf-8"))
        stored["devices"][0]["enabled"] = False
        self.registry.write_registry(stored)
        csrf = self.csrf_token()
        form = {
            "csrf_token": csrf,
            "selected_device_id": "default-kindle",
            "title": "DO NOT SAVE",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "theme": "home_dashboard",
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
        self.assertIn(
            "selected%20device%20is%20unavailable",
            headers["Location"],
        )
        self.assertEqual(self.rendered_device_ids, [])

    def test_selected_device_api_envelope_updates_and_renders(self):
        config = dict(weather_image.DEFAULT_CONFIG)
        config["theme"] = "minimal_weather"
        status, _, body = self.request(
            "POST",
            "/api/config",
            body=json.dumps({
                "selected_device_id": "default-kindle",
                "config": config,
            }),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(
            json.loads(body)["device_id"],
            "default-kindle",
        )
        self.assertEqual(
            self.rendered_device_ids,
            ["default-kindle"],
        )

    def test_selected_device_ui_has_hidden_field_and_indicator(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")

        self.assertIn('name="selected_device_id"', text)
        self.assertIn('id="selected-device-id"', text)
        self.assertIn(
            'id="editing-device-name">Default Kindle',
            text,
        )
        self.assertIn("Editing device:", text)

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
        self.assertIn(
            'type="submit" data-settings-action="save">Save &amp; Regenerate</button>',
            text,
        )
        self.assertIn(
            'id="push-kindle" data-settings-action="push">Push to Kindle</button>',
            text,
        )

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
        for tab in (
            "overview",
            "devices",
            "location",
            "theme",
            "display",
            "device",
            "maintenance",
            "status",
        ):
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

    def test_notes_endpoint_saving_and_validation(self):
        # Override path to temp dir
        test_notes_path = Path(self.tempdir.name) / "daily_notes.json"
        settings_server.DAILY_NOTES_PATH = test_notes_path
        if test_notes_path.exists():
            test_notes_path.unlink()

        csrf = self.csrf_token()
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        }

        # 1. Add always-active reminder writes daily_notes.json
        payload_always = {
            "title": "Always active task",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "start_date": "2026-07-04",
            "date": None,
            "recurrence": None,
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_always),
            headers=headers
        )
        self.assertEqual(status, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertTrue(res["ok"])
        
        # Verify file written
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        self.assertEqual(len(notes["items"]), 1)
        self.assertEqual(notes["items"][0]["title"], "Always active task")
        self.assertEqual(notes["items"][0]["start_date"], "2026-07-04")
        self.assertIsNone(notes["items"][0]["recurrence"])

        # 2. Add one-off reminder writes date field
        payload_oneoff = {
            "title": "One-off task",
            "category": "TODO",
            "priority": "low",
            "enabled": True,
            "date": "2026-07-05",
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_oneoff),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        self.assertEqual(len(notes["items"]), 2)
        oneoff_item = next(item for item in notes["items"] if item["title"] == "One-off task")
        self.assertEqual(oneoff_item["date"], "2026-07-05")

        # 3. Add weekly reminder writes recurrence.type = weekly
        payload_weekly = {
            "title": "Weekly task",
            "category": "SCHOOL",
            "priority": "normal",
            "enabled": True,
            "recurrence": {
                "type": "weekly",
                "days": ["MON", "FRI"]
            }
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_weekly),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        weekly_item = next(item for item in notes["items"] if item["title"] == "Weekly task")
        self.assertEqual(weekly_item["recurrence"]["type"], "weekly")
        self.assertEqual(weekly_item["recurrence"]["days"], ["MON", "FRI"])

        # 4. Add fortnightly reminder writes recurrence.type = fortnightly and anchor_date
        payload_fortnightly = {
            "title": "Fortnightly task",
            "category": "BIN",
            "priority": "high",
            "enabled": True,
            "recurrence": {
                "type": "fortnightly",
                "days": ["MON"],
                "anchor_date": "2026-07-06"
            }
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_fortnightly),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        fort_item = next(item for item in notes["items"] if item["title"] == "Fortnightly task")
        self.assertEqual(fort_item["recurrence"]["type"], "fortnightly")
        self.assertEqual(fort_item["recurrence"]["anchor_date"], "2026-07-06")

        # 5. Add monthly reminder writes recurrence.type = monthly and day_of_month
        payload_monthly = {
            "title": "Monthly task",
            "category": "HOME",
            "priority": "normal",
            "enabled": True,
            "recurrence": {
                "type": "monthly",
                "day_of_month": 15
            }
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_monthly),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        mon_item = next(item for item in notes["items"] if item["title"] == "Monthly task")
        self.assertEqual(mon_item["recurrence"]["type"], "monthly")
        self.assertEqual(mon_item["recurrence"]["day_of_month"], 15)

        # 6. Missing title is rejected
        payload_bad = dict(payload_always, title="")
        status, _, body = self.request("POST", "/api/notes/save", body=json.dumps(payload_bad), headers=headers)
        self.assertEqual(status, 400)
        self.assertIn("Title is required", json.loads(body.decode("utf-8"))["error"])

        # 7. Weekly without weekday is rejected
        payload_bad = {
            "title": "Bad weekly",
            "recurrence": {
                "type": "weekly",
                "days": []
            }
        }
        status, _, body = self.request("POST", "/api/notes/save", body=json.dumps(payload_bad), headers=headers)
        self.assertEqual(status, 400)
        self.assertIn("at least one day selected", json.loads(body.decode("utf-8"))["error"])

        # 8. Fortnightly without anchor_date is rejected
        payload_bad = {
            "title": "Bad fort",
            "recurrence": {
                "type": "fortnightly",
                "days": ["MON"],
                "anchor_date": ""
            }
        }
        status, _, body = self.request("POST", "/api/notes/save", body=json.dumps(payload_bad), headers=headers)
        self.assertEqual(status, 400)
        self.assertIn("requires a start cycle anchor date", json.loads(body.decode("utf-8"))["error"])

        # 9. Monthly invalid day is rejected
        payload_bad = {
            "title": "Bad month",
            "recurrence": {
                "type": "monthly",
                "day_of_month": 35
            }
        }
        status, _, body = self.request("POST", "/api/notes/save", body=json.dumps(payload_bad), headers=headers)
        self.assertEqual(status, 400)
        self.assertIn("between 1 and 31", json.loads(body.decode("utf-8"))["error"])

    def test_malformed_daily_notes_file_handled_safely(self):
        # Create a malformed json file
        test_notes_path = Path(self.tempdir.name) / "daily_notes.json"
        settings_server.DAILY_NOTES_PATH = test_notes_path
        test_notes_path.write_text("{malformed json", encoding="utf-8")
        
        # Should not crash and return empty notes object
        notes = settings_server.load_daily_notes()
        self.assertEqual(notes, {"items": []})

    def test_settings_dark_mode_theme_toggle_elements(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('class="theme-toggle-group"', text)
        self.assertIn('data-theme-val="light"', text)
        self.assertIn('data-theme-val="dark"', text)
        self.assertIn('data-theme-val="system"', text)
        self.assertIn('[data-theme="dark"]', text)
        self.assertIn('kindle_dashboard_ui_theme', text)

    def test_push_named_device_renders_and_refreshes(self):
        # Add a named Kindle PW1 device
        kitchen = self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.150",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            }
        })
        
        csrf = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/device/kitchen-kindle/push",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        self.assertIn("push", [call[0] for call in self.device_calls])
        # Find the push call for kitchen-kindle
        push_calls = [call for call in self.device_calls if call[0] == "push"]
        self.assertEqual(push_calls[-1], ("push", "kitchen-kindle"))

    def test_push_non_kindle_is_rejected(self):
        # Add a generic PNG display
        panel = self.registry.add({
            "id": "living-panel",
            "name": "Living Panel",
            "type": "generic_png",
            "resolution": [800, 600],
            "enabled": True,
            "config_path": "devices/living-panel/config.json",
            "image_path": "devices/living-panel/image.png",
        })
        
        csrf = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/device/living-panel/push",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported device type", json.loads(body.decode("utf-8"))["error"])

    def test_device_qualified_get_status(self):
        kitchen = self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.150",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            }
        })
        status, _, body = self.request("GET", "/api/device/kitchen-kindle/status")
        self.assertEqual(status, 200)
        status_calls = [call for call in self.device_calls if call[0] == "status"]
        self.assertEqual(status_calls[-1], ("status", "kitchen-kindle"))

    def test_notes_save_with_devices(self):
        # Override path to temp dir
        test_notes_path = Path(self.tempdir.name) / "daily_notes.json"
        settings_server.DAILY_NOTES_PATH = test_notes_path
        if test_notes_path.exists():
            test_notes_path.unlink()

        csrf = self.csrf_token()
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        }

        # Add kitchen-kindle
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
        })

        # 1. Add note targeting kitchen-kindle
        payload = {
            "title": "Kitchen note",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "devices": ["kitchen-kindle"]
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload),
            headers=headers
        )
        self.assertEqual(status, 200)
        res = json.loads(body.decode("utf-8"))
        note_id = res["id"]

        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        self.assertEqual(len(notes["items"]), 1)
        self.assertEqual(notes["items"][0]["devices"], ["kitchen-kindle"])

        # 2. Edit note to target both default-kindle and kitchen-kindle
        payload_edit = {
            "id": note_id,
            "title": "Kitchen note updated",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "devices": ["default-kindle", "kitchen-kindle"]
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_edit),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        self.assertEqual(notes["items"][0]["devices"], ["default-kindle", "kitchen-kindle"])

        # 3. Edit note to clear devices (make it global)
        payload_clear = {
            "id": note_id,
            "title": "Kitchen note updated",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "devices": []
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_clear),
            headers=headers
        )
        self.assertEqual(status, 200)
        notes = json.loads(test_notes_path.read_text(encoding="utf-8"))
        self.assertNotIn("devices", notes["items"][0])

    def test_notes_save_validation_for_devices(self):
        csrf = self.csrf_token()
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        }

        # 1. Unknown device ID rejected
        payload = {
            "title": "Unknown device note",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "devices": ["ghost-kindle"]
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload),
            headers=headers
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown device ID: ghost-kindle", json.loads(body.decode("utf-8"))["error"])

        # 2. Invalid device ID format (traversal) rejected
        payload_bad = {
            "title": "Bad traversal note",
            "category": "NOTE",
            "priority": "normal",
            "enabled": True,
            "devices": ["../../x"]
        }
        status, _, body = self.request(
            "POST",
            "/api/notes/save",
            body=json.dumps(payload_bad),
            headers=headers
        )
        self.assertEqual(status, 400)
        self.assertIn("Invalid device ID format: ../../x", json.loads(body.decode("utf-8"))["error"])

    def test_settings_html_includes_show_on_devices_controls(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Show on devices", text)
        self.assertIn('id="note-device-all"', text)
        self.assertIn('id="note-individual-devices"', text)

    def test_api_devices_still_safe(self):
        status, _, body = self.request("GET", "/api/devices")
        self.assertEqual(status, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertIn("devices", res)
        # Verify default-kindle is present
        dev_ids = [d["id"] for d in res["devices"]]
        self.assertIn("default-kindle", dev_ids)

    def test_esp32_device_config_and_push_rejection(self):
        self.registry.add({
            "id": "office-esp32",
            "name": "Office ESP32",
            "type": "esp32_epaper",
            "enabled": True,
            "resolution": [800, 480],
            "config_path": "devices/office-esp32/config.json",
            "image_path": "devices/office-esp32/image.png",
            "connection": {
                "method": "http",
                "host": "192.168.68.200",
                "port": 80
            }
        })

        status, _, body = self.request("GET", "/api/device/office-esp32/config")
        self.assertEqual(status, 200)
        res = json.loads(body.decode("utf-8"))
        self.assertEqual(res["device_id"], "office-esp32")
        self.assertEqual(res["type"], "esp32_epaper")
        self.assertEqual(res["bmp_url"], "/device/office-esp32/image.bmp")
        self.assertEqual(res["image_url"], "/device/office-esp32/image.png")
        self.assertIn("theme", res)
        self.assertNotIn("connection", res)
        self.assertNotIn("host", res)
        self.assertNotIn("method", res)

        csrf = self.csrf_token()
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        }
        status, _, body = self.request(
            "POST",
            "/api/device/office-esp32/push",
            headers=headers
        )
        self.assertEqual(status, 400)
        res_push = json.loads(body.decode("utf-8"))
        self.assertFalse(res_push["ok"])
        self.assertEqual(res_push["error"], "Push is not implemented for esp32_epaper devices")

        status_ui, _, body_ui = self.request("GET", "/settings")
        self.assertEqual(status_ui, 200)
        text_ui = body_ui.decode("utf-8")
        self.assertIn("Office ESP32", text_ui)
        self.assertIn("esp32_epaper", text_ui)
        self.assertIn("800×480", text_ui)
        self.assertIn("Open BMP endpoint", text_ui)
        self.assertIn("Push is unsupported for this device type", text_ui)

    def test_settings_image_server_port_and_url_resolution(self):
        # 1. Test image_server_port default is reflected in the /settings page response
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("const imageServerUrl = \"http://127.0.0.1:8765\";", text)
        self.assertIn("http://127.0.0.1:8765/device/default-kindle/image.png", text)

        # 2. Test environment variable IMAGE_SERVER_URL override
        import os
        old_env = os.environ.get("IMAGE_SERVER_URL")
        try:
            os.environ["IMAGE_SERVER_URL"] = "http://dashboard-images.local:9000"
            status2, _, body2 = self.request("GET", "/settings")
            self.assertEqual(status2, 200)
            text2 = body2.decode("utf-8")
            self.assertIn("const imageServerUrl = \"http://dashboard-images.local:9000\";", text2)
            self.assertIn("http://dashboard-images.local:9000/device/default-kindle/image.png", text2)
        finally:
            if old_env is None:
                os.environ.pop("IMAGE_SERVER_URL", None)
            else:
                os.environ["IMAGE_SERVER_URL"] = old_env

    def test_visible_apple_actions_use_shared_action_contract(self):
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")

        self.assertGreaterEqual(
            text.count('data-settings-action="save"'),
            2,
        )
        self.assertGreaterEqual(
            text.count('data-settings-action="push"'),
            3,
        )
        self.assertGreaterEqual(
            text.count('data-preview-action="open"'),
            2,
        )
        self.assertGreaterEqual(text.count('type="submit"'), 2)
        self.assertIn(
            """querySelectorAll('[data-settings-action="push"]')""",
            text,
        )
        self.assertIn("triggerSelectedDevicePush(button)", text)
        self.assertIn("let remindersPreviewReady = false;", text)
        self.assertIn(
            "if (remindersPreviewReady) {\n    renderRemindersPreview();",
            text,
        )
        self.assertIn("remindersPreviewReady = true;", text)

    def test_preview_resolver_uses_image_server_base_for_relative_urls(self):
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")

        self.assertIn("function resolveDeviceImageUrl(imageUrl, deviceId)", text)
        self.assertIn("new URL(safePath, imageServerUrl)", text)
        self.assertIn(
            "const resolvedImageUrl = resolveDeviceImageUrl(imageUrl, selected);",
            text,
        )
        self.assertIn(
            """document.querySelectorAll('[data-preview-action="open"]')""",
            text,
        )

    def test_selected_device_controls_and_required_tabs_remain_present(self):
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")

        self.assertIn('id="selected-device-id"', text)
        self.assertIn('id="selected-device"', text)
        self.assertIn('id="top-selected-device"', text)
        self.assertIn("kindle_dashboard_selected_device", text)
        for tab_id in ("devices", "daily_notes", "theme"):
            self.assertIn(f'data-tab="{tab_id}"', text)
        for theme_value in ("light", "dark", "system"):
            self.assertIn(f'data-theme-val="{theme_value}"', text)

    def test_devices_tab_shows_status_fields(self):
        status, _, body = self.request("GET", "/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        for label in (
            "Status",
            "Battery",
            "Charging",
            "Last Seen",
            "Last Refresh",
            "IP Address",
            "Firmware",
        ):
            self.assertIn(label, text)


class DeviceConfigEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "dashboard_config.json"
        config = dict(weather_image.DEFAULT_CONFIG)
        config.update({
            "theme": "family_dashboard",
            "refresh_interval_minutes": 30,
            "kindle_frontlight": 12,
        })
        settings_server.atomic_write_config(self.config_path, config)
        (self.root / "kindle_weather.png").write_bytes(
            b"\x89PNG\r\n\x1a\nconfig-endpoint-test"
        )
        self.registry = DeviceRegistry(self.root)
        self.registry.get("default-kindle")
        self.rendered_device_ids = []
        self.server = settings_server.make_server(
            host="127.0.0.1",
            port=0,
            config_path=self.config_path,
            regenerate=lambda: None,
            render_selected=self.rendered_device_ids.append,
            device=mock.MagicMock(),
            restart_settings=lambda: None,
            geocode=lambda query: [],
            registry=self.registry,
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

    def request(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        connection.close()
        return status, body

    def post_json(self, path, payload, headers=None):
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        connection.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers=request_headers,
        )
        response = connection.getresponse()
        body = response.read()
        response_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, response_headers, body

    def test_default_device_config_returns_safe_allowlisted_json(self):
        status, body = self.request(
            "/api/device/default-kindle/config"
        )
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload),
            {
                "device_id",
                "name",
                "type",
                "resolution",
                "theme",
                "refresh_interval_minutes",
                "kindle_frontlight",
                "image_url",
                "enabled",
            },
        )
        self.assertEqual(payload["device_id"], "default-kindle")
        self.assertEqual(payload["type"], "kindle_pw1")
        self.assertEqual(payload["resolution"], [758, 1024])
        self.assertEqual(payload["theme"], "family_dashboard")
        self.assertEqual(payload["refresh_interval_minutes"], 30)
        self.assertEqual(payload["kindle_frontlight"], 12)
        self.assertEqual(
            payload["image_url"],
            "/device/default-kindle/image.png",
        )

        serialized = body.decode("utf-8").lower()
        for forbidden in (
            "key_path",
            "known_hosts",
            "password",
            "token",
            "private_key",
            "/home/user/.ssh",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_default_device_config_inherits_global_theme_when_device_theme_missing(self):
        device = self.registry.get("default-kindle")
        device_config = json.loads(
            device.config_path.read_text(encoding="utf-8")
        )
        device_config.pop("theme")
        device.config_path.write_text(
            json.dumps(device_config),
            encoding="utf-8",
        )

        status, body = self.request(
            "/api/device/default-kindle/config"
        )
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["theme"], "family_dashboard")

    def test_invalid_unknown_and_traversal_device_config_is_404(self):
        for path in (
            "/api/device/missing/config",
            "/api/device/../config",
            "/api/device/%2e%2e/config",
            "/api/device/default-kindle/../../dashboard_config.json",
            "/api/device/UPPERCASE/config",
        ):
            with self.subTest(path=path):
                status, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(
                    str(self.root).encode("utf-8"),
                    body,
                )

    def test_devices_api_returns_safe_allowlisted_registry_data(self):
        status, body = self.request("/api/devices")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"devices"})
        self.assertEqual(len(payload["devices"]), 1)
        device = payload["devices"][0]
        self.assertEqual(
            set(device),
            {
                "id",
                "name",
                "type",
                "enabled",
                "resolution",
                "theme",
                "image_url",
                "config_url",
                "connection",
                "status",
            },
        )
        self.assertEqual(device["id"], "default-kindle")
        self.assertEqual(device["theme"], "family_dashboard")
        self.assertEqual(
            device["image_url"],
            "/device/default-kindle/image.png",
        )
        self.assertEqual(
            device["config_url"],
            "/api/device/default-kindle/config",
        )
        self.assertEqual(
            set(device["connection"]),
            {"host", "user", "ssh_profile", "port"},
        )

        serialized = body.decode("utf-8").lower()
        for forbidden in (
            "key_path",
            "known_hosts",
            "password",
            "token",
            "private_key",
            "/home/user/.ssh",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_device_status_post_and_get_round_trip(self):
        payload = {
            "battery_percent": 77,
            "charging": False,
            "battery_voltage": 3.92,
            "wifi_rssi": -61,
            "ip_address": "192.168.68.88",
            "firmware_version": "5.6.1.1",
            "last_refresh_at": "2026-07-06T10:30:00+00:00",
        }
        status, _, body = self.post_json(
            "/api/device/default-kindle/status",
            payload,
        )
        self.assertEqual(status, 200)
        saved = json.loads(
            (
                self.root / "devices/default-kindle/status.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(saved["battery_percent"], 77)
        self.assertEqual(saved["firmware_version"], "5.6.1.1")

        status, body = self.request(
            "/api/device/default-kindle/status"
        )
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["battery_percent"], 77)
        self.assertFalse(result["charging"])
        self.assertIn("online", result)

    def test_device_status_allows_missing_battery_fields(self):
        status, _, _ = self.post_json(
            "/api/device/default-kindle/status",
            {"ip_address": "192.168.68.88"},
        )

        self.assertEqual(status, 200)
        status, body = self.request(
            "/api/device/default-kindle/status"
        )
        result = json.loads(body)
        self.assertIsNone(result["battery_percent"])
        self.assertEqual(result["ip_address"], "192.168.68.88")

    def test_device_status_rejects_invalid_and_traversal_device_ids(self):
        for path in (
            "/api/device/missing/status",
            "/api/device/../status",
            "/api/device/%2e%2e/status",
            "/api/device/UPPERCASE/status",
        ):
            with self.subTest(path=path):
                status, _, _ = self.post_json(
                    path,
                    {"battery_percent": 50},
                )
                self.assertEqual(status, 404)

    def test_status_token_protected_device_rejects_wrong_token(self):
        device = self.registry.get("default-kindle")
        raw = json.loads(device.config_path.read_text(encoding="utf-8"))
        raw["status_token"] = "correct-token"
        device.config_path.write_text(
            json.dumps(raw),
            encoding="utf-8",
        )

        status, _, _ = self.post_json(
            "/api/device/default-kindle/status",
            {"battery_percent": 50},
            headers={"X-Device-Token": "wrong-token"},
        )
        self.assertEqual(status, 403)

        status, _, _ = self.post_json(
            "/api/device/default-kindle/status",
            {"battery_percent": 50},
            headers={"X-Device-Token": "correct-token"},
        )
        self.assertEqual(status, 200)

    def test_status_token_in_device_config_does_not_break_public_device_config(self):
        device = self.registry.get("default-kindle")
        raw = json.loads(device.config_path.read_text(encoding="utf-8"))
        raw["status_token"] = "correct-token"
        device.config_path.write_text(
            json.dumps(raw),
            encoding="utf-8",
        )

        status, body = self.request("/api/devices")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertNotIn("correct-token", json.dumps(payload))
        self.assertNotIn("status_token", json.dumps(payload))

    def test_devices_api_includes_status_summary_without_token(self):
        self.post_json(
            "/api/device/default-kindle/status",
            {
                "battery_percent": 82,
                "charging": True,
                "ip_address": "192.168.68.88",
                "firmware_version": "5.6.1.1",
            },
        )

        status, body = self.request("/api/devices")
        payload = json.loads(body)
        device = payload["devices"][0]
        self.assertEqual(status, 200)
        self.assertEqual(device["status"]["battery_percent"], 82)
        self.assertTrue(device["status"]["charging"])
        self.assertEqual(device["status"]["ip_address"], "192.168.68.88")
        self.assertNotIn("status_token", json.dumps(device))
        self.assertNotIn("correct-token", json.dumps(device))

    def test_create_new_kindle_device_generates_id_tokens_and_files(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "profile": "kindle_pw1",
                "theme": "family_dashboard",
                "host": "192.168.68.120",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 201)
        self.assertEqual(payload["device"]["id"], "kitchen-kindle")
        self.assertEqual(payload["device"]["type"], "kindle_pw1")
        self.assertIn("install_command", payload)
        self.assertIn("/install/kindle/kitchen-kindle?token=", payload["install_command"])
        self.assertGreaterEqual(len(payload["pairing_token"]), 32)
        self.assertGreaterEqual(len(payload["status_token"]), 32)

        device = self.registry.get("kitchen-kindle")
        config = json.loads(device.config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["theme"], "family_dashboard")
        self.assertEqual(config["status_token"], payload["status_token"])
        self.assertEqual(config["pairing_token"], payload["pairing_token"])
        self.assertTrue(device.image_path.parent.exists())
        self.assertTrue(device.config_path.exists())
        self.assertTrue((device.image_path.parent / "status.json").exists())

    def test_create_duplicate_name_generates_unique_device_id_without_overwrite(self):
        for _ in range(2):
            status, _, _ = self.post_json(
                "/api/devices",
                {
                    "type": "kindle_pw1",
                    "name": "Kitchen Kindle",
                    "theme": "home_dashboard",
                },
            )
            self.assertEqual(status, 201)

        ids = [record.id for record in self.registry.load()]
        self.assertIn("kitchen-kindle", ids)
        self.assertIn("kitchen-kindle-2", ids)
        self.assertIn("default-kindle", ids)

    def test_create_esp32_device_uses_esp32_profile(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "esp32_epaper",
                "name": "Office Panel",
                "profile": "esp32_800x480",
                "theme": "minimal_weather",
                "host": "192.168.68.150",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 201)
        self.assertEqual(payload["device"]["id"], "office-panel")
        self.assertEqual(payload["device"]["resolution"], [800, 480])
        device = self.registry.get("office-panel")
        self.assertEqual(device.connection["method"], "http")
        self.assertEqual(device.connection["host"], "192.168.68.150")

    def test_installer_script_requires_pairing_token_and_contains_endpoints(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        created = json.loads(body)

        status, body = self.request("/install/kindle/kitchen-kindle?token=wrong")
        self.assertEqual(status, 403)

        status, body = self.request(
            "/install/kindle/kitchen-kindle?token="
            + created["pairing_token"]
        )
        script = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('SERVER_HOST="', script)
        self.assertIn('DEVICE_ID="kitchen-kindle"', script)
        self.assertIn('STATUS_TOKEN="' + created["status_token"] + '"', script)
        self.assertIn("/device/kitchen-kindle/image.png", script)
        self.assertIn("/api/device/kitchen-kindle/status", script)

    def test_pair_endpoint_requires_token_and_marks_status_seen(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        created = json.loads(body)

        status, _, _ = self.post_json(
            "/api/device/kitchen-kindle/pair",
            {"token": "wrong"},
        )
        self.assertEqual(status, 403)

        status, _, body = self.post_json(
            "/api/device/kitchen-kindle/pair",
            {"token": created["pairing_token"]},
        )
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["device_id"], "kitchen-kindle")
        status_file = self.root / "devices/kitchen-kindle/status.json"
        self.assertTrue(status_file.exists())

    def test_create_device_rejects_invalid_theme_and_does_not_overwrite_existing(self):
        before = json.loads((self.root / "devices.json").read_text(encoding="utf-8"))
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Bad Device",
                "theme": "not-a-theme",
            },
        )

        self.assertEqual(status, 400)
        after = json.loads((self.root / "devices.json").read_text(encoding="utf-8"))
        self.assertEqual(before, after)

    def test_devices_api_handles_invalid_registry_without_path_leak(self):
        (self.root / "devices.json").write_text(
            '{"devices":[{"id":"../escape"}]}',
            encoding="utf-8",
        )

        status, body = self.request("/api/devices")

        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "Device registry is unavailable",
            },
        )
        self.assertNotIn(
            str(self.root).encode("utf-8"),
            body,
        )

    def test_settings_devices_tab_lists_and_selects_default_device(self):
        status, body = self.request("/settings")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn('data-tab="devices">Devices</button>', text)
        self.assertIn('class="card tab-content" id="devices"', text)
        self.assertIn('id="selected-device"', text)
        self.assertIn('value="default-kindle"', text)
        self.assertIn('data-device-id="default-kindle"', text)
        self.assertIn("Default Kindle", text)
        self.assertIn("kindle_pw1", text)
        self.assertIn("758×1024", text)
        self.assertIn("/device/default-kindle/image.png", text)
        self.assertIn("Add Device", text)
        self.assertIn("add-device-wizard", text)
        self.assertIn("Kindle", text)
        self.assertIn("ESP32 e-paper", text)
        self.assertIn(
            "/api/device/default-kindle/config",
            text,
        )
        self.assertIn(
            "kindle_dashboard_selected_device",
            text,
        )
        self.assertNotIn("/home/user/.ssh", text)
        self.assertNotIn("known_hosts", text)

    def test_settings_page_survives_invalid_device_registry(self):
        (self.root / "devices.json").write_text(
            '{"devices":"invalid"}',
            encoding="utf-8",
        )

        status, body = self.request("/settings")

        self.assertEqual(status, 200)
        self.assertIn(
            "Device registry is currently unavailable.",
            body.decode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()

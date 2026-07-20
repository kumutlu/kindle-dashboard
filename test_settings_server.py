#!/usr/bin/env python3
import base64
import html
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
import special_events
import weather_image
from device_registry import DeviceRegistry


TEST_PNG_DATA_URL = (
    "data:image/png;base64,"
    + base64.b64encode(
        (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00"
            b"\x3a\x7e\x9b\x55\x00\x00\x00\x0bIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01"
            b"\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    ).decode("ascii")
)


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

    def test_unknown_persisted_theme_falls_back_to_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard_config.json"
            config = dict(weather_image.DEFAULT_CONFIG, theme="unknown-theme")
            path.write_text(json.dumps(config), encoding="utf-8")

            loaded = weather_image.load_config(path)

        self.assertEqual(loaded["theme"], weather_image.DEFAULT_CONFIG["theme"])

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
        self.subprocess_patcher = mock.patch("subprocess.run")
        self.mock_run = self.subprocess_patcher.start()
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        self.mock_run.return_value = mock_result

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.subprocess_patcher.stop()
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

    def post_json(self, path, payload, headers=None):
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        return self.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers=req_headers,
        )

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
        self.assertIn('data-settings-action="push">Refresh Now</button>', text)
        self.assertNotIn('data-device-action="refresh">Refresh Now</button>', text)
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
        rendered_values = set(re.findall(
            r'type="radio" name="theme" value="([^"]+)"',
            text,
        ))
        self.assertEqual(rendered_values, set(settings_server.THEMES))
        for value in (
            "home_dashboard",
            "minimal_weather",
            "server_monitor",
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
        self.assertNotIn('name="theme" value="travel_weather"', text)
        self.assertNotIn('name="theme" value="compact_dashboard"', text)

    def test_todo_theme_ui_has_selected_device_task_management(self):
        status, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('name="theme" value="todo"', text)
        for marker in (
            'id="todo-manager"',
            'id="todo-add-form"',
            'id="todo-title"',
            'id="todo-incomplete-list"',
            'id="todo-completed-list"',
            'data-todo-drag-handle',
            "loadTodoTasks(selected)",
            "/tasks/reorder",
        ):
            self.assertIn(marker, text)

    def test_device_task_api_crud_toggle_reorder_and_csrf(self):
        status, _, body = self.post_json(
            "/api/device/default-kindle/tasks", {"title": "Denied"}
        )
        self.assertEqual(status, 403, body)

        csrf = self.csrf_token()
        first_status, _, first_body = self.post_json(
            "/api/device/default-kindle/tasks",
            {"title": "First"},
            headers={"X-CSRF-Token": csrf},
        )
        second_status, _, second_body = self.post_json(
            "/api/device/default-kindle/tasks",
            {"title": "Second"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual((first_status, second_status), (201, 201))
        first = json.loads(first_body)["task"]
        second = json.loads(second_body)["task"]

        status, _, body = self.request(
            "PUT",
            f'/api/device/default-kindle/tasks/{first["id"]}',
            body=json.dumps({"title": "First edited", "completed": True}),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["task"]["completed"])

        status, _, body = self.request(
            "PUT",
            f'/api/device/default-kindle/tasks/{first["id"]}',
            body=json.dumps({"completed": False}),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        self.assertEqual(status, 200, body)

        status, _, body = self.request(
            "PUT",
            "/api/device/default-kindle/tasks/reorder",
            body=json.dumps({
                "completed": False,
                "task_ids": [first["id"], second["id"]],
            }),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(
            [task["title"] for task in json.loads(body)["tasks"]],
            ["First edited", "Second"],
        )

        status, _, body = self.request(
            "DELETE",
            f'/api/device/default-kindle/tasks/{second["id"]}',
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200, body)
        status, _, body = self.request(
            "GET", "/api/device/default-kindle/tasks"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [task["title"] for task in json.loads(body)["tasks"]],
            ["First edited"],
        )

    def test_task_api_is_device_isolated_and_renders_only_todo_devices(self):
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
        kitchen_config["theme"] = "todo"
        settings_server.atomic_write_config(kitchen.config_path, kitchen_config)
        csrf = self.csrf_token()

        status, _, _ = self.post_json(
            "/api/device/default-kindle/tasks",
            {"title": "Default task"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 201)
        self.assertEqual(self.rendered_device_ids, [])

        status, _, _ = self.post_json(
            "/api/device/kitchen-kindle/tasks",
            {"title": "Kitchen task"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 201)
        self.assertEqual(self.rendered_device_ids, ["kitchen-kindle"])

        _, _, default_body = self.request(
            "GET", "/api/device/default-kindle/tasks"
        )
        _, _, kitchen_body = self.request(
            "GET", "/api/device/kitchen-kindle/tasks"
        )
        self.assertEqual(
            json.loads(default_body)["tasks"][0]["title"], "Default task"
        )
        self.assertEqual(
            json.loads(kitchen_body)["tasks"][0]["title"], "Kitchen task"
        )

        status, _, _ = self.request("GET", "/api/device/missing/tasks")
        self.assertEqual(status, 404)

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
            "home_dashboard",
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
            "theme": "home_dashboard",
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
        self.assertEqual(saved["theme"], "home_dashboard")
        self.assertEqual(saved["weather_query"], "London")
        self.assertEqual(saved["refresh_interval_minutes"], 30)
        self.assertEqual(
            self.rendered_device_ids[-1],
            "kitchen-kindle",
        )
        self.assertEqual(
            self.rendered_device_themes[-1],
            "home_dashboard",
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

    def test_selected_device_config_is_applied_to_theme_form(self):
        _, _, body = self.request("GET", "/settings")
        text = body.decode("utf-8")

        self.assertIn("function applyDeviceConfigToForm", text)
        self.assertIn('querySelector(`input[name="theme"][value="${config.theme}"]`)', text)
        self.assertIn("applyDeviceConfigToForm(configData)", text)
        for name in (
            "title",
            "location",
            "country",
            "latitude",
            "longitude",
            "location_display",
            "weather_query",
            "location_label",
            "timezone",
        ):
            self.assertIn(f'"{name}"', text)
        self.assertIn('querySelector(`[name="${name}"]`)', text)

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
        with mock.patch("settings_server.render_device") as mock_render_device:
            self.mock_run.reset_mock()
            status, _, body = self.request(
                "POST",
                "/api/device/refresh",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(
            json.loads(body.decode("utf-8"))["message"],
            "Dashboard generated and pushed",
        )
        mock_render_device.assert_called_once_with(
            "default-kindle",
            force=True,
            registry=self.registry,
        )
        self.assertNotIn(("action", "refresh"), self.device_calls)
        self.assertEqual(self.mock_run.call_count, 2)

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

    @mock.patch("settings_server.render_device")
    def test_push_named_device_renders_copies_and_displays_image(self, mock_render_device):
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
        mock_render_device.assert_called_once_with(
            "kitchen-kindle",
            force=True,
            registry=self.registry,
        )
        self.assertNotIn(("push", "kitchen-kindle"), self.device_calls)
        self.assertEqual(self.mock_run.call_count, 2)
        scp_args = self.mock_run.call_args_list[0].args[0]
        ssh_args = self.mock_run.call_args_list[1].args[0]
        self.assertEqual(scp_args[:3], ["scp", "-i", "/home/user/.ssh/kindle_dashboard_ed25519"])
        self.assertIn("-o", scp_args)
        self.assertIn("IdentitiesOnly=yes", scp_args)
        self.assertEqual(
            scp_args[-1],
            "root@192.168.68.150:/mnt/us/dashboard/image.png",
        )
        self.assertEqual(ssh_args[:3], ["ssh", "-i", "/home/user/.ssh/kindle_dashboard_ed25519"])
        self.assertEqual(ssh_args[-2], "root@192.168.68.150")
        self.assertIn("/usr/sbin/eips -c", ssh_args[-1])
        self.assertIn("/usr/sbin/eips -g /mnt/us/dashboard/image.png", ssh_args[-1])
        self.assertNotIn("apply-screensaver-overlay.sh", ssh_args[-1])

    @mock.patch("settings_server.render_device")
    def test_refresh_now_endpoint_uses_selected_device_push_path(self, mock_render_device):
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
            "/api/device/kitchen-kindle/refresh",
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["message"], "Dashboard generated and pushed")
        mock_render_device.assert_called_once_with(
            "kitchen-kindle",
            force=True,
            registry=self.registry,
        )
        self.assertNotIn(("action", "refresh", "kitchen-kindle"), self.device_calls)
        self.assertEqual(self.mock_run.call_count, 2)
        self.assertEqual(self.mock_run.call_args_list[0].args[0][0], "scp")
        self.assertEqual(self.mock_run.call_args_list[1].args[0][0], "ssh")
        self.assertIn("/usr/sbin/eips -g /mnt/us/dashboard/image.png", self.mock_run.call_args_list[1].args[0][-1])

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

    def test_kindle_kt4_light_get_set_and_saved_default(self):
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
                "port": 22,
            },
        })
        kt4_config = dict(weather_image.DEFAULT_CONFIG)
        kt4_config.update({
            "theme": "minimal_weather",
            "kindle_frontlight": 4,
        })
        settings_server.atomic_write_config(kt4.config_path, kt4_config)

        status, _, body = self.request("GET", "/api/device/kindle-131/light")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["brightness"], 8)
        self.assertIn(("get_light", "kindle-131"), self.device_calls)

        token = self.csrf_token()
        status, _, body = self.request(
            "POST",
            "/api/device/kindle-131/light",
            body=json.dumps({"level": 12}),
            headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["brightness"], 12)
        self.assertIn(("set_light", 12, "kindle-131"), self.device_calls)

        saved = json.loads(kt4.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["kindle_frontlight"], 12)

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
        self.mock_device = mock.MagicMock()
        self.mock_device.push.return_value = "generated and pushed"
        self.mock_device.run_action.return_value = "action completed"
        self.mock_device.set_light.return_value = 12
        self.server = settings_server.make_server(
            host="127.0.0.1",
            port=0,
            config_path=self.config_path,
            regenerate=lambda: None,
            render_selected=self.rendered_device_ids.append,
            device=self.mock_device,
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

    def post_form(self, path, form):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        connection.request(
            "POST",
            path,
            body=urlencode(form),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        body = response.read()
        response_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, response_headers, body

    def csrf_token(self):
        status, body = self.request("/settings")
        self.assertEqual(status, 200)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            body,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

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
                "enabled",
                "title",
                "location",
                "country",
                "latitude",
                "longitude",
                "location_display",
                "location_label",
                "weather_query",
                "timezone",
                "theme",
                "show_weather",
                "show_forecast",
                "show_server",
                "show_pihole",
                "show_tailscale",
                "refresh_interval_minutes",
                "wifi_power_save",
                "update_only_if_changed",
                "kindle_frontlight",
                "prayer_method",
                "prayer_school",
                "prayer_high_latitude",
                "hijri_adjustment",
                "image_url",
            },
        )
        self.assertEqual(payload["device_id"], "default-kindle")
        self.assertEqual(payload["type"], "kindle_pw1")
        self.assertEqual(payload["resolution"], [758, 1024])
        self.assertEqual(payload["theme"], "family_dashboard")
        self.assertEqual(payload["refresh_interval_minutes"], 30)
        self.assertEqual(payload["wifi_power_save"], True)
        self.assertEqual(payload["update_only_if_changed"], True)
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

    def test_device_config_endpoint_returns_safe_persistent_settings(self):
        kitchen = self.registry.add({
            "id": "kitchen",
            "name": "Kitchen",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen/config.json",
            "image_path": "devices/kitchen/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            },
        })
        config = dict(weather_image.DEFAULT_CONFIG)
        config.update({
            "title": "KITCHEN",
            "location": "Istanbul",
            "country": "Türkiye",
            "latitude": 41.0082,
            "longitude": 28.9784,
            "location_display": "Istanbul, Türkiye",
            "location_label": "Istanbul, Türkiye",
            "weather_query": "Istanbul",
            "timezone": "Europe/Istanbul",
            "theme": "minimal_weather",
            "refresh_interval_minutes": 30,
            "wifi_power_save": False,
            "update_only_if_changed": True,
            "kindle_frontlight": 4,
            "status_token": "secret-status-token",
            "pairing_token": "secret-pairing-token",
        })
        kitchen.config_path.write_text(
            json.dumps(config),
            encoding="utf-8",
        )

        status, body = self.request("/api/device/kitchen/config")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        for key in (
            "title",
            "location",
            "country",
            "latitude",
            "longitude",
            "location_display",
            "location_label",
            "weather_query",
            "timezone",
            "theme",
            "refresh_interval_minutes",
            "wifi_power_save",
            "update_only_if_changed",
            "kindle_frontlight",
        ):
            self.assertEqual(payload[key], config[key])
        self.assertNotIn("status_token", payload)
        self.assertNotIn("pairing_token", payload)

    def test_kitchen_save_persists_only_kitchen_config(self):
        kitchen = self.registry.add({
            "id": "kitchen",
            "name": "Kitchen",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen/config.json",
            "image_path": "devices/kitchen/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
            },
        })
        default_before = json.loads(
            self.registry.get("default-kindle").config_path.read_text(
                encoding="utf-8",
            )
        )
        csrf = self.csrf_token()
        form = {
            "csrf_token": csrf,
            "selected_device_id": "kitchen",
            "title": "KITCHEN",
            "location": "Istanbul",
            "country": "Türkiye",
            "latitude": "41.0082",
            "longitude": "28.9784",
            "location_display": "Istanbul, Türkiye",
            "location_label": "Istanbul, Türkiye",
            "weather_query": "Istanbul",
            "timezone": "Europe/Istanbul",
            "theme": "minimal_weather",
            "show_weather": "on",
            "show_forecast": "on",
            "refresh_interval_minutes": "30",
            "update_only_if_changed": "on",
        }

        status, headers, _ = self.post_form(
            "/settings",
            form,
        )

        self.assertEqual(status, 303)
        saved = json.loads(kitchen.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["theme"], "minimal_weather")
        self.assertEqual(saved["weather_query"], "Istanbul")
        self.assertEqual(saved["timezone"], "Europe/Istanbul")
        self.assertFalse(saved["wifi_power_save"])
        self.assertTrue(saved["update_only_if_changed"])
        default_after = json.loads(
            self.registry.get("default-kindle").config_path.read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(default_after, default_before)

    def test_kindle_kt4_saves_and_reloads_every_canonical_theme(self):
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
        kt4_config = dict(weather_image.DEFAULT_CONFIG)
        kt4_config["theme"] = "minimal_weather"
        settings_server.atomic_write_config(kt4.config_path, kt4_config)
        csrf = self.csrf_token()

        base_form = {
            "csrf_token": csrf,
            "selected_device_id": "kindle-131",
            "title": "KINDLE 131",
            "location": "Nottingham",
            "country": "United Kingdom",
            "latitude": "52.9536",
            "longitude": "-1.1505",
            "location_display": "Nottingham, England, United Kingdom",
            "location_label": "Nottingham, UK",
            "weather_query": "Nottingham",
            "timezone": "Europe/London",
            "show_weather": "on",
            "show_forecast": "on",
            "refresh_interval_minutes": "60",
        }

        other_before = self.registry.get(
            "default-kindle"
        ).config_path.read_bytes()
        legacy_before = self.config_path.read_bytes()

        for theme in settings_server.THEMES:
            with self.subTest(theme=theme):
                form = dict(base_form, theme=theme)
                status, headers, _ = self.post_form("/settings", form)
                self.assertEqual(status, 303)
                self.assertEqual(headers["Location"], "/settings?status=saved")
                saved = json.loads(
                    kt4.config_path.read_text(encoding="utf-8")
                )
                self.assertEqual(saved["theme"], theme)

                status, body = self.request(
                    "/api/device/kindle-131/config"
                )
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["theme"], theme)
                self.assertEqual(self.rendered_device_ids[-1], kt4.id)

        self.assertEqual(
            self.registry.get("default-kindle").config_path.read_bytes(),
            other_before,
        )
        self.assertEqual(self.config_path.read_bytes(), legacy_before)

    def test_kindle_kt4_deprecated_themes_persist_as_canonical_values(self):
        kt4 = self.registry.add({
            "id": "kindle-131",
            "name": "Kindle 131",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-131/config.json",
            "image_path": "devices/kindle-131/image.png",
        })
        base = dict(weather_image.DEFAULT_CONFIG)
        aliases = {
            "travel_weather": "minimal_weather",
            "compact_dashboard": "home_dashboard",
        }

        for deprecated, canonical in aliases.items():
            with self.subTest(theme=deprecated):
                candidate = dict(base, theme=deprecated)
                status, _, body = self.post_json(
                    "/api/config",
                    {
                        "selected_device_id": kt4.id,
                        "config": candidate,
                    },
                )
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertEqual(payload["config"]["theme"], canonical)
                self.assertEqual(
                    json.loads(
                        kt4.config_path.read_text(encoding="utf-8")
                    )["theme"],
                    canonical,
                )
                status, body = self.request(
                    "/api/device/kindle-131/config"
                )
                self.assertEqual(json.loads(body)["theme"], canonical)

    def test_kindle_kt4_unknown_theme_keeps_persisted_theme(self):
        kt4 = self.registry.add({
            "id": "kindle-131",
            "name": "Kindle 131",
            "type": "kindle_kt4",
            "resolution": [600, 800],
            "enabled": True,
            "config_path": "devices/kindle-131/config.json",
            "image_path": "devices/kindle-131/image.png",
        })
        original = dict(
            weather_image.DEFAULT_CONFIG,
            theme="maarif_calendar",
        )
        settings_server.atomic_write_config(kt4.config_path, original)

        status, _, body = self.post_json(
            "/api/config",
            {
                "selected_device_id": kt4.id,
                "config": dict(original, theme="unknown-theme"),
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("unsupported theme", json.loads(body)["error"])
        self.assertEqual(
            json.loads(kt4.config_path.read_text(encoding="utf-8"))["theme"],
            "maarif_calendar",
        )

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
        self.assertIn("/api/device/kitchen-kindle/status", script)
        self.assertIn("status.sh", script)
        self.assertIn("refresh.sh", script)
        self.assertIn("start.sh", script)
        self.assertIn("dashboard_loop.sh", script)
        self.assertIn("watchdog.sh", script)
        self.assertIn("stop.sh", script)
        self.assertIn("STATUS_URL=", script)
        self.assertIn("CONFIG_URL=", script)
        self.assertIn("IMAGE_URL=", script)
        self.assertIn(
            "http://127.0.0.1:8765/device/kitchen-kindle/image.png",
            script,
        )
        self.assertNotIn(
            "http://127.0.0.1:8767/device/kitchen-kindle/image.png",
            script,
        )
        self.assertIn("Authorization: Bearer", script)
        # Verify REFRESH_INTERVAL_MINUTES is written to device.env
        self.assertIn('REFRESH_INTERVAL_MINUTES="30"', script)
        self.assertIn('WIFI_POWER_SAVE="1"', script)
        self.assertIn('UPDATE_ONLY_IF_CHANGED="1"', script)
        self.assertIn("refresh-once.sh", script)
        # Verify BusyBox-compatible syntax and chmod executions
        self.assertIn('chmod +x "$DASHBOARD_DIR/status.sh"', script)
        self.assertIn('cat <<\'EOF\' > "$DASHBOARD_DIR/status.sh"', script)
        # Verify upstart config creation
        self.assertIn('/etc/upstart/dashboard.conf', script)
        self.assertIn('mntroot rw', script)
        self.assertIn('mntroot ro', script)
        # Verify wlan0 IP preference, lipc battery level fallback, and prettyversion.txt firmware version extraction
        self.assertIn("ifconfig wlan0", script)
        self.assertIn("lipc-get-prop", script)
        self.assertIn("battLevel", script)
        self.assertIn("awk '{print $1}'", script)
        self.assertIn("/etc/prettyversion.txt", script)
        self.assertIn("firmware_version", script)
        self.assertNotIn("] && command -v", script)

    def test_installer_refresh_uses_absolute_eips_without_path_lookup(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        created = json.loads(body)

        status, body = self.request(
            "/install/kindle/kitchen-kindle?token="
            + created["pairing_token"]
        )
        script = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn('EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"', script)
        self.assertIn('"$EIPS_BIN" -g "$IMG"', script)
        self.assertNotIn("command -v eips", script)
        self.assertNotIn("\neips ", script)

    def test_installer_contains_idempotent_multi_device_layout(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        created = json.loads(body)

        status, body = self.request(
            "/install/kindle/kitchen-kindle?token="
            + created["pairing_token"]
        )
        script = body.decode("utf-8")

        self.assertEqual(status, 200)
        for path in (
            "device.env",
            "device-id",
            "status-token",
            "status.sh",
            "refresh.sh",
            "start.sh",
            "stop.sh",
            "dashboard_loop.sh",
        ):
            self.assertIn(path, script)
        self.assertIn('mkdir -p "$DASHBOARD_DIR"', script)
        self.assertIn('cat <<', script)

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
        self.assertIn('class="btn-regenerate-installer"', text)
        self.assertIn('class="installer-command-wrap"', text)
        self.assertIn('class="regenerated-installer-command"', text)
        self.assertNotIn("/home/user/.ssh", text)
        self.assertNotIn("known_hosts", text)

    def test_settings_html_does_not_embed_device_status_tokens(self):
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        created = json.loads(body)

        status, body = self.request("/settings")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertNotIn(created["status_token"], text)
        self.assertNotIn("deviceStatusTokens", text)
        self.assertNotIn("X-Device-Token", text)

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

    def test_installer_token_reset_endpoint_and_token_persistence(self):
        # 1. Create a Kindle device
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Reset Test Kindle",
                "theme": "home_dashboard",
            },
        )
        self.assertEqual(status, 201)
        created = json.loads(body)
        device_id = created["device"]["device_id"]
        old_pairing_token = created["pairing_token"]
        old_status_token = created["status_token"]

        # 2. Simulate pairing token deletion (e.g. status after successful pair)
        device = self.registry.get(device_id)
        from settings_server import read_raw_device_config, atomic_write_bytes
        config = read_raw_device_config(device)
        self.assertIn("pairing_token", config)
        config.pop("pairing_token")
        
        # Write it back without pairing_token
        atomic_write_bytes(
            device.config_path,
            (json.dumps(config, indent=2) + "\n").encode("utf-8")
        )

        # Verify old pairing token is now invalid / forbidden
        status, _ = self.request(f"/install/kindle/{device_id}?token={old_pairing_token}")
        self.assertEqual(status, 403)

        # Retrieve CSRF token
        status, settings_body = self.request("/settings")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            settings_body,
        )
        self.assertIsNotNone(match)
        csrf = match.group(1).decode("ascii")

        # 3. Call reset endpoint to generate new token
        status, _, body = self.post_json(
            f"/api/device/{device_id}/installer-token/reset",
            {},
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        new_token = payload["pairing_token"]
        self.assertNotEqual(new_token, old_pairing_token)
        self.assertIn("install_command", payload)
        self.assertIn(new_token, payload["install_command"])

        # 4. Verify status_token and other fields are preserved
        config = read_raw_device_config(device)
        self.assertEqual(config.get("status_token"), old_status_token)
        self.assertEqual(device.name, "Reset Test Kindle")
        self.assertEqual(config.get("pairing_token"), new_token)

        # 5. Verify public config/API does not expose pairing_token/status_token
        status, body = self.request(f"/api/device/{device_id}/config")
        self.assertEqual(status, 200)
        pub_config = json.loads(body)
        self.assertNotIn("pairing_token", pub_config)
        self.assertNotIn("status_token", pub_config)

        # 6. Verify that the new pairing token successfully fetches installer script
        status, body = self.request(f"/install/kindle/{device_id}?token={new_token}")
        self.assertEqual(status, 200)
        script = body.decode("utf-8")
        self.assertIn("status.sh", script)

        # 7. Verify token persistence: save settings via forms and ensure tokens are not stripped
        from settings_server import atomic_write_config
        config["theme"] = "family_dashboard"
        atomic_write_config(device.config_path, config)
        
        # Reload and check
        config_after = read_raw_device_config(device)
        self.assertEqual(config_after.get("pairing_token"), new_token)
        self.assertEqual(config_after.get("status_token"), old_status_token)
        self.assertEqual(config_after.get("theme"), "family_dashboard")

    def test_installer_generates_status_token_if_missing(self):
        # 1. Create a Kindle device
        status, _, body = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Missing Status Token Kindle",
                "theme": "home_dashboard",
            },
        )
        self.assertEqual(status, 201)
        created = json.loads(body)
        device_id = created["device"]["device_id"]
        pairing_token = created["pairing_token"]

        # 2. Simulate status_token missing but pairing_token present
        device = self.registry.get(device_id)
        from settings_server import read_raw_device_config, atomic_write_bytes
        config = read_raw_device_config(device)
        self.assertIn("status_token", config)
        config.pop("status_token")
        
        # Write it back without status_token
        atomic_write_bytes(
            device.config_path,
            (json.dumps(config, indent=2) + "\n").encode("utf-8")
        )

        # 3. Request installer script using pairing_token
        status, body = self.request(f"/install/kindle/{device_id}?token={pairing_token}")
        self.assertEqual(status, 200)
        script = body.decode("utf-8")
        
        # Verify installer script contains status.sh, refresh.sh, start.sh
        self.assertIn("status.sh", script)
        self.assertIn("refresh.sh", script)
        self.assertIn("start.sh", script)
        
        # Verify STATUS_TOKEN in script is non-empty
        self.assertIn('STATUS_TOKEN="', script)
        self.assertNotIn('STATUS_TOKEN=""', script)

        # 4. Verify status_token is now saved and present in the device config file
        config_after = read_raw_device_config(device)
        self.assertIn("status_token", config_after)
        self.assertTrue(config_after["status_token"])
        self.assertEqual(config_after["pairing_token"], pairing_token)

        # 5. Reset endpoint generates both pairing_token and status_token if status_token is missing
        # Delete status_token and pairing_token
        config_after.pop("status_token")
        config_after.pop("pairing_token")
        atomic_write_bytes(
            device.config_path,
            (json.dumps(config_after, indent=2) + "\n").encode("utf-8")
        )

        # Retrieve CSRF token
        status, settings_body = self.request("/settings")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            settings_body,
        )
        self.assertIsNotNone(match)
        csrf = match.group(1).decode("ascii")

        # Call reset endpoint
        status, _, body = self.post_json(
            f"/api/device/{device_id}/installer-token/reset",
            {},
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        new_pairing_token = payload["pairing_token"]

        # Verify device config contains both pairing_token and status_token
        config_final = read_raw_device_config(device)
        self.assertIn("pairing_token", config_final)
        self.assertEqual(config_final.get("pairing_token"), new_pairing_token)
        self.assertIn("status_token", config_final)
        self.assertTrue(config_final["status_token"])

    def test_multiple_devices_flows(self):
        # 1. Create kitchen device
        status_k, _, body_k = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        self.assertEqual(status_k, 201)
        res_k = json.loads(body_k)
        dev_k = res_k["device"]
        id_k = dev_k["id"]
        
        # 2. Create bedroom device
        status_b, _, body_b = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Bedroom Kindle",
                "theme": "minimal_weather",
            },
        )
        self.assertEqual(status_b, 201)
        res_b = json.loads(body_b)
        dev_b = res_b["device"]
        id_b = dev_b["id"]

        # 3. Verify two devices exist and have distinct IDs
        self.assertEqual(id_k, "kitchen-kindle")
        self.assertEqual(id_b, "bedroom-kindle")
        self.assertNotEqual(id_k, id_b)
        
        # 4. Verify tokens are unique
        self.assertNotEqual(res_k["pairing_token"], res_b["pairing_token"])
        self.assertNotEqual(res_k["status_token"], res_b["status_token"])

        # 5. Verify image URLs are different
        self.assertEqual(dev_k["image_url"], f"/device/{id_k}/image.png")
        self.assertEqual(dev_b["image_url"], f"/device/{id_b}/image.png")
        self.assertNotEqual(dev_k["image_url"], dev_b["image_url"])

        # 6. Verify status files are separate
        status_file_k = self.root / f"devices/{id_k}/status.json"
        status_file_b = self.root / f"devices/{id_b}/status.json"
        self.assertTrue(status_file_k.exists())
        self.assertTrue(status_file_b.exists())

        # 7. Verify installer command is device-specific
        self.assertIn(id_k, res_k["install_command"])
        self.assertIn(res_k["pairing_token"], res_k["install_command"])
        self.assertIn(id_b, res_b["install_command"])
        self.assertIn(res_b["pairing_token"], res_b["install_command"])

        # 8. Check that the config files exist and have the correct unique titles
        from settings_server import read_raw_device_config
        record_k = self.registry.get(id_k)
        record_b = self.registry.get(id_b)
        config_k = read_raw_device_config(record_k)
        config_b = read_raw_device_config(record_b)
        self.assertEqual(config_k["title"], "KITCHEN KINDLE")
        self.assertEqual(config_b["title"], "BEDROOM KINDLE")
        self.assertEqual(config_k["theme"], "home_dashboard")
        self.assertEqual(config_b["theme"], "minimal_weather")

        # 9. Verify web UI lists both devices and their unique installer commands
        status, body = self.request("/settings")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        
        # Verify both device names are listed
        self.assertIn("Kitchen Kindle", text)
        self.assertIn("Bedroom Kindle", text)
        
        # Verify both installer commands are printed on their respective cards
        self.assertIn(html.escape(res_k["install_command"]), text)
        self.assertIn(html.escape(res_b["install_command"]), text)

        # 10. Render both devices and verify different PNGs are generated containing screen labels
        from PIL import Image, ImageDraw
        rendered_titles = []
        def fake_renderer(config):
            rendered_titles.append(config["title"])
            img = Image.new("L", (758, 1024), 255)
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), config["title"])
            img.save(weather_image.ACTIVE_OUTPUT.get())

        with mock.patch.dict(
            weather_image.THEME_RENDERERS,
            {
                "home_dashboard": fake_renderer,
                "minimal_weather": fake_renderer,
            },
            clear=True,
        ):
            res_render_k = weather_image.render_device(id_k, registry=self.registry)
            res_render_b = weather_image.render_device(id_b, registry=self.registry)

        self.assertEqual(rendered_titles, ["KITCHEN KINDLE", "BEDROOM KINDLE"])
        path_k = Path(res_render_k["output_path"])
        path_b = Path(res_render_b["output_path"])
        self.assertTrue(path_k.exists())
        self.assertTrue(path_b.exists())
        
        content_k = path_k.read_bytes()
        content_b = path_b.read_bytes()
        self.assertNotEqual(content_k, content_b)

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_push_requires_csrf_and_rejects_status_token(self, mock_render_device, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        status_k, _, body_k = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        self.assertEqual(status_k, 201)
        res_k = json.loads(body_k)
        id_k = res_k["device"]["id"]
        token_k = res_k["status_token"]

        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("POST", f"/api/device/{id_k}/push", headers={"X-Device-Token": token_k})
        response = conn.getresponse()
        status = response.status
        response.read()
        conn.close()
        self.assertEqual(status, 403)

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/device/default-kindle/push",
            {},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body.decode("utf-8")),
            {"ok": True, "message": "Dashboard generated and pushed"},
        )
        mock_render_device.assert_called_once_with(
            "default-kindle",
            force=True,
            registry=self.registry,
        )
        self.mock_device.push.assert_not_called()

    def test_push_with_invalid_csrf_fails(self):
        status_k, _, body_k = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        self.assertEqual(status_k, 201)
        res_k = json.loads(body_k)
        id_k = res_k["device"]["id"]

        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("POST", f"/api/device/{id_k}/push", headers={"X-CSRF-Token": "invalid-token-12345"})
        response = conn.getresponse()
        status = response.status
        body = response.read()
        conn.close()
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body.decode("utf-8"))["error"], "invalid request token")

    def test_status_token_only_authenticates_status_endpoint(self):
        status_k, _, body_k = self.post_json(
            "/api/devices",
            {
                "type": "kindle_pw1",
                "name": "Kitchen Kindle",
                "theme": "home_dashboard",
            },
        )
        res_k = json.loads(body_k)
        id_k = res_k["device"]["id"]
        token_k = res_k["status_token"]

        status, _, body = self.post_json(
            f"/api/device/{id_k}/status",
            {"battery_percent": 83},
            headers={"X-Device-Token": token_k},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode("utf-8"))["ok"])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_push_endpoint_uses_direct_render_scp_and_absolute_eips(self, mock_render_device, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/device/default-kindle/push",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(status, 200)
        mock_render_device.assert_called_once_with(
            "default-kindle",
            force=True,
            registry=self.registry,
        )
        self.mock_device.push.assert_not_called()
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(mock_run.call_args_list[0].args[0][0], "scp")
        self.assertIn("/home/user/.ssh/kindle_dashboard_ed25519", mock_run.call_args_list[0].args[0])
        self.assertIn("root@192.168.68.119:/mnt/us/dashboard/image.png", mock_run.call_args_list[0].args[0])
        self.assertEqual(mock_run.call_args_list[1].args[0][0], "ssh")
        self.assertIn("root@192.168.68.119", mock_run.call_args_list[1].args[0])
        self.assertIn("/usr/sbin/eips -g /mnt/us/dashboard/image.png", mock_run.call_args_list[1].args[0][-1])
        self.assertNotIn("apply-screensaver-overlay.sh", mock_run.call_args_list[1].args[0][-1])

    def test_special_events_push_button_uses_relative_special_event_api(self):
        status, body = self.request("/settings")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn('id="btn-push-all-special"', text)
        self.assertIn('/api/special-events/${encodeURIComponent(selectedSpecialEventId)}/push-all', text)
        self.assertNotIn(":8765/api/special-events", text)

    def test_special_event_crud_round_trip(self):
        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/special-events",
            {
                "title": "Happy Test Day",
                "start_date": "2026-07-10",
                "end_date": "2026-07-12",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle"],
                "enabled": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200, body)
        self.assertTrue(payload["ok"])
        event_id = payload["event"]["id"]
        self.assertEqual(payload["event"]["devices"], ["default-kindle"])
        self.assertTrue((self.registry.project_root / payload["event"]["image_path"]).exists())

        status, body = self.request("/api/special-events")
        listed = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["events"]), 1)
        self.assertEqual(listed["events"][0]["id"], event_id)

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(
            "PUT",
            f"/api/special-events/{event_id}",
            body=json.dumps({
                "title": "Updated Test Day",
                "start_date": "2026-07-11",
                "end_date": "2026-07-13",
                "devices": ["default-kindle"],
                "enabled": False,
            }),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        response = connection.getresponse()
        body = response.read()
        status = response.status
        connection.close()
        updated = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200, body)
        self.assertEqual(updated["event"]["title"], "Updated Test Day")
        self.assertFalse(updated["event"]["enabled"])

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(
            "DELETE",
            f"/api/special-events/{event_id}",
            headers={"X-CSRF-Token": csrf},
        )
        response = connection.getresponse()
        body = response.read()
        status = response.status
        connection.close()
        self.assertEqual(status, 200, body)
        status, body = self.request("/api/special-events")
        listed = json.loads(body.decode("utf-8"))
        self.assertEqual(listed["events"], [])

    @mock.patch("settings_server.push_image_to_kindle")
    @mock.patch("settings_server.render_special_event_for_device")
    def test_special_event_push_uses_selected_event_image(self, mock_render_special, mock_push):
        csrf = self.csrf_token()
        created = special_events.create_event(
            self.registry.project_root,
            {
                "title": "Event Push",
                "start_date": "2026-07-10",
                "end_date": "2026-07-10",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle"],
                "enabled": True,
            },
            ["default-kindle"],
        )
        special_events.save_events(self.registry.project_root, [created])
        mock_render_special.return_value = self.registry.project_root / "cache" / "special.png"

        status, _, body = self.post_json(
            f"/api/special-events/{created.id}/push",
            {"device_id": "default-kindle"},
            headers={"X-CSRF-Token": csrf},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200, body)
        self.assertTrue(payload["ok"])
        mock_render_special.assert_called_once()
        pushed_device = mock_push.call_args.args[0]
        self.assertEqual(pushed_device.id, "default-kindle")
        self.assertEqual(mock_push.call_args.args[1], self.registry.project_root / "cache" / "special.png")

    @mock.patch("settings_server.push_image_to_kindle")
    @mock.patch("settings_server.render_special_event_for_device")
    def test_special_event_push_all_respects_target_devices_and_partial_failures(self, mock_render_special, mock_push):
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
        })
        created = special_events.create_event(
            self.registry.project_root,
            {
                "title": "Targeted Push",
                "start_date": "2026-07-10",
                "end_date": "2026-07-10",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle", "kitchen-kindle"],
                "enabled": True,
            },
            ["default-kindle", "kitchen-kindle"],
        )
        special_events.save_events(self.registry.project_root, [created])
        mock_render_special.side_effect = [
            self.registry.project_root / "cache" / "default.png",
            self.registry.project_root / "cache" / "kitchen.png",
        ]
        mock_push.side_effect = [None, RuntimeError("No route to host")]
        csrf = self.csrf_token()

        status, _, body = self.post_json(
            f"/api/special-events/{created.id}/push-all",
            {},
            headers={"X-CSRF-Token": csrf},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200, body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["pushed"], ["Default Kindle"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("Kitchen Kindle", payload["errors"][0])

    @mock.patch("settings_server.push_image_to_kindle")
    @mock.patch("settings_server.render_special_event_for_device")
    def test_special_event_push_all_complete_failure(self, mock_render_special, mock_push):
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
        })
        created = special_events.create_event(
            self.registry.project_root,
            {
                "title": "Targeted Push",
                "start_date": "2026-07-10",
                "end_date": "2026-07-10",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle", "kitchen-kindle"],
                "enabled": True,
            },
            ["default-kindle", "kitchen-kindle"],
        )
        special_events.save_events(self.registry.project_root, [created])
        mock_render_special.side_effect = [
            self.registry.project_root / "cache" / "default.png",
            self.registry.project_root / "cache" / "kitchen.png",
        ]
        mock_push.side_effect = [RuntimeError("offline"), RuntimeError("No route to host")]
        csrf = self.csrf_token()

        status, _, body = self.post_json(
            f"/api/special-events/{created.id}/push-all",
            {},
            headers={"X-CSRF-Token": csrf},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 503, body)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["partial"])
        self.assertEqual(payload["pushed"], [])
        self.assertEqual(len(payload["errors"]), 2)
        self.assertIn("Default Kindle", payload["error"])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_special_event_push_all_endpoint_pushes_all_enabled_kindles(self, mock_render_device, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
            "use_screensaver_overlay": True,
        })

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/devices/push-all",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["partial"])
        self.assertEqual(payload["pushed"], ["Default Kindle", "Kitchen Kindle"])
        self.assertEqual(payload["errors"], [])
        mock_render_device.assert_has_calls([
            mock.call("default-kindle", force=True, registry=self.registry),
            mock.call("kitchen-kindle", force=True, registry=self.registry),
        ])
        kitchen_ssh_args = mock_run.call_args_list[3].args[0]
        self.assertIn("/mnt/us/dashboard/apply-screensaver-overlay.sh && sync", kitchen_ssh_args[-1])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_special_event_push_all_returns_partial_success(self, mock_render_device, mock_run):
        success = mock.Mock(returncode=0, stdout="", stderr="")
        failure = mock.Mock(
            returncode=255,
            stdout="",
            stderr="ssh: connect to host 192.168.68.122 port 22: No route to host\n",
        )
        mock_run.side_effect = [
            success,
            success,
            success,
            failure,
        ]
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
            "use_screensaver_overlay": True,
        })

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/devices/push-all",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["pushed"], ["Default Kindle"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("Kitchen Kindle", payload["errors"][0])
        self.assertIn("No route to host", payload["errors"][0])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_push_kitchen_kindle_reapplies_overlay_when_configured(self, mock_render_device, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
            "use_screensaver_overlay": True,
        })

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/device/kitchen-kindle/push",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(status, 200)
        ssh_args = mock_run.call_args_list[1].args[0]
        self.assertIn("root@192.168.68.122", ssh_args)
        self.assertIn(
            "if [ ! -x /mnt/us/dashboard/apply-screensaver-overlay.sh ]; then",
            ssh_args[-1],
        )
        self.assertIn(
            "missing required screensaver overlay script",
            ssh_args[-1],
        )
        self.assertIn("/mnt/us/dashboard/apply-screensaver-overlay.sh && sync", ssh_args[-1])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_push_kindle_131_uses_profile_key_and_reapplies_overlay(self, mock_render_device, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        self.registry.add({
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
                "port": 22,
            },
            "use_screensaver_overlay": True,
        })

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/device/kindle-131/push",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(status, 200)
        mock_render_device.assert_called_once_with(
            "kindle-131",
            force=True,
            registry=self.registry,
        )
        scp_args = mock_run.call_args_list[0].args[0]
        ssh_args = mock_run.call_args_list[1].args[0]
        self.assertEqual(scp_args[:3], ["scp", "-i", "/home/user/.ssh/kindle_dashboard_ed25519"])
        self.assertIn("UserKnownHostsFile=/home/user/.ssh/kindle_dashboard_known_hosts", scp_args)
        self.assertIn("root@192.168.68.131:/mnt/us/dashboard/image.png", scp_args)
        self.assertEqual(ssh_args[:3], ["ssh", "-i", "/home/user/.ssh/kindle_dashboard_ed25519"])
        self.assertIn("root@192.168.68.131", ssh_args)
        self.assertIn("/mnt/us/dashboard/apply-screensaver-overlay.sh && sync", ssh_args[-1])
        self.assertIn("/usr/sbin/eips -g /mnt/us/dashboard/image.png", ssh_args[-1])

    @mock.patch("settings_server.subprocess.run")
    @mock.patch("settings_server.render_device")
    def test_push_overlay_missing_returns_useful_error(self, mock_render_device, mock_run):
        copy_result = mock.Mock(returncode=0, stdout="", stderr="")
        overlay_result = mock.Mock(
            returncode=1,
            stdout="",
            stderr="missing required screensaver overlay script: /mnt/us/dashboard/apply-screensaver-overlay.sh\n",
        )
        mock_run.side_effect = [copy_result, overlay_result]
        self.registry.add({
            "id": "kitchen-kindle",
            "name": "Kitchen Kindle",
            "type": "kindle_pw1",
            "resolution": [758, 1024],
            "enabled": True,
            "config_path": "devices/kitchen-kindle/config.json",
            "image_path": "devices/kitchen-kindle/image.png",
            "connection": {
                "host": "192.168.68.122",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
            "use_screensaver_overlay": True,
        })

        csrf = self.csrf_token()
        status, _, body = self.post_json(
            "/api/device/kitchen-kindle/push",
            {},
            headers={"X-CSRF-Token": csrf},
        )

        self.assertEqual(status, 503)
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("missing required screensaver overlay script", payload["error"])


class LowPowerDeploymentIntegrationTests(unittest.TestCase):
    def test_prepare_low_power_deployment_requires_explicit_default_device(self):
        default = mock.Mock(
            id="default-kindle",
            name="Default Kindle",
            type="kindle_pw1",
            enabled=True,
            resolution=(758, 1024),
            connection={
                "host": "192.168.68.119",
                "user": "root",
                "ssh_profile": "kindle_dashboard",
                "port": 22,
            },
        )
        registry = mock.Mock()
        registry.get.return_value = default

        deployment = settings_server.prepare_low_power_deployment(
            registry,
            "default-kindle",
            {"refresh_interval_minutes": 60},
            "192.168.68.167",
            8765,
        )

        registry.get.assert_called_once_with(
            "default-kindle", require_enabled=True
        )
        self.assertEqual(deployment.device_id, "default-kindle")
        self.assertIn(
            "/mnt/us/dashboard/low-power-cycle.sh", deployment.files
        )

    def test_prepare_low_power_deployment_rejects_other_device_ids(self):
        registry = mock.Mock()
        with self.assertRaises(ValueError):
            settings_server.prepare_low_power_deployment(
                registry,
                "kitchen-kindle",
                {"refresh_interval_minutes": 60},
                "192.168.68.167",
                8765,
            )
        registry.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()

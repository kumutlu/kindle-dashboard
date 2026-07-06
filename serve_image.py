#!/usr/bin/env python3
"""Strict HTTP server for legacy and per-device dashboard images."""

import datetime
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from device_registry import DeviceNotFoundError, DeviceRegistry


BASE = Path(__file__).resolve().parent
LEGACY_IMAGE_PATH = BASE / "kindle_weather.png"
NEWS_IMAGE_PATH = BASE / "kindle_news.png"
BATT_FILE = BASE / "battery.txt"
ACCESS_LOG_PATH = BASE / "access.log"
PORT = 8765
DEVICE_IMAGE_RE = re.compile(
    r"^/device/([a-z0-9][a-z0-9-]{0,63})/image\.png$"
)
DEVICE_BMP_RE = re.compile(
    r"^/device/([a-z0-9][a-z0-9-]{0,63})/image\.bmp$"
)


def make_handler(
    registry,
    legacy_image_path=LEGACY_IMAGE_PATH,
    news_image_path=NEWS_IMAGE_PATH,
    battery_file=BATT_FILE,
    access_log_path=ACCESS_LOG_PATH,
):
    legacy_image_path = Path(legacy_image_path)
    news_image_path = Path(news_image_path)
    battery_file = Path(battery_file)
    access_log_path = Path(access_log_path)

    class ImageHandler(BaseHTTPRequestHandler):
        server_version = "KindleImage"
        sys_version = ""

        def _send_empty(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _legacy_maarif_check(self):
            try:
                import weather_image

                config = weather_image.load_config()
                if (
                    config.get("theme") == "maarif_calendar"
                    and weather_image.should_regenerate_maarif(config)
                ):
                    weather_image.generate_dashboard_safe()
            except Exception as exc:
                print(
                    "Error checking/regenerating Maarif Calendar:"
                    f" {exc}"
                )

        def _record_battery(self, raw_path):
            match = re.search(r"[?&]batt=(\d{1,3})", raw_path)
            if match is None:
                return
            try:
                battery_file.write_text(
                    match.group(1),
                    encoding="utf-8",
                )
            except OSError:
                pass

        def _resolve_image(self, route_path):
            if route_path == "/weather.png":
                self._legacy_maarif_check()
                return legacy_image_path
            if route_path == "/news.png":
                return news_image_path
            match = DEVICE_IMAGE_RE.fullmatch(route_path)
            if match is None:
                raise DeviceNotFoundError(route_path)
            device = registry.get(
                match.group(1),
                require_enabled=True,
            )
            if (
                device.id == "default-kindle"
                and not device.image_path.exists()
            ):
                return legacy_image_path
            return device.image_path

        def _serve_image(self, include_body=True):
            raw_path = self.path
            route_path = urlsplit(raw_path).path
            if include_body and route_path == "/weather.png":
                self._record_battery(raw_path)
            bmp_match = DEVICE_BMP_RE.fullmatch(route_path)
            if bmp_match is not None:
                device_id = bmp_match.group(1)
                try:
                    device = registry.get(device_id, require_enabled=True)
                except DeviceNotFoundError:
                    self._send_empty(404)
                    return
                if device.type == "esp32_epaper":
                    err_msg = b"BMP output for ESP32 e-paper devices is not implemented yet\n"
                    self.send_response(501)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(err_msg)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if include_body:
                        self.wfile.write(err_msg)
                    return
                else:
                    self._send_empty(400)
                    return

            try:
                image_path = self._resolve_image(route_path)
                image = image_path.read_bytes()
            except (DeviceNotFoundError, FileNotFoundError, OSError):
                self._send_empty(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                self.wfile.write(image)

        def do_GET(self):
            self._serve_image(include_body=True)

        def do_HEAD(self):
            self._serve_image(include_body=False)

        def log_message(self, format_string, *args):
            line = "%s %s %s\n" % (
                datetime.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                self.address_string(),
                format_string % args,
            )
            try:
                with access_log_path.open(
                    "a",
                    encoding="utf-8",
                ) as handle:
                    handle.write(line)
            except OSError:
                pass

    return ImageHandler


def make_server(
    host="0.0.0.0",
    port=PORT,
    registry=None,
    legacy_image_path=LEGACY_IMAGE_PATH,
    news_image_path=NEWS_IMAGE_PATH,
    battery_file=BATT_FILE,
    access_log_path=ACCESS_LOG_PATH,
):
    if registry is None:
        registry = DeviceRegistry(BASE)
    return HTTPServer(
        (host, port),
        make_handler(
            registry,
            legacy_image_path=legacy_image_path,
            news_image_path=news_image_path,
            battery_file=battery_file,
            access_log_path=access_log_path,
        ),
    )


if __name__ == "__main__":
    server = make_server()
    print(
        "Serving /weather.png and /device/<device_id>/image.png"
        f" on port {PORT}"
    )
    server.serve_forever()

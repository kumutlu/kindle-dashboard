#!/usr/bin/env python3
import hmac
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


HOST = "127.0.0.1"
PORT = 8766
IMAGE_PATH = Path(__file__).with_name("kindle_weather.png")


def make_handler(image_path, token):
    image_path = Path(image_path)
    expected_authorization = f"Bearer {token}"

    class AuthenticatedImageHandler(BaseHTTPRequestHandler):
        server_version = "KindleImage"
        sys_version = ""

        def _serve_image(self, include_body=True):
            if urlsplit(self.path).path != "/weather.png":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            supplied_authorization = self.headers.get("Authorization", "")
            if not hmac.compare_digest(
                supplied_authorization,
                expected_authorization,
            ):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            try:
                import sys
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import weather_image
                config = weather_image.load_config()
                if config.get("theme") == "maarif_calendar":
                    if weather_image.should_regenerate_maarif(config):
                        weather_image.generate_dashboard_safe()
            except Exception as e:
                pass

            try:
                image = image_path.read_bytes()
            except FileNotFoundError:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
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
            return

    return AuthenticatedImageHandler


def make_server(image_path, token, host=HOST, port=PORT):
    return HTTPServer((host, port), make_handler(image_path, token))


def main():
    token = os.environ.get("PUBLIC_IMAGE_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("PUBLIC_IMAGE_TOKEN is missing or too short")

    server = make_server(IMAGE_PATH, token)
    print(f"Authenticated Kindle image endpoint listening on {HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

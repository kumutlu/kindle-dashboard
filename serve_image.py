#!/usr/bin/env python3
"""
Kindle Display — HTTP Image Server
Serves weather.png and news.png over HTTP.
The Kindle fetches these with wget.
"""

import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE = os.path.dirname(__file__)
ROUTES = {
    "/weather.png": os.path.join(BASE, "kindle_weather.png"),
    "/news.png":    os.path.join(BASE, "kindle_news.png"),
}
BATT_FILE = os.path.join(BASE, "battery.txt")
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def _serve_image(self, include_body=True):
        raw_path = self.path
        route_path = raw_path.split("?", 1)[0]
        if route_path == "/weather.png":
            try:
                import weather_image
                config = weather_image.load_config()
                if config.get("theme") == "maarif_calendar":
                    if weather_image.should_regenerate_maarif(config):
                        weather_image.generate_dashboard_safe()
            except Exception as e:
                print(f"Error checking/regenerating Maarif Calendar: {e}")
        if include_body:
            m = re.search(r"[?&]batt=(\d{1,3})", raw_path)
            if m:
                try:
                    with open(BATT_FILE, "w") as f:
                        f.write(m.group(1))
                except Exception:
                    pass
        path = ROUTES.get(route_path)
        if path:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if include_body:
                    self.wfile.write(data)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                if include_body:
                    self.wfile.write(b"Image not found")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        self._serve_image(include_body=True)

    def do_HEAD(self):
        self._serve_image(include_body=False)

    def log_message(self, fmt, *args):
        # Log Kindle accesses (for diagnostics)
        import datetime
        line = "%s %s %s\n" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            self.address_string(),
            (fmt % args),
        )
        try:
            with open(os.path.join(BASE, "access.log"), "a") as f:
                f.write(line)
        except Exception:
            pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving {list(ROUTES.keys())} on port {PORT}")
    server.serve_forever()

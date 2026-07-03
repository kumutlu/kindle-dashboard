#!/usr/bin/env python3
import fcntl
import json
import os
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from dashboard_themes import effective_visibility, validate_theme

W, H = 758, 1024
PROJECT_DIR = Path(__file__).resolve().parent
OUT = PROJECT_DIR / "kindle_weather.png"
CONFIG_PATH = PROJECT_DIR / "dashboard_config.json"
LOCK_PATH = PROJECT_DIR / ".dashboard-generation.lock"

WTTR_BASE = "https://wttr.in"
OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
PIHOLE_BASE = os.environ.get("PIHOLE_BASE", "http://192.168.68.167").rstrip("/")
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

DEFAULT_CONFIG = {
    "title": "NOTTINGHAM HOME",
    "location_label": "Nottingham, UK",
    "weather_query": "Nottingham",
    "timezone": "Europe/London",
    "theme": "home_dashboard",
    "show_weather": True,
    "show_forecast": True,
    "show_server": True,
    "show_pihole": True,
    "show_tailscale": True,
}

STRING_LIMITS = {
    "title": 28,
    "location_label": 80,
    "weather_query": 100,
    "timezone": 64,
    "theme": 40,
}
BOOLEAN_FIELDS = {
    "show_weather",
    "show_forecast",
    "show_server",
    "show_pihole",
    "show_tailscale",
}


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError("configuration must be a JSON object")
    if set(value) != set(DEFAULT_CONFIG):
        raise ValueError("configuration fields do not match the supported schema")

    config = {}
    for key, limit in STRING_LIMITS.items():
        item = value.get(key)
        if not isinstance(item, str):
            raise ValueError(f"{key} must be text")
        item = item.strip()
        if not item or len(item) > limit:
            raise ValueError(f"{key} must contain 1-{limit} characters")
        config[key] = item

    validate_theme(config["theme"])
    try:
        ZoneInfo(config["timezone"])
    except Exception as exc:
        raise ValueError("unknown timezone") from exc

    for key in BOOLEAN_FIELDS:
        item = value.get(key)
        if not isinstance(item, bool):
            raise ValueError(f"{key} must be true or false")
        config[key] = item

    return config


def load_config(path=CONFIG_PATH):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return validate_config(raw)
    except Exception:
        print("Dashboard config missing or invalid; using Nottingham defaults")
        return dict(DEFAULT_CONFIG)


def weather_url(query):
    return f"{WTTR_BASE}/{quote(query, safe='')}?format=j1"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=7).strip()
    except Exception:
        return ""


def http_json(url, timeout=8, data=None, headers=None):
    req_headers = {"User-Agent": "KindleDashboard/1.0"}
    if headers:
        req_headers.update(headers)

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def txt(d, x, y, value, fnt, anchor=None):
    d.text((x, y), str(value), fill=0, font=fnt, anchor=anchor)


def box(d, xy, radius=8, width=2):
    d.rounded_rectangle(xy, radius=radius, outline=0, width=width)


def fmt(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def open_meteo_weather_kind(code):
    try:
        code = int(code)
    except Exception:
        return "partly_cloudy"
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly_cloudy"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "partly_cloudy"


def weather_description(kind):
    return {
        "clear": "Clear",
        "partly_cloudy": "Partly cloudy",
        "cloudy": "Cloudy",
        "rain": "Rain",
        "snow": "Snow",
        "storm": "Thunderstorm",
        "fog": "Fog",
    }.get(kind, "Partly cloudy")


def degrees_to_compass(degrees):
    directions = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    value = float(degrees) % 360
    return directions[int((value + 11.25) // 22.5) % 16]


def kmh_to_mph(value):
    return int(round(float(value) * 0.621371))


def open_meteo_time(value):
    text = str(value)
    return text.split("T", 1)[-1][:5]


def normalize_open_meteo(payload):
    current = payload["current"]
    daily = payload["daily"]
    days = []
    for index, date in enumerate(daily["time"][:3]):
        kind = open_meteo_weather_kind(daily["weather_code"][index])
        hourly = [
            {
                "weatherCode": kind,
                "chanceofrain": str(
                    int(round(daily["precipitation_probability_max"][index]))
                ),
            }
            for _ in range(5)
        ]
        days.append({
            "date": date,
            "maxtempC": str(
                int(round(daily["temperature_2m_max"][index]))
            ),
            "mintempC": str(
                int(round(daily["temperature_2m_min"][index]))
            ),
            "astronomy": [{
                "sunrise": open_meteo_time(daily["sunrise"][index]),
                "sunset": open_meteo_time(daily["sunset"][index]),
            }],
            "hourly": hourly,
        })
    if len(days) < 3:
        raise ValueError("Open-Meteo returned fewer than three forecast days")

    current_kind = open_meteo_weather_kind(current["weather_code"])
    return {
        "current_condition": [{
            "temp_C": str(int(round(current["temperature_2m"]))),
            "FeelsLikeC": str(
                int(round(current["apparent_temperature"]))
            ),
            "weatherDesc": [{"value": weather_description(current_kind)}],
            "humidity": str(
                int(round(current["relative_humidity_2m"]))
            ),
            "windspeedMiles": str(kmh_to_mph(current["wind_speed_10m"])),
            "winddir16Point": degrees_to_compass(
                current["wind_direction_10m"]
            ),
            "pressure": str(int(round(current["pressure_msl"]))),
            "weatherCode": current_kind,
        }],
        "weather": days,
    }


def fetch_open_meteo(query, timezone):
    geocoding_url = (
        f"{OPEN_METEO_GEOCODING}?"
        + urlencode({
            "name": query,
            "count": 1,
            "language": "en",
            "format": "json",
        })
    )
    geocoding = http_json(geocoding_url, timeout=12)
    results = geocoding.get("results") or []
    if not results:
        raise ValueError("location was not found")
    location = results[0]

    forecast_url = (
        f"{OPEN_METEO_FORECAST}?"
        + urlencode({
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": ",".join((
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "pressure_msl",
            )),
            "daily": ",".join((
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "sunrise",
                "sunset",
                "weather_code",
            )),
            "timezone": timezone,
            "forecast_days": 3,
            "wind_speed_unit": "kmh",
        })
    )
    return normalize_open_meteo(http_json(forecast_url, timeout=20))


def fetch_wttr(query):
    return http_json(weather_url(query), timeout=20)


def fetch_weather(query, timezone):
    try:
        weather = fetch_open_meteo(query, timezone)
        provider = "open_meteo"
    except Exception:
        weather = fetch_wttr(query)
        provider = "wttr_fallback"
    current = weather["current_condition"][0]
    print(
        f"Weather provider: {provider},"
        f" temp={current['temp_C']},"
        f" feels={current['FeelsLikeC']}"
    )
    return weather


def get_cpu():
    out = sh("top -bn1 | grep 'Cpu(s)'")
    try:
        idle = float(out.split("id,")[0].split(",")[-1].strip())
        return int(round(100 - idle))
    except Exception:
        return 0


def get_ram():
    out = sh("free | awk '/Mem:/ {print int($3/$2*100)}'")
    try:
        return int(out)
    except Exception:
        return 0


def get_disk():
    out = sh("df / | awk 'NR==2 {gsub(\"%\",\"\",$5); print $5}'")
    try:
        return int(out)
    except Exception:
        return 0


def get_tailscale():
    devices = []
    raw = sh("tailscale status --json 2>/dev/null")

    if raw:
        try:
            data = json.loads(raw)

            self_node = data.get("Self", {})
            if self_node.get("TailscaleIPs"):
                devices.append({
                    "name": self_node.get("HostName", "this-device"),
                    "online": True,
                })

            for peer in data.get("Peer", {}).values():
                devices.append({
                    "name": peer.get("HostName", "device"),
                    "online": bool(peer.get("Online", False)),
                })
        except Exception:
            pass

    if not devices:
        raw = sh("tailscale status 2>/dev/null")
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("100."):
                devices.append({
                    "name": parts[1],
                    "online": "offline" not in line.lower(),
                })

    online = [d for d in devices if d.get("online")]
    return {
        "total": len(devices),
        "online": len(online),
    }


def pihole_v6_sid():
    if not PIHOLE_PASSWORD:
        return None

    try:
        data = http_json(
            f"{PIHOLE_BASE}/api/auth",
            timeout=8,
            data={"password": PIHOLE_PASSWORD},
        )
        session = data.get("session", {})
        return session.get("sid") or data.get("sid")
    except Exception:
        return None


def close_pihole_v6_sid(sid):
    request = urllib.request.Request(
        f"{PIHOLE_BASE}/api/auth",
        headers={"X-FTL-SID": sid},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read()
    except Exception:
        pass


def get_pihole():
    # Pi-hole v5
    try:
        data = http_json(f"{PIHOLE_BASE}/admin/api.php", timeout=5)
        if isinstance(data, dict) and "dns_queries_today" in data:
            return {
                "queries": int(data.get("dns_queries_today", 0)),
                "blocked": int(data.get("ads_blocked_today", 0)),
                "clients": int(data.get("unique_clients", 0)),
                "ok": True,
                "version": "v5",
            }
    except Exception:
        pass

    # Pi-hole v6
    sid = pihole_v6_sid()
    if sid:
        try:
            data = http_json(
                f"{PIHOLE_BASE}/api/stats/summary",
                timeout=8,
                headers={"X-FTL-SID": sid},
            )

            queries = 0
            blocked = 0
            clients = 0

            if isinstance(data.get("queries"), dict):
                queries = data["queries"].get("total", 0)
                blocked = data["queries"].get("blocked", 0)

            if isinstance(data.get("clients"), dict):
                clients = data["clients"].get("active", 0)

            return {
                "queries": int(queries or 0),
                "blocked": int(blocked or 0),
                "clients": int(clients or 0),
                "ok": True,
                "version": "v6",
            }
        except Exception:
            pass
        finally:
            close_pihole_v6_sid(sid)

    return {
        "queries": 0,
        "blocked": 0,
        "clients": 0,
        "ok": False,
        "version": "none",
    }


def progress(d, x, y, width, pct):
    pct = max(0, min(100, int(pct)))
    d.rectangle((x, y, x + width, y + 9), outline=0, width=1)
    if pct > 0:
        d.rectangle((x, y, x + int(width * pct / 100), y + 9), fill=0)


# ---------- Clean weather icons ----------

def draw_sun(d, cx, cy, size):
    r = max(6, size // 5)
    ray_inner = r + max(5, size // 12)
    ray_outer = size // 2
    stroke = max(2, size // 22)

    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=stroke)

    for dx, dy in [
        (0, -1), (1, -1), (1, 0), (1, 1),
        (0, 1), (-1, 1), (-1, 0), (-1, -1),
    ]:
        length = 0.707 if dx and dy else 1
        x1 = cx + int(dx * ray_inner * length)
        y1 = cy + int(dy * ray_inner * length)
        x2 = cx + int(dx * ray_outer * length)
        y2 = cy + int(dy * ray_outer * length)
        d.line((x1, y1, x2, y2), fill=0, width=stroke)


def draw_cloud(d, cx, cy, size):
    # Merge the cloud primitives into one mask so no internal arcs overlap.
    mask = Image.new("L", d._image.size, 0)
    md = ImageDraw.Draw(mask)
    left = cx - size // 2
    right = cx + size // 2
    bottom = cy + size // 4

    md.rectangle((left + size // 8, cy, right - size // 8, bottom), fill=255)
    md.ellipse((left, cy - size // 12, left + size // 2, bottom), fill=255)
    md.ellipse(
        (cx - size // 3, cy - size // 2, cx + size // 4, bottom),
        fill=255,
    )
    md.ellipse((cx, cy - size // 4, right, bottom), fill=255)

    border = max(3, size // 20)
    outline = mask.filter(ImageFilter.MaxFilter(border * 2 + 1))
    d.bitmap((0, 0), outline, fill=0)
    d.bitmap((0, 0), mask, fill=255)


def draw_partly_cloudy(d, cx, cy, size):
    draw_sun(d, cx - size // 4, cy - size // 5, size * 3 // 5)
    draw_cloud(d, cx + size // 10, cy + size // 9, size * 4 // 5)


def draw_rain(d, cx, cy, size):
    draw_cloud(d, cx, cy - size // 8, size)
    stroke = max(2, size // 22)
    for dx in (-size // 4, 0, size // 4):
        d.line(
            (cx + dx + size // 18, cy + size // 3,
             cx + dx - size // 18, cy + size // 2),
            fill=0,
            width=stroke,
        )


def draw_snow(d, cx, cy, size):
    draw_cloud(d, cx, cy - size // 8, size)
    stroke = max(2, size // 24)
    arm = max(4, size // 12)
    for dx in (-size // 4, 0, size // 4):
        sx = cx + dx
        sy = cy + size * 2 // 5
        d.line((sx - arm, sy, sx + arm, sy), fill=0, width=stroke)
        d.line((sx, sy - arm, sx, sy + arm), fill=0, width=stroke)


def draw_storm(d, cx, cy, size):
    draw_cloud(d, cx, cy - size // 8, size)
    bolt = [
        (cx + size // 14, cy + size // 4),
        (cx - size // 8, cy + size // 2),
        (cx, cy + size // 2),
        (cx - size // 14, cy + size * 3 // 4),
        (cx + size // 5, cy + size * 2 // 5),
        (cx + size // 16, cy + size * 2 // 5),
    ]
    d.polygon(bolt, fill=0)


def draw_fog(d, cx, cy, size):
    draw_cloud(d, cx, cy - size // 7, size)
    stroke = max(2, size // 24)
    half = size // 3
    for offset in (size // 3, size // 2):
        d.line((cx - half, cy + offset, cx + half, cy + offset),
               fill=0, width=stroke)


def weather_kind(code):
    semantic = {
        "clear": "sun",
        "partly_cloudy": "partly",
        "cloudy": "cloud",
        "rain": "rain",
        "snow": "snow",
        "storm": "storm",
        "fog": "fog",
    }
    if str(code) in semantic:
        return semantic[str(code)]
    try:
        code = int(code)
    except Exception:
        return "partly"

    if code == 113:
        return "sun"
    if code in [143, 248, 260]:
        return "fog"
    if code in [119, 122]:
        return "cloud"
    if code in [176, 263, 266, 293, 296, 299, 302, 305, 308, 353, 356, 359]:
        return "rain"
    if code in [179, 182, 185, 227, 230, 317, 320, 323, 326, 329, 332,
                335, 338, 350, 362, 365, 368, 371, 374, 377]:
        return "snow"
    if code in [200, 386, 389, 392, 395]:
        return "storm"
    return "partly"


def draw_weather_icon(d, kind, cx, cy, size):
    if kind == "sun":
        draw_sun(d, cx, cy, size)
    elif kind == "cloud":
        draw_cloud(d, cx, cy, size)
    elif kind == "rain":
        draw_rain(d, cx, cy, size)
    elif kind == "snow":
        draw_snow(d, cx, cy, size)
    elif kind == "storm":
        draw_storm(d, cx, cy, size)
    elif kind == "fog":
        draw_fog(d, cx, cy, size)
    else:
        draw_partly_cloudy(d, cx, cy, size)


def build_layout(config):
    """Return section coordinates and visible server cards."""
    visibility = effective_visibility(config["theme"], config)
    cursor = 120
    layout = {
        "weather_top": None,
        "forecast_heading": None,
        "forecast_cards": None,
        "forecast_divider": None,
        "server_heading": None,
        "server_cards": None,
        "server_card_names": [],
    }

    if visibility["show_weather"]:
        layout["weather_top"] = cursor + 20
        cursor += 275

    if visibility["show_forecast"]:
        layout["forecast_heading"] = cursor + 20
        layout["forecast_cards"] = cursor + 60
        layout["forecast_divider"] = cursor + 245
        cursor += 245

    if visibility["show_server"]:
        layout["server_heading"] = cursor + 28
        layout["server_cards"] = cursor + 68
        cards = ["CPU", "RAM", "DISK"]
        if visibility["show_pihole"]:
            cards.extend(["PI-HOLE", "QUERIES"])
        if visibility["show_tailscale"]:
            cards.append("TAILSCALE")
        layout["server_card_names"] = cards

    return layout


def main():
    with LOCK_PATH.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        generate_dashboard()


def collect_dashboard_data(config):
    weather = fetch_weather(config["weather_query"], config["timezone"])
    current = weather["current_condition"][0]
    days = weather["weather"]
    now = datetime.now(ZoneInfo(config["timezone"]))
    return {
        "weather": weather,
        "current": current,
        "days": days,
        "now": now,
        "temp": int(current["temp_C"]),
        "feels": int(current["FeelsLikeC"]),
        "desc": current["weatherDesc"][0]["value"],
        "humidity": current["humidity"],
        "wind": current["windspeedMiles"],
        "wind_dir": current["winddir16Point"],
        "pressure": current["pressure"],
        "hi": days[0]["maxtempC"],
        "lo": days[0]["mintempC"],
        "sunrise": days[0]["astronomy"][0]["sunrise"][:5],
        "sunset": days[0]["astronomy"][0]["sunset"][:5],
        "cpu": get_cpu(),
        "ram": get_ram(),
        "disk": get_disk(),
        "ph": get_pihole(),
        "ts": get_tailscale(),
    }


def dashboard_fonts():
    return {
        "FB96": font(FONT_BOLD, 96),
        "FB72": font(FONT_BOLD, 72),
        "FB68": font(FONT_BOLD, 68),
        "FB44": font(FONT_BOLD, 44),
        "FB36": font(FONT_BOLD, 36),
        "FB32": font(FONT_BOLD, 32),
        "FB28": font(FONT_BOLD, 28),
        "FB24": font(FONT_BOLD, 24),
        "FB22": font(FONT_BOLD, 22),
        "FB20": font(FONT_BOLD, 20),
        "FB18": font(FONT_BOLD, 18),
        "FR20": font(FONT_REG, 20),
        "FR18": font(FONT_REG, 18),
        "FR16": font(FONT_REG, 16),
        "FR14": font(FONT_REG, 14),
    }


def save_dashboard(img, data):
    img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    temporary_output = Path(f"{OUT}.tmp")
    try:
        img.save(temporary_output, format="PNG")
        os.replace(temporary_output, OUT)
    finally:
        temporary_output.unlink(missing_ok=True)
    print(f"Saved: {OUT}")
    print(f"Pi-hole: {data['ph']}")
    print(
        f"Tailscale: online={data['ts']['online']}"
        f" total={data['ts']['total']}"
    )


def render_home_dashboard(config):
    layout = build_layout(config)
    data = collect_dashboard_data(config)
    current = data["current"]
    days = data["days"]
    (
        now, temp, feels, desc, humidity, wind, wind_dir, pressure,
        hi, lo, sunrise, sunset, cpu, ram, disk, ph, ts,
    ) = (
        data[key] for key in (
            "now", "temp", "feels", "desc", "humidity", "wind",
            "wind_dir", "pressure", "hi", "lo", "sunrise", "sunset",
            "cpu", "ram", "disk", "ph", "ts",
        )
    )
    fonts = dashboard_fonts()
    (
        FB68, FB28, FB24, FB22, FB20, FB18, FR18, FR16, FR14,
    ) = (
        fonts[key] for key in (
            "FB68", "FB28", "FB24", "FB22", "FB20", "FB18",
            "FR18", "FR16", "FR14",
        )
    )

    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    box(d, (10, 10, 748, 1014), 10, 2)

    # Header - no large clock, use large date instead
    txt(d, 34, 28, config["title"], FB28)
    txt(d, 720, 30, now.strftime("%A").upper(), FB24, anchor="ra")
    txt(d, 720, 68, now.strftime("%d %B %Y"), FB24, anchor="ra")
    d.line((24, 112, 734, 112), fill=0, width=2)

    weather_top = layout["weather_top"]
    if weather_top is not None:
        box(d, (24, weather_top, 734, weather_top + 235), 8, 2)

        # Current weather: icon, dominant temperature, then compact details.
        txt(d, 48, weather_top + 32, "CURRENT WEATHER", FB18)
        draw_weather_icon(
            d,
            weather_kind(current.get("weatherCode")),
            120,
            weather_top + 130,
            100,
        )
        txt(d, 335, weather_top + 105, f"{temp}°C", FB68, anchor="mm")
        txt(d, 335, weather_top + 170, desc.upper(), FB18, anchor="mm")
        txt(d, 335, weather_top + 202, f"Feels like {feels}°C", FR16, anchor="mm")

        # Right side info: deliberately small fonts and wide spacing.
        label_x = 515
        value_x = 710
        y = weather_top + 22
        step = 39

        rows = [
            ("High / Low", f"{hi}° / {lo}°"),
            ("Humidity", f"{humidity}%"),
            ("Wind", f"{wind} mph {wind_dir}"),
            ("Sunrise", sunrise),
            ("Sunset", sunset),
        ]

        for label, value in rows:
            txt(d, label_x, y, label, FR14)
            txt(d, value_x, y, value, FB18, anchor="ra")
            y += step

    forecast_heading = layout["forecast_heading"]
    if forecast_heading is not None:
        txt(d, 34, forecast_heading, "FORECAST", FB22)

        names = ["TODAY", "TOMORROW"]
        try:
            names.append(
                datetime.strptime(days[2]["date"], "%Y-%m-%d")
                .strftime("%A").upper()
            )
        except Exception:
            names.append("DAY 3")

        for i, day in enumerate(days[:3]):
            x = 34 + i * 236
            y = layout["forecast_cards"]
            box(d, (x, y, x + 210, y + 165), 8, 2)

            noon = day["hourly"][4]
            txt(d, x + 105, y + 25, names[i], FB18, anchor="mm")
            draw_weather_icon(
                d,
                weather_kind(noon.get("weatherCode")),
                x + 105,
                y + 72,
                48,
            )
            txt(d, x + 105, y + 118,
                f"{day['maxtempC']}° / {day['mintempC']}°",
                FB20, anchor="mm")
            txt(d, x + 105, y + 147,
                f"{noon['chanceofrain']}% rain",
                FR14, anchor="mm")

        d.line(
            (24, layout["forecast_divider"], 734, layout["forecast_divider"]),
            fill=0,
            width=2,
        )

    server_heading = layout["server_heading"]
    if server_heading is not None:
        txt(d, 34, server_heading, "SERVER STATUS", FB22)

        all_cards = {
            "CPU": ("CPU", f"{cpu}%", cpu, ""),
            "RAM": ("RAM", f"{ram}%", ram, ""),
            "DISK": ("DISK", f"{disk}%", disk, ""),
            "PI-HOLE": ("PI-HOLE", fmt(ph["blocked"]), None, "blocked today"),
            "QUERIES": ("QUERIES", fmt(ph["queries"]), None, "dns today"),
            "TAILSCALE": (
                "TAILSCALE", ts["online"], None, "online devices"
            ),
        }
        cards = [
            all_cards[name] for name in layout["server_card_names"]
        ]

        for i, (label, value, pct, sub) in enumerate(cards):
            row = i // 3
            col = i % 3
            x = 34 + col * 236
            y = layout["server_cards"] + row * 112

            box(d, (x, y, x + 210, y + 94), 8, 2)
            txt(d, x + 18, y + 18, label, FB18)
            txt(d, x + 190, y + 42, value, FB22, anchor="ra")

            if pct is not None:
                progress(d, x + 22, y + 72, 166, pct)
            else:
                txt(d, x + 18, y + 70, sub, FR14)

    # No long Tailscale line anymore

    d.line((24, 960, 734, 960), fill=0, width=2)
    txt(d, 34, 988, f"Updated {now.strftime('%H:%M')}", FR14)
    txt(d, 300, 988, f"Pressure {pressure} hPa", FR14)
    txt(d, 585, 988, f"Clients {ph['clients']}", FR14)

    save_dashboard(img, data)


def forecast_day_names(days):
    names = ["TODAY", "TOMORROW"]
    try:
        names.append(
            datetime.strptime(days[2]["date"], "%Y-%m-%d")
            .strftime("%A").upper()
        )
    except Exception:
        names.append("DAY 3")
    return names


def draw_large_forecast(d, days, fonts, top):
    names = forecast_day_names(days)
    for i, day in enumerate(days[:3]):
        x = 34 + i * 236
        box(d, (x, top, x + 210, top + 240), 10, 2)
        noon = day["hourly"][4]
        txt(d, x + 105, top + 31, names[i], fonts["FB20"], anchor="mm")
        draw_weather_icon(
            d,
            weather_kind(noon.get("weatherCode")),
            x + 105,
            top + 94,
            70,
        )
        txt(
            d, x + 105, top + 162,
            f"{day['maxtempC']}° / {day['mintempC']}°",
            fonts["FB24"], anchor="mm",
        )
        txt(
            d, x + 105, top + 207,
            f"{noon['chanceofrain']}% rain",
            fonts["FR16"], anchor="mm",
        )


def draw_weather_footer(d, data, fonts):
    d.line((24, 960, 734, 960), fill=0, width=2)
    txt(d, 34, 988, f"Updated {data['now'].strftime('%H:%M')}", fonts["FR14"])
    txt(d, 300, 988, f"Pressure {data['pressure']} hPa", fonts["FR14"])
    txt(d, 585, 988, f"Humidity {data['humidity']}%", fonts["FR14"])


def render_minimal_weather(config):
    data = collect_dashboard_data(config)
    fonts = dashboard_fonts()
    current = data["current"]
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    box(d, (10, 10, 748, 1014), 10, 2)

    txt(d, 34, 28, config["title"], fonts["FB28"])
    txt(d, 34, 70, config["location_label"], fonts["FR18"])
    txt(d, 720, 30, data["now"].strftime("%A").upper(), fonts["FB24"], anchor="ra")
    txt(d, 720, 68, data["now"].strftime("%d %B %Y"), fonts["FB20"], anchor="ra")
    d.line((24, 112, 734, 112), fill=0, width=2)

    box(d, (24, 140, 734, 500), 10, 2)
    txt(d, 48, 172, "CURRENT WEATHER", fonts["FB20"])
    draw_weather_icon(
        d, weather_kind(current.get("weatherCode")), 155, 315, 160
    )
    txt(d, 455, 282, f"{data['temp']}°C", fonts["FB96"], anchor="mm")
    txt(d, 455, 370, data["desc"].upper(), fonts["FB28"], anchor="mm")
    txt(
        d, 455, 414, f"Feels like {data['feels']}°C",
        fonts["FR20"], anchor="mm",
    )
    d.line((48, 452, 710, 452), fill=0, width=2)
    txt(d, 62, 474, f"High / Low  {data['hi']}° / {data['lo']}°", fonts["FR16"])
    txt(d, 300, 474, f"Humidity  {data['humidity']}%", fonts["FR16"])
    txt(
        d, 520, 474, f"Wind  {data['wind']} mph {data['wind_dir']}",
        fonts["FR16"],
    )

    txt(d, 34, 545, "FORECAST", fonts["FB24"])
    draw_large_forecast(d, data["days"], fonts, 590)
    draw_weather_footer(d, data, fonts)
    save_dashboard(img, data)


def render_server_monitor(config):
    data = collect_dashboard_data(config)
    fonts = dashboard_fonts()
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    box(d, (10, 10, 748, 1014), 10, 2)

    txt(d, 34, 28, config["title"], fonts["FB28"])
    txt(d, 34, 72, "SERVER MONITOR", fonts["FR18"])
    txt(d, 720, 30, data["now"].strftime("%A").upper(), fonts["FB24"], anchor="ra")
    txt(d, 720, 68, data["now"].strftime("%d %B %Y"), fonts["FB20"], anchor="ra")
    d.line((24, 112, 734, 112), fill=0, width=2)
    txt(d, 34, 145, "SYSTEM STATUS", fonts["FB24"])

    cards = [
        ("CPU", f"{data['cpu']}%", data["cpu"], ""),
        ("RAM", f"{data['ram']}%", data["ram"], ""),
        ("DISK", f"{data['disk']}%", data["disk"], ""),
        ("PI-HOLE", fmt(data["ph"]["blocked"]), None, "blocked today"),
        ("QUERIES", fmt(data["ph"]["queries"]), None, "dns today"),
        ("TAILSCALE", data["ts"]["online"], None, "online devices"),
    ]
    for i, (label, value, pct, sub) in enumerate(cards):
        row = i // 2
        col = i % 2
        x = 34 + col * 356
        y = 185 + row * 220
        box(d, (x, y, x + 334, y + 196), 10, 2)
        txt(d, x + 24, y + 25, label, fonts["FB24"])
        txt(d, x + 308, y + 96, value, fonts["FB44"], anchor="ra")
        if pct is not None:
            progress(d, x + 24, y + 146, 286, pct)
        else:
            txt(d, x + 24, y + 154, sub, fonts["FR18"])

    d.line((24, 960, 734, 960), fill=0, width=2)
    txt(d, 34, 988, f"Updated {data['now'].strftime('%H:%M')}", fonts["FR14"])
    txt(
        d, 280, 988, f"Clients {data['ph']['clients']}",
        fonts["FR14"],
    )
    txt(
        d, 530, 988, f"Tailscale {data['ts']['online']} online",
        fonts["FR14"],
    )
    save_dashboard(img, data)


def render_travel_weather(config):
    data = collect_dashboard_data(config)
    fonts = dashboard_fonts()
    current = data["current"]
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    box(d, (10, 10, 748, 1014), 10, 2)

    txt(d, 379, 35, config["title"], fonts["FB32"], anchor="ma")
    txt(
        d, 379, 82, config["location_label"].upper(),
        fonts["FB24"], anchor="ma",
    )
    txt(
        d, 379, 119,
        data["now"].strftime("%A · %d %B %Y").upper(),
        fonts["FR18"], anchor="ma",
    )
    d.line((24, 142, 734, 142), fill=0, width=2)

    box(d, (24, 165, 734, 505), 10, 2)
    draw_weather_icon(
        d, weather_kind(current.get("weatherCode")), 170, 315, 155
    )
    txt(d, 465, 260, f"{data['temp']}°C", fonts["FB96"], anchor="mm")
    txt(d, 465, 352, data["desc"].upper(), fonts["FB28"], anchor="mm")
    txt(
        d, 465, 398, f"Feels like {data['feels']}°C",
        fonts["FR20"], anchor="mm",
    )
    d.line((48, 447, 710, 447), fill=0, width=2)
    txt(
        d, 80, 476, f"{data['hi']}° / {data['lo']}°  HIGH / LOW",
        fonts["FR16"],
    )
    txt(d, 325, 476, f"SUNRISE  {data['sunrise']}", fonts["FR16"])
    txt(d, 545, 476, f"SUNSET  {data['sunset']}", fonts["FR16"])

    txt(d, 34, 545, "3-DAY OUTLOOK", fonts["FB24"])
    draw_large_forecast(d, data["days"], fonts, 590)
    draw_weather_footer(d, data, fonts)
    save_dashboard(img, data)


THEME_RENDERERS = {
    "home_dashboard": render_home_dashboard,
    "minimal_weather": render_minimal_weather,
    "server_monitor": render_server_monitor,
    "travel_weather": render_travel_weather,
}


def generate_dashboard():
    config = load_config()
    renderer = THEME_RENDERERS.get(config["theme"])
    if renderer is None:
        raise ValueError("theme renderer is not available")
    renderer(config)


if __name__ == "__main__":
    main()

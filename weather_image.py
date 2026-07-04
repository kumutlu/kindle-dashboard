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
    "location": "Nottingham",
    "country": "United Kingdom",
    "latitude": 52.9536,
    "longitude": -1.1505,
    "location_display": "Nottingham, England, United Kingdom",
    "location_label": "Nottingham, UK",
    "weather_query": "Nottingham",
    "timezone": "Europe/London",
    "theme": "home_dashboard",
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
    "refresh_interval_minutes": 10,
}

STRING_LIMITS = {
    "title": 28,
    "location": 100,
    "country": 100,
    "location_display": 160,
    "location_label": 160,
    "weather_query": 100,
    "timezone": 64,
    "theme": 40,
}
OPTIONAL_LOCATION_FIELDS = {
    "location",
    "country",
    "latitude",
    "longitude",
    "location_display",
    "kindle_frontlight",
    "prayer_method",
    "prayer_school",
    "prayer_high_latitude",
    "hijri_adjustment",
    "refresh_interval_minutes",
}
BOOLEAN_FIELDS = {
    "show_weather",
    "show_forecast",
    "show_server",
    "show_pihole",
    "show_tailscale",
}


def get_now(tz=None):
    if tz is None:
        return datetime.now().astimezone()
    return datetime.now(tz)


def get_local_date(config):
    tz_name = config.get("timezone")
    tz = None
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            pass
    now = get_now(tz)
    return now.date().isoformat()


def compute_config_hash(config):
    import hashlib
    serializable = {k: v for k, v in sorted(config.items()) if k in DEFAULT_CONFIG}
    return hashlib.sha256(json.dumps(serializable).encode("utf-8")).hexdigest()


def should_regenerate_maarif(config):
    img_file = PROJECT_DIR / "kindle_weather.png"
    if not img_file.exists():
        print("Maarif image file missing; regenerating image")
        return True

    state_file = PROJECT_DIR / "cache" / "render_state.json"
    if not state_file.exists():
        print("Maarif render state file missing; regenerating image")
        return True

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        print("Maarif render state file corrupted; regenerating image")
        return True

    if state.get("theme") != config.get("theme"):
        print("Maarif theme changed; regenerating image")
        return True

    if state.get("timezone") != config.get("timezone"):
        print("Maarif timezone changed; regenerating image")
        return True

    if state.get("latitude") != config.get("latitude") or state.get("longitude") != config.get("longitude"):
        print("Maarif coordinates changed; regenerating image")
        return True

    current_hash = compute_config_hash(config)
    if state.get("config_hash") != current_hash:
        print("Maarif config hash changed; regenerating image")
        return True

    local_date = get_local_date(config)
    last_date = state.get("local_date")
    print(f"Maarif local date: {local_date} {config.get('timezone')}")
    print(f"Maarif render state date: {last_date}")
    if local_date != last_date:
        print("Maarif date changed; regenerating image")
        return True

    return False



def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError("configuration must be a JSON object")
    unknown = set(value) - set(DEFAULT_CONFIG)
    required = set(DEFAULT_CONFIG) - OPTIONAL_LOCATION_FIELDS
    if unknown or not required.issubset(value):
        raise ValueError("configuration fields do not match the supported schema")

    value = dict(value)
    value.setdefault("location", value["weather_query"])
    value.setdefault("country", "")
    value.setdefault("latitude", None)
    value.setdefault("longitude", None)
    value.setdefault("location_display", value["location_label"])

    config = {}
    for key, limit in STRING_LIMITS.items():
        item = value.get(key)
        if not isinstance(item, str):
            raise ValueError(f"{key} must be text")
        item = item.strip()
        if key == "country" and not item:
            config[key] = ""
            continue
        if not item or len(item) > limit:
            raise ValueError(f"{key} must contain 1-{limit} characters")
        config[key] = item

    latitude = value.get("latitude")
    longitude = value.get("longitude")
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    if latitude is not None:
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
        ):
            raise ValueError("latitude and longitude must be numbers")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude or longitude is out of range")
        config["latitude"] = float(latitude)
        config["longitude"] = float(longitude)
    else:
        config["latitude"] = None
        config["longitude"] = None

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

    kindle_frontlight = value.get("kindle_frontlight")
    if kindle_frontlight is not None:
        if isinstance(kindle_frontlight, bool) or not isinstance(kindle_frontlight, int):
            raise ValueError("kindle_frontlight must be an integer")
        if kindle_frontlight not in (0, 1, 4, 8, 12, 18):
            raise ValueError("kindle_frontlight must be one of: 0, 1, 4, 8, 12, 18")
        config["kindle_frontlight"] = kindle_frontlight
    else:
        config["kindle_frontlight"] = 8

    for field, default, validator in (
        ("prayer_method", 13, lambda x: isinstance(x, int) and (0 <= x <= 23 or x == 99)),
        ("prayer_school", 0, lambda x: isinstance(x, int) and x in (0, 1)),
        ("prayer_high_latitude", 3, lambda x: isinstance(x, int) and x in (1, 2, 3)),
        ("hijri_adjustment", 0, lambda x: isinstance(x, int) and -2 <= x <= 2),
        ("refresh_interval_minutes", 10, lambda x: isinstance(x, int) and x in (5, 10, 15, 30, 60)),
    ):
        val = value.get(field)
        if val is not None:
            if isinstance(val, bool) or not validator(val):
                raise ValueError(f"invalid value for {field}")
            config[field] = val
        else:
            config[field] = default

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


def geocode_locations(query, count=8):
    query = str(query).strip()
    if not query:
        raise ValueError("location query is required")
    geocoding_url = (
        f"{OPEN_METEO_GEOCODING}?"
        + urlencode({
            "name": query,
            "count": max(1, min(int(count), 10)),
            "language": "en",
            "format": "json",
        })
    )
    geocoding = http_json(geocoding_url, timeout=12)
    normalized = []
    seen = set()
    for item in geocoding.get("results") or []:
        try:
            city = str(item["name"]).strip()
            country = str(item["country"]).strip()
            latitude = float(item["latitude"])
            longitude = float(item["longitude"])
            timezone = str(item["timezone"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        region = str(
            item.get("admin1") or item.get("admin2") or ""
        ).strip()
        parts = [city]
        if region and region.casefold() != city.casefold():
            parts.append(region)
        if country and country.casefold() not in {
            part.casefold() for part in parts
        }:
            parts.append(country)
        key = (
            city.casefold(),
            region.casefold(),
            country.casefold(),
            round(latitude, 5),
            round(longitude, 5),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "city": city,
            "region": region,
            "country": country,
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "display_name": ", ".join(parts),
        })
    return normalized


def fetch_open_meteo(query, timezone, latitude=None, longitude=None):
    if latitude is None or longitude is None:
        results = geocode_locations(query, count=1)
        if not results:
            raise ValueError("location was not found")
        latitude = results[0]["latitude"]
        longitude = results[0]["longitude"]

    forecast_url = (
        f"{OPEN_METEO_FORECAST}?"
        + urlencode({
            "latitude": latitude,
            "longitude": longitude,
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


def fetch_weather(query, timezone, latitude=None, longitude=None):
    try:
        weather = fetch_open_meteo(
            query,
            timezone,
            latitude=latitude,
            longitude=longitude,
        )
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


# --- Turkish/English Localized Maarif Calendar Helpers ---

LOCALES = {
    "tr": {
        "hijri_suffix": "Hicri",
        "rumi_suffix": "Rumi",
        "remaining_daylight_label": "Kalan Günışığı",
        "day_lengthening_label": "Günün Uzaması: 2 dk",
        "year_label": "Yıl",
        "month_label": "Ay",
        "day_label": "Gün",
        "weather_label": "HAVA DURUMU",
        "day_length_prefix": "Günün uzunluğu",
        "night_length_prefix": "Gecenin uzunluğu",
        "year_day_suffix": "günü",
        "year_day_prefix": "Yılın",
        "daily_wisdom_label": "GÜNÜN SÖZÜ",
        "credit_label": "Büyük Saatli Maarif Takvimi",
        "prayers": ["Güneş", "Öğle", "İkindi", "Akşam", "Yatsı", "İmsak"],
        "weather_rows": ["Derece", "Durum", "Gün Batımı", "Nem", "Basınç", "Rüzgar"],
        "months": {
            1: "OCAK", 2: "ŞUBAT", 3: "MART", 4: "NİSAN", 5: "MAYIS", 6: "HAZİRAN",
            7: "TEMMUZ", 8: "AĞUSTOS", 9: "EYLÜL", 10: "EKİM", 11: "KASIM", 12: "ARALIK"
        },
        "hijri_months": {
            1: "MUHARREM", 2: "SAFER", 3: "REBİÜLEVVEL", 4: "REBİÜLAHİR",
            5: "CEMAZİYELEVVEL", 6: "CEMAZİYELAHİR", 7: "RECEP", 8: "ŞABAN",
            9: "RAMAZAN", 10: "ŞEVVAL", 11: "ZİLKADE", 12: "ZİLHİCCE"
        },
        "weekdays": {
            0: "PAZARTESİ", 1: "SALI", 2: "ÇARŞAMBA", 3: "PERŞEMBE",
            4: "CUMA", 5: "CUMARTESİ", 6: "PAZAR"
        },
        "seasons": {
            "Hızır": "Hızır",
            "Kasım": "Kasım"
        },
        "weather_desc": {
            "sun": "Açık",
            "partly": "Parçalı Bulutlu",
            "cloud": "Bulutlu",
            "rain": "Yağmurlu",
            "snow": "Karlı",
            "storm": "Fırtınalı",
            "fog": "Sisli"
        },
        "temp_desc": {
            "cold": "Soğuk",
            "mild": "Ilık",
            "warm": "Sıcak",
            "hot": "Çok Sıcak"
        },
        "quotes": [
            ("Doğanın isteklerini anlamamazlıktan gelen", "cezasını görür.", "H. de Balzac"),
            ("İşleyen demir pas tutmaz,", "çalışan insan kötülük düşünmez.", "Atasözü"),
            ("Bir elin nesi var,", "iki elin sesi var.", "Atasözü"),
            ("Dost acı söyler ama", "doğruyu söyler.", "Atasözü"),
            ("Sabır acıdır,", "meyvesi tatlıdır.", "Atasözü"),
            ("Akıl yaşta değil,", "baştadır.", "Atasözü"),
            ("Bugünün işini", "asla yarına bırakma.", "Atasözü"),
            ("Bilmemek ayıp değil,", "öğrenmemek ayıptır.", "Atasözü"),
            ("Birlikten kuvvet doğar,", "dirlik ve düzen gelir.", "Atasözü"),
            ("Komşu komşunun", "külüne muhtaçtır.", "Atasözü"),
            ("Ne ekersen", "onu biçersin.", "Atasözü"),
            ("Gülü seven", "dikenine katlanır.", "Atasözü"),
            ("Damlaya damlaya", "göl olur.", "Atasözü"),
            ("Ayağını yorganına", "göre uzat.", "Atasözü"),
            ("Tatlı dil", "yılanı deliğinden çıkarır.", "Atasözü"),
            ("Sakla samanı,", "gelir zamanı.", "Atasözü"),
            ("Ağaç yaşken", "eğilir.", "Atasözü"),
            ("Öfkeyle kalkan", "zararla oturur.", "Atasözü"),
            ("Güneş balçıkla", "sıvanmaz.", "Atasözü"),
            ("Rüzgâr eken,", "fırtına biçer.", "Atasözü"),
        ],
        "h_unit": "S.",
        "m_unit": "D.",
        "prayer_unavailable": "Namaz vakitleri\nalınamadı"
    },
    "en": {
        "hijri_suffix": "Hijri",
        "rumi_suffix": "Rumi",
        "remaining_daylight_label": "Remaining Daylight",
        "day_lengthening_label": "Day Lengthening: 2 min",
        "year_label": "Year",
        "month_label": "Month",
        "day_label": "Day",
        "weather_label": "WEATHER",
        "day_length_prefix": "Day length",
        "night_length_prefix": "Night length",
        "year_day_suffix": "day of the year",
        "year_day_prefix": "The",
        "daily_wisdom_label": "DAILY WISDOM",
        "credit_label": "Grand Maarif Calendar",
        "prayers": ["Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha", "Fajr"],
        "weather_rows": ["Temp", "Condition", "Sunset", "Humidity", "Pressure", "Wind"],
        "months": {
            1: "JANUARY", 2: "FEBRUARY", 3: "MARCH", 4: "APRIL", 5: "MAY", 6: "JUNE",
            7: "JULY", 8: "AUGUST", 9: "SEPTEMBER", 10: "OCTOBER", 11: "NOVEMBER", 12: "DECEMBER"
        },
        "hijri_months": {
            1: "MUHARRAM", 2: "SAFAR", 3: "RABI I", 4: "RABI II",
            5: "JUMADA I", 6: "JUMADA II", 7: "RAJAB", 8: "SHA'BAN",
            9: "RAMADAN", 10: "SHAWWAL", 11: "DHU'L-QA'DA", 12: "DHU'L-HIJJAH"
        },
        "weekdays": {
            0: "MONDAY", 1: "TUESDAY", 2: "WEDNESDAY", 3: "THURSDAY",
            4: "FRIDAY", 5: "SATURDAY", 6: "SUNDAY"
        },
        "seasons": {
            "Hızır": "Summer",
            "Kasım": "Winter"
        },
        "weather_desc": {
            "sun": "Clear",
            "partly": "Partly Cloudy",
            "cloud": "Cloudy",
            "rain": "Rainy",
            "snow": "Snowy",
            "storm": "Stormy",
            "fog": "Foggy"
        },
        "temp_desc": {
            "cold": "Cold",
            "mild": "Mild",
            "warm": "Warm",
            "hot": "Hot"
        },
        "quotes": [
            ("He who ignores the requests of nature", "receives its punishment.", "H. de Balzac"),
            ("A rolling stone", "gathers no moss.", "Proverb"),
            ("Actions speak", "louder than words.", "Proverb"),
            ("A friend in need", "is a friend indeed.", "Proverb"),
            ("Patience is bitter,", "but its fruit is sweet.", "Proverb"),
            ("Wisdom is not in age,", "but in the head.", "Proverb"),
            ("Never put off till tomorrow", "what you can do today.", "Proverb"),
            ("It is not a shame not to know,", "it is a shame not to learn.", "Proverb"),
            ("Unity makes strength,", "division brings fall.", "Proverb"),
            ("A neighbor needs the smoke", "of his neighbor's chimney.", "Proverb"),
            ("As you sow,", "so shall you reap.", "Proverb"),
            ("He who loves the rose", "endures its thorns.", "Proverb"),
            ("Many a mickle", "makes a muckle.", "Proverb"),
            ("Cut your coat according", "to your cloth.", "Proverb"),
            ("A soft answer", "turns away wrath.", "Proverb"),
            ("No pain,", "no gain.", "Proverb"),
            ("Barking dogs", "seldom bite.", "Proverb"),
            ("Better late", "than never.", "Proverb"),
            ("Truth is stranger", "than fiction.", "Proverb"),
            ("Where there is a will,", "there is a way.", "Proverb"),
        ],
        "h_unit": "h",
        "m_unit": "m",
        "prayer_unavailable": "Prayer times\nunavailable"
    }
}

def get_dashboard_lang(config):
    tz = config.get("timezone", "").lower()
    loc = config.get("location_label", "").lower()
    title = config.get("title", "").lower()
    if "istanbul" in tz or "turkey" in loc or "türkiye" in loc:
        return "tr"
    turkish_keywords = ["ev", "panel", "takvim", "maarif", "istanbul", "ankara", "izmir"]
    if any(kw in title for kw in turkish_keywords) or any(kw in loc for kw in turkish_keywords):
        return "tr"
    return "en"

def draw_star(d, cx, cy, size):
    import math
    points = []
    for i in range(10):
        r = size if i % 2 == 0 else size * 0.4
        angle = i * math.pi / 5 - math.pi / 2
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        points.append((x, y))
    d.polygon(points, fill=0)


def gregorian_to_hijri(g_year, g_month, g_day):
    import datetime
    try:
        g_date = datetime.date(g_year, g_month, g_day)
        epoch = datetime.date(622, 7, 16)
        diff = (g_date - epoch).days
        h_year = int(diff / 354.367068) + 1
        year_days = diff - int((h_year - 1) * 354.367068)
        h_month = int(year_days / 29.530559) + 1
        h_day = int(year_days - int((h_month - 1) * 29.530559)) + 1
        if h_day > 30:
            h_day -= 30
            h_month += 1
        if h_month > 12:
            h_month -= 12
            h_year += 1
        return h_year, h_month, h_day
    except Exception:
        return 1448, 1, 8

def adjust_hijri_date(h_year, h_month, h_day, offset):
    if offset == 0:
        return h_year, h_month, h_day

    def month_days(y, m):
        if m in (1, 3, 5, 7, 9, 11):
            return 30
        if m == 12:
            is_leap = (y * 11 + 14) % 30 < 11
            return 30 if is_leap else 29
        return 29

    h_day += offset
    while h_day <= 0:
        h_month -= 1
        if h_month <= 0:
            h_month = 12
            h_year -= 1
        h_day += month_days(h_year, h_month)

    while h_day > month_days(h_year, h_month):
        h_day -= month_days(h_year, h_month)
        h_month += 1
        if h_month > 12:
            h_month = 1
            h_year += 1

    return h_year, h_month, h_day

def validate_prayer_times(timings):
    required = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha", "Imsak"]
    if not timings or not all(k in timings for k in required):
        return False
    import re
    time_pat = re.compile(r"^\d{2}:\d{2}$")
    for k in required:
        if not isinstance(timings[k], str) or not time_pat.match(timings[k]):
            return False
    try:
        minutes = {}
        for k in required:
            h, m = map(int, timings[k].split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return False
            minutes[k] = h * 60 + m
        # Fajr < Sunrise < Dhuhr < Asr < Maghrib < Isha
        if not (minutes["Fajr"] < minutes["Sunrise"] < minutes["Dhuhr"] < minutes["Asr"] < minutes["Maghrib"] < minutes["Isha"]):
            return False
    except Exception:
        return False
    return True

def gregorian_to_rumi(g_year, g_month, g_day):
    import datetime
    try:
        g_date = datetime.datetime(g_year, g_month, g_day)
        rumi_date = g_date - datetime.timedelta(days=13)
        if rumi_date.month in (1, 2):
            rumi_year = rumi_date.year - 585
        else:
            rumi_year = rumi_date.year - 584
        return rumi_year, rumi_date.month, rumi_date.day
    except Exception:
        return 1442, 6, 20

def turkish_season_info(now_dt):
    import datetime
    try:
        curr_year = now_dt.year
        hizir_start = datetime.date(curr_year, 5, 6)
        kasim_start = datetime.date(curr_year, 11, 8)
        curr_date = datetime.date(now_dt.year, now_dt.month, now_dt.day)
        if curr_date >= hizir_start and curr_date < kasim_start:
            season = "Hızır"
            days = (curr_date - hizir_start).days + 1
        else:
            season = "Kasım"
            if curr_date < hizir_start:
                k_start = datetime.date(curr_year - 1, 11, 8)
            else:
                k_start = kasim_start
            days = (curr_date - k_start).days + 1
        return season, days
    except Exception:
        return "Hızır", 59

def resolve_coordinates(query):
    try:
        geocoding_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urlencode({
                "name": query,
                "count": 1,
                "language": "en",
                "format": "json",
            })
        )
        geocoding = http_json(geocoding_url, timeout=6)
        results = geocoding.get("results") or []
        if results:
            return results[0]["latitude"], results[0]["longitude"]
    except Exception:
        pass
    q = query.lower()
    if "istanbul" in q:
        return 41.0082, 28.9784
    if "ankara" in q:
        return 39.9334, 32.8597
    if "london" in q:
        return 51.5074, -0.1278
    return 52.9548, -1.1581


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
    generate_dashboard_safe()


def collect_dashboard_data(config):
    weather = fetch_weather(
        config["weather_query"],
        config["timezone"],
        latitude=config.get("latitude"),
        longitude=config.get("longitude"),
    )
    current = weather["current_condition"][0]
    days = weather["weather"]
    now = get_now(ZoneInfo(config["timezone"]))
    
    # Auto-detect language
    lang = get_dashboard_lang(config)
    locale = LOCALES[lang]
    
    # Maarif-specific calculations
    lat = config.get("latitude")
    lng = config.get("longitude")
    if lat is None or lng is None:
        try:
            lat, lng = resolve_coordinates(config["weather_query"])
        except Exception:
            lat, lng = 52.9536, -1.1505

    import hashlib
    import json
    from datetime import datetime as dt_class

    local_date = get_local_date(config)
    parts = local_date.split("-")
    date_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
    method = config.get("prayer_method", 13)
    school = config.get("prayer_school", 0)
    high_latitude = config.get("prayer_high_latitude", 3)
    hijri_adj = config.get("hijri_adjustment", 0)

    tz_safe = config.get("timezone", "local").replace("/", "_")
    cache_dir = PROJECT_DIR / "cache" / "prayer_times"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{local_date}_{lat:.4f}_{lng:.4f}_{tz_safe}_{method}_{school}_{high_latitude}.json"

    def load_from_cache():
        if cache_file.exists():
            try:
                c_data = json.loads(cache_file.read_text(encoding="utf-8"))
                timings_cached = {
                    "Fajr": c_data.get("fajr"),
                    "Sunrise": c_data.get("sunrise"),
                    "Dhuhr": c_data.get("dhuhr"),
                    "Asr": c_data.get("asr"),
                    "Maghrib": c_data.get("maghrib"),
                    "Isha": c_data.get("isha"),
                    "Imsak": c_data.get("imsak", "03:42"),
                }
                if validate_prayer_times(timings_cached):
                    return timings_cached, int(c_data.get("hijri_year")), int(c_data.get("hijri_month_num")), int(c_data.get("hijri_day"))
            except Exception:
                pass
        return None

    timings = None
    hijri_year, hijri_month_num, hijri_day = None, None, None

    prayer_data = None
    try:
        url = (
            f"http://api.aladhan.com/v1/timings/{date_str}?"
            f"latitude={lat}&longitude={lng}&method={method}&school={school}"
            f"&latitudeAdjustmentMethod={high_latitude}"
        )
        prayer_data = http_json(url, timeout=7)
    except Exception as e:
        print(f"Failed to fetch prayer times: {e}")

    if prayer_data and prayer_data.get("code") == 200:
        try:
            p_data = prayer_data["data"]
            timings_api = p_data["timings"]
            if validate_prayer_times(timings_api):
                timings = timings_api
                hijri = p_data["date"]["hijri"]
                hijri_day = int(hijri["day"])
                hijri_month_num = int(hijri["month"]["number"])
                hijri_year = int(hijri["year"])

                norm_data = {
                    "date": local_date,
                    "location_display": config.get("location_display", ""),
                    "latitude": float(lat),
                    "longitude": float(lng),
                    "timezone": config["timezone"],
                    "method": method,
                    "school": school,
                    "high_latitude_adjustment": high_latitude,
                    "fajr": timings["Fajr"],
                    "sunrise": timings["Sunrise"],
                    "dhuhr": timings["Dhuhr"],
                    "asr": timings["Asr"],
                    "maghrib": timings["Maghrib"],
                    "isha": timings["Isha"],
                    "imsak": timings.get("Imsak", "03:42"),
                    "hijri_day": hijri_day,
                    "hijri_month_num": hijri_month_num,
                    "hijri_year": hijri_year,
                    "source": "aladhan",
                    "fetched_at": dt_class.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                cache_file.write_text(json.dumps(norm_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Error processing Aladhan API response: {e}")

    if timings is None:
        cached = load_from_cache()
        if cached:
            timings, hijri_year, hijri_month_num, hijri_day = cached

    if timings is not None:
        print(f"Prayer times loaded for {local_date} {config['timezone']}")

    if timings is None:
        hijri_year, hijri_month_num, hijri_day = gregorian_to_hijri(now.year, now.month, now.day)

    if hijri_year is not None:
        hijri_year, hijri_month_num, hijri_day = adjust_hijri_date(
            hijri_year, hijri_month_num, hijri_day, hijri_adj
        )

    # Calendar dates
    rumi_year, rumi_month_num, rumi_day = gregorian_to_rumi(now.year, now.month, now.day)
    hijri_month_name = locale["hijri_months"].get(hijri_month_num, "MUHARREM")
    rumi_month_name = locale["months"].get(rumi_month_num, "OCAK")
    greg_month_localized = locale["months"].get(now.month, "OCAK")
    day_name_localized = locale["weekdays"].get(now.weekday(), "PAZARTESİ")
    
    # Season Info
    season_key, season_day = turkish_season_info(now)
    season_name = locale["seasons"].get(season_key, "Hızır")
    
    # Daylight calculations
    sunrise_time = days[0]["astronomy"][0]["sunrise"][:5]
    sunset_time = days[0]["astronomy"][0]["sunset"][:5]
    try:
        sr_h, sr_m = map(int, sunrise_time.split(":"))
        ss_h, ss_m = map(int, sunset_time.split(":"))
        sr_min = sr_h * 60 + sr_m
        ss_min = ss_h * 60 + ss_m
        day_len_min = ss_min - sr_min
        
        day_h = day_len_min // 60
        day_m = day_len_min % 60
        day_length_str = f"{day_h} {locale['h_unit']} {day_m} {locale['m_unit']}"
        
        night_len_min = 1440 - day_len_min
        night_h = night_len_min // 60
        night_m = night_len_min % 60
        night_length_str = f"{night_h} {locale['h_unit']} {night_m} {locale['m_unit']}"
        
        # Remaining daylight
        now_min = now.hour * 60 + now.minute
        if now_min < sr_min:
            rem_min = day_len_min
        elif now_min > ss_min:
            rem_min = 0
        else:
            rem_min = ss_min - now_min
        rem_h = rem_min // 60
        rem_m = rem_min % 60
        remaining_daylight_str = f"{rem_h} {locale['h_unit']} {rem_m} {locale['m_unit']}"
    except Exception:
        day_length_str = f"12 {locale['h_unit']} 0 {locale['m_unit']}"
        night_length_str = f"12 {locale['h_unit']} 0 {locale['m_unit']}"
        remaining_daylight_str = f"12 {locale['h_unit']} 0 {locale['m_unit']}"

    # Temperature feeling description
    temp_val = int(current["temp_C"])
    if temp_val < 5:
        temp_desc = locale["temp_desc"]["cold"]
    elif temp_val < 15:
        temp_desc = locale["temp_desc"]["mild"]
    elif temp_val < 25:
        temp_desc = locale["temp_desc"]["warm"]
    else:
        temp_desc = locale["temp_desc"]["hot"]
        
    # Weather description translation
    weather_desc_localized = locale["weather_desc"].get(
        weather_kind(current.get("weatherCode")),
        current["weatherDesc"][0]["value"]
    )
        
    # Quote of the day
    day_of_year = now.timetuple().tm_yday
    quote_tuple = locale["quotes"][day_of_year % len(locale["quotes"])]

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
        "sunrise": sunrise_time,
        "sunset": sunset_time,
        "cpu": get_cpu(),
        "ram": get_ram(),
        "disk": get_disk(),
        "ph": get_pihole(),
        "ts": get_tailscale(),
        # Localized Maarif fields
        "hijri_day": hijri_day,
        "hijri_month_name": hijri_month_name,
        "hijri_year": hijri_year,
        "rumi_day": rumi_day,
        "rumi_month_name": rumi_month_name,
        "rumi_year": rumi_year,
        "greg_month_localized": greg_month_localized,
        "day_name_localized": day_name_localized,
        "season_name": season_name,
        "season_day": season_day,
        "timings": timings,
        "day_length": day_length_str,
        "night_length": night_length_str,
        "remaining_daylight": remaining_daylight_str,
        "temp_desc": temp_desc,
        "weather_desc_localized": weather_desc_localized,
        "quote": quote_tuple,
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


def maarif_font(style, weight, size):
    import platform
    if platform.system() == "Darwin":  # macOS
        paths = {
            ("serif", "reg"): "/System/Library/Fonts/Supplemental/Georgia.ttf",
            ("serif", "bold"): "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            ("serif", "italic"): "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
            ("sans", "reg"): "/System/Library/Fonts/Supplemental/Arial.ttf",
            ("sans", "bold"): "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        }
    else:  # Linux (Ubuntu)
        dejavu_dir = "/usr/share/fonts/truetype/dejavu"
        paths = {
            ("serif", "reg"): f"{dejavu_dir}/DejaVuSerif.ttf",
            ("serif", "bold"): f"{dejavu_dir}/DejaVuSerif-Bold.ttf",
            ("serif", "italic"): f"{dejavu_dir}/DejaVuSerif-Italic.ttf",
            ("sans", "reg"): f"{dejavu_dir}/DejaVuSans.ttf",
            ("sans", "bold"): f"{dejavu_dir}/DejaVuSans-Bold.ttf",
        }
    path = paths.get((style, weight))
    try:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        if weight == "bold":
            return ImageFont.truetype(FONT_BOLD, size)
        return ImageFont.truetype(FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


def render_maarif_calendar(config):

    data = collect_dashboard_data(config)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    
    # Resolve active language
    lang = get_dashboard_lang(config)
    locale = LOCALES[lang]
    
    # Fonts
    star_f = maarif_font("sans", "reg", 12)
    sans_reg_14 = maarif_font("sans", "reg", 14)
    sans_reg_16 = maarif_font("sans", "reg", 16)
    sans_reg_18 = maarif_font("sans", "reg", 18)
    sans_bold_16 = maarif_font("sans", "bold", 16)
    sans_bold_18 = maarif_font("sans", "bold", 18)
    sans_bold_20 = maarif_font("sans", "bold", 20)
    sans_bold_44 = maarif_font("sans", "bold", 44)
    sans_bold_56 = maarif_font("sans", "bold", 56)
    serif_reg_14 = maarif_font("serif", "reg", 14)
    serif_bold_20 = maarif_font("serif", "bold", 20)
    serif_italic_18 = maarif_font("serif", "italic", 18)
    
    # 1. Custom polygonal star border (continuous solid black stars)
    # Spaced every ~20px for high-density traditional look
    for i in range(37): # 0 to 36
        x = 20 + int(i * 718 / 36)
        draw_star(d, x, 20, 6)
        draw_star(d, x, 1006, 6)
    for i in range(1, 49): # 1 to 48
        y = 20 + int(i * 984 / 49)
        draw_star(d, 20, y, 6)
        draw_star(d, 738, y, 6)
        
    # 2. Top Header (Date Sections)
    # Left column: Hijri Date
    txt(d, 150, 36, f"{data['hijri_year']} {locale['hijri_suffix']}", sans_reg_16, anchor="ma")
    txt(d, 150, 60, data["hijri_month_name"], sans_bold_18, anchor="ma")
    txt(d, 150, 88, data["hijri_day"], sans_bold_20, anchor="ma")
    
    # Center column: Remaining daylight & daylight info
    txt(d, 379, 36, locale["remaining_daylight_label"], sans_reg_16, anchor="ma")
    txt(d, 379, 60, data["remaining_daylight"], sans_bold_18, anchor="ma")
    txt(d, 379, 88, locale["day_lengthening_label"], sans_reg_14, anchor="ma")
    
    # Right column: Rumi Date
    txt(d, 608, 36, f"{data['rumi_year']} {locale['rumi_suffix']}", sans_reg_16, anchor="ma")
    txt(d, 608, 60, data["rumi_month_name"], sans_bold_18, anchor="ma")
    txt(d, 608, 88, data["rumi_day"], sans_bold_20, anchor="ma")
    
    # Horizontal line below top row
    d.line((24, 115, 734, 115), fill=0, width=2)
    
    # Second row: Gregorian date info
    txt(d, 70, 122, f"{locale['year_label']}: {data['now'].year}", sans_reg_16)
    txt(d, 230, 122, f"{locale['month_label']}: {data['now'].month}", sans_reg_16)
    txt(d, 390, 122, f"{locale['day_label']}: {data['now'].timetuple().tm_yday}", sans_reg_16)
    txt(d, 550, 122, f"{data['season_name']}: {data['season_day']}", sans_reg_16)
    
    # Horizontal line below Gregorian info
    d.line((24, 148, 734, 148), fill=0, width=2)
    
    # 3. Middle Section: Month & Giant Day
    # Gregorian Month Name (centered)
    txt(d, 379, 168, data["greg_month_localized"], sans_bold_56, anchor="ma")
    
    # Giant Day Number (original blueprint rendering logic with 210px safety width cap)
    day_str = str(data["now"].day)
    serif_giant = maarif_font("serif", "bold", 420)
    txt_img = Image.new("L", (800, 600), 255)
    txt_draw = ImageDraw.Draw(txt_img)
    txt_draw.text((400, 300), day_str, fill=0, font=serif_giant, anchor="mm")
    
    import PIL.ImageOps
    inverted = PIL.ImageOps.invert(txt_img)
    bbox = inverted.getbbox()
    if bbox:
        cropped = txt_img.crop(bbox)
        w_crop = bbox[2] - bbox[0]
        h_crop = bbox[3] - bbox[1]
        
        target_h = 420
        target_w = int(w_crop * (target_h / h_crop) * 0.52)
        # Cap to 210px to ensure zero touching/intersection with side boxes (gap is 258px)
        if target_w > 210:
            target_w = 210
            
        resized = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)
        
        paste_x = 379 - target_w // 2
        paste_y = 485 - target_h // 2
        img.paste(resized, (paste_x, paste_y))
    
    # Left Box: Location & Prayer Times
    txt(d, 150, 268, config["location_label"].split(",")[0].upper(), sans_bold_18, anchor="ma")
    box(d, (50, 298, 250, 698), radius=6, width=2)
    
    if data["timings"] is not None:
        prayer_order = [
            (locale["prayers"][0], data["timings"].get("Sunrise", "05:14")),
            (locale["prayers"][1], data["timings"].get("Dhuhr", "13:08")),
            (locale["prayers"][2], data["timings"].get("Asr", "17:02")),
            (locale["prayers"][3], data["timings"].get("Maghrib", "20:45")),
            (locale["prayers"][4], data["timings"].get("Isha", "22:15")),
            (locale["prayers"][5], data["timings"].get("Imsak", "03:42")),
        ]
        
        for i, (name, tm) in enumerate(prayer_order):
            y_pos = 312 + i * 62
            txt(d, 150, y_pos, name, sans_bold_16, anchor="ma")
            txt(d, 150, y_pos + 22, tm, sans_reg_18, anchor="ma")
    else:
        msg = locale.get("prayer_unavailable", "Prayer times\nunavailable")
        lines = msg.split("\n")
        txt(d, 150, 480, lines[0], sans_bold_16, anchor="ma")
        if len(lines) > 1:
            txt(d, 150, 510, lines[1], sans_bold_16, anchor="ma")
        
    # Right Box: Weather Stats
    txt(d, 608, 268, locale["weather_label"], sans_bold_18, anchor="ma")
    box(d, (508, 298, 708, 698), radius=6, width=2)
    
    weather_rows = [
        (locale["weather_rows"][0], f"{data['temp']}°C"),
        (locale["weather_rows"][1], data["weather_desc_localized"]),
        (locale["weather_rows"][2], data["sunset"]),
        (locale["weather_rows"][3], f"%{data['humidity']}"),
        (locale["weather_rows"][4], f"{data['pressure']} hPa"),
        (locale["weather_rows"][5], f"{data['wind']} mph"),
    ]
    
    for i, (label, val) in enumerate(weather_rows):
        y_pos = 312 + i * 62
        txt(d, 608, y_pos, label, sans_bold_16, anchor="ma")
        txt(d, 608, y_pos + 22, val, sans_reg_18, anchor="ma")
        
    # 4. Below Middle Section
    d_len_str = f"{locale['day_length_prefix']}: {data['day_length']}  —  {locale['night_length_prefix']}: {data['night_length']}"
    txt(d, 379, 722, d_len_str, sans_reg_16, anchor="ma")
    
    # Large Day Name
    txt(d, 379, 760, data["day_name_localized"], sans_bold_44, anchor="ma")
    
    # Parentheses info lines
    p_line1 = f"({data['weather_desc_localized']} · {data['temp_desc']})"
    txt(d, 379, 818, p_line1, sans_reg_16, anchor="ma")
    p_line2 = f"({locale['year_day_prefix']} {data['now'].timetuple().tm_yday}. {locale['year_day_suffix']})"
    txt(d, 379, 844, p_line2, sans_reg_14, anchor="ma")
    
    # Horizontal line above quote
    d.line((24, 874, 734, 874), fill=0, width=2)
    
    # 5. Wisdom Quote
    txt(d, 379, 890, locale["daily_wisdom_label"], serif_bold_20, anchor="ma")
    q_line1, q_line2, q_author = data["quote"]
    txt(d, 379, 916, f"“{q_line1}”", serif_italic_18, anchor="ma")
    if q_line2:
        txt(d, 379, 938, f"“{q_line2}”", serif_italic_18, anchor="ma")
        txt(d, 379, 962, f"— {q_author}", serif_reg_14, anchor="ma")
    else:
        txt(d, 379, 950, f"— {q_author}", serif_reg_14, anchor="ma")
        
    # Bottom separator line
    d.line((24, 980, 734, 980), fill=0, width=2)
    
    # Credit at very bottom
    txt(d, 379, 990, locale["credit_label"], serif_reg_14, anchor="ma")
    
    save_dashboard(img, data)



THEME_RENDERERS = {
    "home_dashboard": render_home_dashboard,
    "minimal_weather": render_minimal_weather,
    "server_monitor": render_server_monitor,
    "travel_weather": render_travel_weather,
    "maarif_calendar": render_maarif_calendar,
}


def generate_dashboard():
    config = load_config()
    renderer = THEME_RENDERERS.get(config["theme"])
    if renderer is None:
        raise ValueError("theme renderer is not available")
    renderer(config)
    
    try:
        local_date = get_local_date(config)
        rendered_at = get_now().isoformat()
        state = {
            "theme": config.get("theme"),
            "timezone": config.get("timezone"),
            "local_date": local_date,
            "latitude": config.get("latitude"),
            "longitude": config.get("longitude"),
            "config_hash": compute_config_hash(config),
            "rendered_at": rendered_at
        }
        state_file = PROJECT_DIR / "cache" / "render_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to save render state: {e}")


def generate_dashboard_safe():
    with LOCK_PATH.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        generate_dashboard()


if __name__ == "__main__":
    main()

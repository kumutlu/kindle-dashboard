#!/usr/bin/env python3
import hmac
import html
import json
import os
import secrets
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from dashboard_themes import THEMES
from kindle_device import DeviceError, KindleDevice
from weather_image import (
    DEFAULT_CONFIG,
    geocode_locations,
    load_config,
    validate_config,
)


BIND_HOST = "0.0.0.0"
PORT = 8767
PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "dashboard_config.json"
RUN_DASHBOARD = PROJECT_DIR / "run_dashboard.sh"
MAX_REQUEST_BYTES = 16 * 1024

CITY_DATA = [
    ("Nottingham", "United Kingdom", "Nottingham, UK", "Europe/London",
     "NOTTINGHAM HOME"),
    ("Leicester", "United Kingdom", "Leicester, UK", "Europe/London",
     "LEICESTER DASHBOARD"),
    ("London", "United Kingdom", "London, UK", "Europe/London",
     "LONDON DASHBOARD"),
    ("Birmingham", "United Kingdom", "Birmingham, UK", "Europe/London",
     "BIRMINGHAM DASHBOARD"),
    ("Manchester", "United Kingdom", "Manchester, UK", "Europe/London",
     "MANCHESTER DASHBOARD"),
    ("Oxford", "United Kingdom", "Oxford, UK", "Europe/London",
     "OXFORD DASHBOARD"),
    ("Reading", "United Kingdom", "Reading, UK", "Europe/London",
     "READING DASHBOARD"),
    ("Lincoln", "United Kingdom", "Lincoln, UK", "Europe/London",
     "LINCOLN DASHBOARD"),
    ("Istanbul", "Türkiye", "Istanbul, Türkiye", "Europe/Istanbul",
     "ISTANBUL DASHBOARD"),
    ("Ankara", "Türkiye", "Ankara, Türkiye", "Europe/Istanbul",
     "ANKARA DASHBOARD"),
    ("Izmir", "Türkiye", "Izmir, Türkiye", "Europe/Istanbul",
     "IZMIR DASHBOARD"),
    ("Antalya", "Türkiye", "Antalya, Türkiye", "Europe/Istanbul",
     "ANTALYA DASHBOARD"),
    ("Amsterdam", "Netherlands", "Amsterdam, Netherlands",
     "Europe/Amsterdam", "AMSTERDAM DASHBOARD"),
]

COMMON_TIMEZONES = (
    "Europe/London",
    "Europe/Istanbul",
    "Europe/Amsterdam",
    "Europe/Berlin",
    "Europe/Paris",
    "UTC",
)


def atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_config(path, config):
    validated = validate_config(config)
    data = (
        json.dumps(validated, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    atomic_write_bytes(path, data)


def regenerate_dashboard():
    result = subprocess.run(
        [str(RUN_DASHBOARD)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("dashboard regeneration failed")


def terminate_settings_process():
    os._exit(1)


def schedule_settings_restart():
    timer = threading.Timer(0.35, terminate_settings_process)
    timer.daemon = True
    timer.start()


def update_config(config_path, candidate, regenerate):
    config_path = Path(config_path)
    previous_exists = config_path.exists()
    previous_data = config_path.read_bytes() if previous_exists else None

    # Preserve custom Maarif fields from existing config if not in candidate
    for field in ("kindle_frontlight", "prayer_method", "prayer_school", "prayer_high_latitude", "hijri_adjustment"):
        if previous_exists and field not in candidate:
            try:
                prev_config = json.loads(previous_data.decode("utf-8"))
                if field in prev_config:
                    candidate[field] = prev_config[field]
            except Exception:
                pass

    validated = validate_config(candidate)
    atomic_write_config(config_path, validated)
    try:
        regenerate()
    except Exception:
        if previous_exists:
            atomic_write_bytes(config_path, previous_data)
        else:
            config_path.unlink(missing_ok=True)
        raise
    return validated


def get_prayer_cache_status(config):
    try:
        import hashlib
        from zoneinfo import ZoneInfo
        from datetime import datetime
        lat = config.get("latitude")
        lng = config.get("longitude")
        if lat is None or lng is None:
            return "Unavailable (Missing coordinates)", "Never"
        timezone = config.get("timezone", "Europe/London")
        method = config.get("prayer_method", 13)
        school = config.get("prayer_school", 0)
        high_latitude = config.get("prayer_high_latitude", 3)

        now = datetime.now(ZoneInfo(timezone))
        date_str = now.strftime("%d-%m-%Y")

        project_dir = Path(__file__).resolve().parent
        cache_dir = project_dir / "cache" / "prayer_times"
        key_string = f"{date_str}_{lat:.4f}_{lng:.4f}_{timezone}_{method}_{school}_{high_latitude}"
        cache_filename = f"prayer_{hashlib.md5(key_string.encode('utf-8')).hexdigest()}.json"
        cache_file = cache_dir / cache_filename

        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return "Cached (API)", data.get("fetched_at", "Unknown")
    except Exception:
        pass
    return "Not cached / Pending fetch", "Never"


def render_settings(config, csrf_token, status_message=""):
    escaped = {key: html.escape(str(value), quote=True)
               for key, value in config.items()}
    latitude_value = (
        "" if config["latitude"] is None else str(config["latitude"])
    )
    longitude_value = (
        "" if config["longitude"] is None else str(config["longitude"])
    )

    def checked(key):
        return " checked" if config[key] else ""

    def selected_opt(key, val):
        return " selected" if str(config.get(key)) == str(val) else ""

    prayer_status, prayer_last_update = get_prayer_cache_status(config)

    theme_cards = "".join(
        f'<label class="theme-choice{" disabled" if not definition["implemented"] else ""}">'
        f'<input type="radio" name="theme" value="{html.escape(theme, quote=True)}"'
        f'{" checked" if config["theme"] == theme else ""}'
        f'{" disabled" if not definition["implemented"] else ""}>'
        f'<span><strong>{html.escape(definition["label"])}</strong>'
        f'<small>{html.escape(definition["description"])}</small></span></label>'
        for theme, definition in THEMES.items()
    )
    message = (
        f'<p class="message" role="status">{html.escape(status_message)}</p>'
        if status_message else ""
    )
    device_buttons = "".join(
        f'<button type="button" data-device-action="{action}">{label}</button>'
        for action, label in (
            ("start-dashboard", "Start Dashboard"),
            ("home", "Return Home"),
            ("refresh", "Refresh Now"),
            ("autostart/enable", "Enable Autostart"),
            ("autostart/disable", "Disable Autostart"),
        )
    )
    light_buttons = "".join(
        f'<button type="button" data-light="{level}">{label}</button>'
        for level, label in (
            (0, "Light Off"), (1, "Light 1"), (4, "Light 4"),
            (8, "Light 8"), (12, "Light 12"), (18, "Light 18"),
        )
    )
    saved_brightness = str(config.get("kindle_frontlight", 8))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Kindle Dash">
<meta name="theme-color" content="#111111">
<link rel="icon" href="data:,">
<title>Kindle Dashboard</title>
<style>
:root{{
  --bg:#f5f6f8;
  --card:#ffffff;
  --ink:#111111;
  --muted:#6e767f;
  --line:#e1e4e8;
  --accent:#2b6cb0;
  --soft:#f8f9fa;
  --border-radius:16px;
}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}}
.shell{{max-width:900px;margin:0 auto;padding:24px 16px 140px}}
.app-header{{margin-bottom:24px;text-align:center}}
.app-header h1{{font-size:1.8rem;font-weight:800;margin:0 0 6px;letter-spacing:-0.025em}}
.subtitle{{margin:0;color:var(--muted);font-size:0.95rem}}

/* Tabs Navigation */
.tabs-nav{{display:flex;gap:8px;overflow-x:auto;padding:4px;margin-bottom:24px;background:var(--soft);border-radius:14px;border:1px solid var(--line);scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch}}
.tabs-nav::-webkit-scrollbar{{display:none}}
.tabs-nav{{-ms-overflow-style:none;scrollbar-width:none}}
.tab-btn{{flex:0 0 auto;scroll-snap-align:start;min-height:40px;padding:8px 16px;border:none;border-radius:10px;background:transparent;color:var(--muted);font-size:0.95rem;font-weight:600;cursor:pointer;transition:all 0.2s ease}}
.tab-btn:hover{{color:var(--ink);background:rgba(0,0,0,0.04)}}
.tab-btn.active{{color:var(--ink);background:var(--card);box-shadow:0 2px 8px rgba(0,0,0,0.06)}}

/* Tab Section Visibility */
.tab-content{{display:none}}
.tab-content.active{{display:block}}

/* Section Card */
.card{{background:var(--card);border:1px solid var(--line);border-radius:var(--border-radius);padding:24px;box-shadow:0 4px 20px rgba(0,0,0,0.03)}}
.card h2{{font-size:1.3rem;font-weight:750;margin:0 0 8px;letter-spacing:-0.015em}}
.section-note{{margin:0 0 20px!important;color:var(--muted);font-size:0.9rem;line-height:1.45}}

/* Form Fields */
.field{{display:block;margin-bottom:18px}}
.field span{{display:block;margin-bottom:8px;font-weight:650;font-size:0.9rem}}
input[type=text],input[type=search],input[type=number],select{{width:100%;min-height:46px;padding:10px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-size:0.95rem;transition:all 0.2s ease}}
input:focus,select:focus{{outline:none;border-color:var(--ink);box-shadow:0 0 0 3px rgba(0,0,0,0.05)}}

/* Buttons */
.button-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:16px}}
button{{min-height:46px;padding:10px 16px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-weight:650;font-size:0.92rem;cursor:pointer;transition:all 0.2s ease}}
button:hover:not(:disabled){{background:var(--soft);border-color:#a0aec0}}
button:active:not(:disabled){{transform:translateY(1px)}}
button:disabled{{color:var(--muted);background:var(--soft);cursor:not-allowed;opacity:0.65}}

/* Overview Dashboard */
.overview-stats{{display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:24px}}
.stat-item{{padding:14px;background:var(--soft);border-radius:12px;border:1px solid var(--line)}}
.stat-item small{{display:block;color:var(--muted);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px}}
.stat-item strong{{display:block;font-size:1.05rem;font-weight:700}}
.match{{margin:-4px 0 14px;padding:11px 12px;border-radius:12px;background:var(--soft);color:var(--muted);font-size:0.9rem;border:1px solid var(--line)}}

/* City Results */
.city-results{{display:grid;gap:8px;margin:0 0 14px}}
.city-result{{display:grid;gap:3px;width:100%;min-height:58px;text-align:left;padding:10px 12px;border-color:var(--line)}}
.city-result strong{{font-size:.95rem}}.city-result small{{color:var(--muted);font-weight:500}}
.search-state{{padding:10px 12px;color:var(--muted);background:var(--soft);border-radius:12px;border:1px solid var(--line)}}

/* Theme Selection Cards */
.theme-list{{display:grid;gap:12px;margin-top:14px}}
.theme-choice{{display:flex;align-items:center;gap:14px;padding:14px 16px;border:1px solid var(--line);border-radius:12px;cursor:pointer;transition:all 0.2s ease}}
.theme-choice:hover:not(.disabled){{border-color:#a0aec0;background:var(--soft)}}
.theme-choice:has(input:checked){{border-color:var(--ink);border-width:2px;padding:13px 15px;background:var(--soft)}}
.theme-choice input[type=radio]{{width:20px;height:20px;accent-color:var(--ink);margin:0;flex:0 0 auto}}
.theme-choice span{{display:flex;flex-direction:column;gap:2px}}
.theme-choice strong{{font-size:1rem;font-weight:700}}
.theme-choice small{{color:var(--muted);font-size:0.85rem}}
.theme-choice.disabled{{opacity:0.5;cursor:not-allowed}}

/* Display Toggles */
.toggle-list{{display:grid;grid-template-columns:1fr;gap:12px}}
.toggle{{display:flex;align-items:center;gap:12px;padding:12px 16px;border:1px solid var(--line);border-radius:12px;font-weight:600;font-size:0.95rem;cursor:pointer;transition:all 0.2s ease}}
.toggle:hover{{background:var(--soft)}}
.toggle input[type=checkbox]{{width:22px;height:22px;margin:0;accent-color:var(--ink);flex:0 0 auto}}

/* Device Tab */
.device-state{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}}
.device-stat{{padding:12px 8px;background:var(--soft);border:1px solid var(--line);border-radius:12px;text-align:center}}
.device-stat small{{display:block;color:var(--muted);font-size:0.72rem;text-transform:uppercase;margin-bottom:4px}}
.device-stat strong{{display:block;font-size:0.95rem;font-weight:750}}
.device-message{{padding:12px 14px;background:var(--soft);border-radius:12px;font-size:0.9rem;margin:0 0 16px!important;border:1px solid var(--line)}}
.light-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}}
.log-box{{max-height:280px;overflow:auto;margin-top:14px;padding:16px;border-radius:12px;background:#1a202c;color:#edf2f7;font-family:SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:0.8rem;line-height:1.5;white-space:pre-wrap;border:1px solid #2d3748}}

/* Maintenance Tab */
.maintenance-message{{margin-top:12px;color:var(--muted);font-size:0.88rem}}

/* Status Tab */
.status-list{{display:grid;gap:10px;margin:0}}
.status-row{{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid var(--line)}}
.status-row:last-child{{border-bottom:0}}
.status-row dt{{color:var(--muted);font-size:0.92rem}}
.status-row dd{{margin:0;text-align:right;font-weight:700;font-size:0.92rem}}

/* Action Bar */
.action-bar{{position:fixed;z-index:100;left:0;right:0;bottom:0;display:grid;grid-template-columns:1.35fr 1fr;gap:12px;padding:14px 16px calc(14px + env(safe-area-inset-bottom));background:rgba(255, 255, 255, 0.96);border-top:1px solid var(--line);box-shadow:0 -8px 30px rgba(0, 0, 0, 0.08);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}}
.action-bar button{{margin:0;width:100%}}
.action-bar button[type=submit],.overview-actions button[type=submit]{{background:var(--ink);color:var(--card);border-color:var(--ink)}}
.action-bar button[type=submit]:hover:not(:disabled),.overview-actions button[type=submit]:hover:not(:disabled){{background:#2d3748;border-color:#2d3748}}

.advanced{{margin-top:14px;border-top:1px solid var(--line);padding-top:14px}}
.advanced summary{{min-height:44px;display:flex;align-items:center;font-weight:750;cursor:pointer}}
.future-box{{margin-top:16px;padding:14px;background:var(--soft);border-radius:14px;border:1px solid var(--line)}}
.future-box h3{{margin:0 0 4px;font-size:.95rem}}
.future-box p{{color:var(--muted);font-size:.86rem}}
.future-box input:disabled{{opacity:.65}}

/* Desktop Styles */
@media (min-width: 760px){{
  .shell{{padding:40px 24px 160px}}
  .overview-stats{{grid-template-columns:repeat(3,1fr)}}
  .toggle-list{{grid-template-columns:1fr 1fr}}
  .city-results{{grid-template-columns:1fr 1fr}}
  .action-bar{{left:50%;right:auto;bottom:24px;width:min(600px,calc(100% - 32px));transform:translateX(-50%);border:1px solid var(--line);border-radius:16px;padding:10px;box-shadow:0 8px 30px rgba(0,0,0,0.12)}}
}}
</style>
</head>
<body>
<main class="shell">
<header class="app-header">
<h1>Kindle Dashboard</h1>
<p class="subtitle">{escaped['location_label']} · {escaped['theme']}</p>
</header>
{message}

<nav class="tabs-nav" aria-label="Dashboard sections">
  <button type="button" class="tab-btn active" data-tab="overview">Overview</button>
  <button type="button" class="tab-btn" data-tab="location">Location</button>
  <button type="button" class="tab-btn" data-tab="theme">Theme</button>
  <button type="button" class="tab-btn" data-tab="display">Display</button>
  <button type="button" class="tab-btn" data-tab="device">Device</button>
  <button type="button" class="tab-btn" data-tab="maintenance">Maintenance</button>
  <button type="button" class="tab-btn" data-tab="status">Status</button>
</nav>

<form method="post" action="/settings">
<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">

<!-- TAB CONTENTS -->

<!-- 1. Overview Tab -->
<section class="card tab-content active" id="overview">
  <h2>Overview</h2>
  <p class="section-note">Quick summary and primary dashboard actions.</p>
  <div class="overview-stats">
    <div class="stat-item">
      <small>Location</small>
      <strong>{escaped['location_label']}</strong>
    </div>
    <div class="stat-item">
      <small>Theme</small>
      <strong>{escaped['theme']}</strong>
    </div>
    <div class="stat-item">
      <small>Last Generated</small>
      <strong>{html.escape(status_message or 'No result in this session')}</strong>
    </div>
    <div class="stat-item">
      <small>Server Status</small>
      <strong style="color: #2f855a;">Online</strong>
    </div>
    <div class="stat-item">
      <small>Kindle Connection</small>
      <strong id="overview-kindle-connection">Checking…</strong>
    </div>
  </div>
  <div class="button-grid overview-actions">
    <button type="submit">Save &amp; Regenerate</button>
    <button type="button" id="overview-push-kindle-btn">Push to Kindle</button>
  </div>
</section>

<!-- 2. Location Tab -->
<section class="card tab-content" id="location">
  <h2>Location</h2>
  <p class="section-note">Search for a city, then select the correct result.</p>
  <label class="field"><span>Search city</span><input type="search" id="city-search" value="{escaped['location']}" placeholder="Nottingham, Istanbul, London…" autocomplete="off"></label>
  <div class="city-results" id="city-results" aria-live="polite"></div>
  <div class="match" id="city-match">Selected: {escaped['location_display']} · {escaped['timezone']}</div>
  <details class="advanced">
    <summary>Advanced location settings</summary>
    <label class="field"><span>Dashboard title</span><input type="text" name="title" maxlength="28" value="{escaped['title']}" required></label>
    <label class="field"><span>City</span><input type="text" name="location" maxlength="100" value="{escaped['location']}" required></label>
    <label class="field"><span>Country</span><input type="text" name="country" maxlength="100" value="{escaped['country']}"></label>
    <label class="field"><span>Latitude</span><input type="number" name="latitude" step="any" min="-90" max="90" value="{html.escape(latitude_value, quote=True)}"></label>
    <label class="field"><span>Longitude</span><input type="number" name="longitude" step="any" min="-180" max="180" value="{html.escape(longitude_value, quote=True)}"></label>
    <label class="field"><span>Display name</span><input type="text" name="location_display" maxlength="160" value="{escaped['location_display']}" required></label>
    <label class="field"><span>Weather query</span><input type="text" name="weather_query" maxlength="100" value="{escaped['weather_query']}" required></label>
    <label class="field"><span>Location label</span><input type="text" name="location_label" maxlength="160" value="{escaped['location_label']}" required></label>
    <label class="field"><span>Timezone</span><input type="text" name="timezone" maxlength="64" value="{escaped['timezone']}" required></label>
    <div class="future-box" style="margin-top:20px;border-top:1px solid var(--line);padding-top:16px">
      <h3 style="font-size:1.05rem;font-weight:700;margin:0 0 12px">Maarif / Prayer Settings</h3>
      <label class="field"><span>Prayer calculation method</span>
        <select name="prayer_method">
          <option value="13"{selected_opt('prayer_method', 13)}>Turkey (Diyanet)</option>
          <option value="1"{selected_opt('prayer_method', 1)}>Karachi (Univ of Islamic Sciences)</option>
          <option value="2"{selected_opt('prayer_method', 2)}>ISNA (North America)</option>
          <option value="3"{selected_opt('prayer_method', 3)}>MWL (Muslim World League)</option>
          <option value="4"{selected_opt('prayer_method', 4)}>Umm Al-Qura (Makkah)</option>
          <option value="5"{selected_opt('prayer_method', 5)}>Egyptian Authority</option>
          <option value="7"{selected_opt('prayer_method', 7)}>Tehran (Univ of Geophysics)</option>
          <option value="8"{selected_opt('prayer_method', 8)}>Gulf Region</option>
          <option value="9"{selected_opt('prayer_method', 9)}>Kuwait</option>
          <option value="10"{selected_opt('prayer_method', 10)}>Qatar</option>
          <option value="11"{selected_opt('prayer_method', 11)}>Singapore (MUIS)</option>
          <option value="12"{selected_opt('prayer_method', 12)}>France (UOIF)</option>
          <option value="14"{selected_opt('prayer_method', 14)}>Russia (SAMR)</option>
        </select>
      </label>
      <label class="field"><span>Asr school</span>
        <select name="prayer_school">
          <option value="0"{selected_opt('prayer_school', 0)}>Standard (Shafi, Maliki, Hanbali)</option>
          <option value="1"{selected_opt('prayer_school', 1)}>Hanafi</option>
        </select>
      </label>
      <label class="field"><span>High latitude adjustment</span>
        <select name="prayer_high_latitude">
          <option value="3"{selected_opt('prayer_high_latitude', 3)}>Angle Based (Default)</option>
          <option value="1"{selected_opt('prayer_high_latitude', 1)}>Middle of the Night</option>
          <option value="2"{selected_opt('prayer_high_latitude', 2)}>One Seventh</option>
        </select>
      </label>
      <label class="field"><span>Hijri date adjustment</span>
        <select name="hijri_adjustment">
          <option value="0"{selected_opt('hijri_adjustment', 0)}>No adjustment (0)</option>
          <option value="-2"{selected_opt('hijri_adjustment', -2)}>Subtract 2 days (-2)</option>
          <option value="-1"{selected_opt('hijri_adjustment', -1)}>Subtract 1 day (-1)</option>
          <option value="1"{selected_opt('hijri_adjustment', 1)}>Add 1 day (+1)</option>
          <option value="2"{selected_opt('hijri_adjustment', 2)}>Add 2 days (+2)</option>
        </select>
      </label>
    </div>
  </details>
</section>

<!-- 3. Theme Tab -->
<section class="card tab-content" id="theme">
  <h2>Theme</h2>
  <p class="section-note">Choose the dashboard’s visual focus.</p>
  <div class="theme-list">{theme_cards}</div>
</section>

<!-- 4. Display Tab -->
<section class="card tab-content" id="display">
  <h2>Display</h2>
  <p class="section-note">Choose what appears on Home Dashboard.</p>
  <div class="toggle-list">
    <label class="toggle"><input type="checkbox" name="show_weather"{checked('show_weather')}> <span>Weather</span></label>
    <label class="toggle"><input type="checkbox" name="show_forecast"{checked('show_forecast')}> <span>Forecast</span></label>
    <label class="toggle"><input type="checkbox" name="show_server"{checked('show_server')}> <span>Server status</span></label>
    <label class="toggle"><input type="checkbox" name="show_pihole"{checked('show_pihole')}> <span>Pi-hole</span></label>
    <label class="toggle"><input type="checkbox" name="show_tailscale"{checked('show_tailscale')}> <span>Tailscale</span></label>
  </div>
</section>

<!-- 5. Device Tab -->
<section class="card tab-content" id="device">
  <h2>Device Controls</h2>
  <p class="section-note">Autostart controls, front light levels, and device actions.</p>
  <div class="device-state">
    <div class="device-stat"><small>Connection</small><strong id="kindle-connection">Checking…</strong></div>
    <div class="device-stat"><small>Brightness</small><strong id="kindle-brightness">—</strong></div>
    <div class="device-stat"><small>Autostart</small><strong id="kindle-autostart">—</strong></div>
  </div>
  <p class="device-message" id="device-message" role="status">Ready</p>
  <div class="button-grid">{device_buttons}</div>
  <h3 style="margin-top:20px;font-size:1.1rem;font-weight:700">Default front light level</h3>
  <p class="section-note" style="margin-bottom:10px">Selected level will be persistently saved to configuration and reapplied automatically.</p>
  <p class="device-message" style="margin-bottom:14px;background:var(--soft);border:1px solid var(--line)" id="persistent-light-display">Current saved default: <strong>{saved_brightness}</strong></p>
  <div class="light-grid">{light_buttons}</div>
  <button type="button" id="restart-kindle" style="width:100%;border-color:#e53e3e;color:#e53e3e;background:#fff5f5">Restart Kindle</button>
</section>

<!-- 6. Maintenance Tab -->
<section class="card tab-content" id="maintenance">
  <h2>Advanced / Maintenance</h2>
  <p class="section-note">Occasional server maintenance actions and recent logs.</p>
  <button type="button" id="restart-settings-server" style="width:100%;margin-bottom:12px;border-color:#dd6b20;color:#dd6b20;background:#fffaf0">Restart Settings Server</button>
  <p class="maintenance-message" id="maintenance-message" role="status"></p>
  <h3 style="margin-top:20px;font-size:1.1rem;font-weight:700">Recent dashboard log</h3>
  <pre class="log-box" id="device-log">Loading…</pre>
</section>

<!-- 7. Status Tab -->
<section class="card tab-content" id="status">
  <h2>Status</h2>
  <p class="section-note">Current server environment and settings info.</p>
  <dl class="status-list">
    <div class="status-row"><dt>Current title</dt><dd>{escaped['title']}</dd></div>
    <div class="status-row"><dt>Weather query</dt><dd>{escaped['weather_query']}</dd></div>
    <div class="status-row"><dt>Location label</dt><dd>{escaped['location_label']}</dd></div>
    <div class="status-row"><dt>Timezone</dt><dd>{escaped['timezone']}</dd></div>
    <div class="status-row"><dt>Selected theme</dt><dd>{escaped['theme']}</dd></div>
    <div class="status-row"><dt>Prayer data status</dt><dd>{prayer_status}</dd></div>
    <div class="status-row"><dt>Last prayer update</dt><dd>{prayer_last_update}</dd></div>
    <div class="status-row"><dt>Last generation</dt><dd>{html.escape(status_message or 'No result in this session')}</dd></div>
    <div class="status-row"><dt>Last push</dt><dd id="last-push">Not in this session</dd></div>
  </dl>
</section>

<nav class="bottom-nav" aria-label="Dashboard sections" style="display:none">
  <a href="#location">Settings</a>
  <a href="#theme">Theme</a>
  <a href="#device">Device</a>
  <a href="#status">Status</a>
</nav>
<div class="action-bar">
  <button type="submit">Save &amp; Regenerate</button>
  <button type="button" id="push-kindle">Push to Kindle</button>
</div>
</div>
</form>
</main>
<script>
const tabBtns=document.querySelectorAll(".tab-btn");
const tabContents=document.querySelectorAll(".tab-content");
function switchTab(tabId){{
  tabBtns.forEach(btn=>btn.classList.toggle("active",btn.dataset.tab===tabId));
  tabContents.forEach(content=>content.classList.toggle("active",content.id===tabId));
  localStorage.setItem("active_tab",tabId);
  window.location.hash=tabId;
}}
tabBtns.forEach(btn=>btn.addEventListener("click",()=>switchTab(btn.dataset.tab)));
const initialTab=window.location.hash.slice(1)||localStorage.getItem("active_tab")||"overview";
if(document.getElementById(initialTab)){{
  switchTab(initialTab);
}}else{{
  switchTab("overview");
}}
window.addEventListener("hashchange",()=>{{
  const tabId=window.location.hash.slice(1);
  if(document.getElementById(tabId)) switchTab(tabId);
}});

const overviewPushBtn=document.getElementById("overview-push-kindle-btn");
if(overviewPushBtn){{
  overviewPushBtn.addEventListener("click",()=>{{
    document.getElementById("push-kindle").click();
  }});
}}

const citySearch=document.getElementById("city-search");
const cityResults=document.getElementById("city-results");
const cityMatch=document.getElementById("city-match");
const prayerLocation=document.getElementById("prayer-location");
const prayerCountry=document.getElementById("prayer-country");
let citySearchTimer;
let citySearchController;
function setLocationField(name,value){{
  document.querySelector(`[name="${{name}}"]`).value=value;
}}
function selectCity(result){{
  citySearch.value=result.city;
  setLocationField("location",result.city);
  setLocationField("country",result.country);
  setLocationField("latitude",result.latitude);
  setLocationField("longitude",result.longitude);
  setLocationField("location_display",result.display_name);
  setLocationField("weather_query",result.city);
  setLocationField("location_label",result.display_name);
  setLocationField("timezone",result.timezone);
  setLocationField(
    "title",
    result.city.toLowerCase()==="nottingham"
      ?"NOTTINGHAM HOME"
      :`${{result.city.toUpperCase()}} DASHBOARD`.slice(0,28),
  );
  prayerLocation.value=result.city;
  prayerCountry.value=result.country;
  cityMatch.textContent=`Selected: ${{result.display_name}} · ${{result.timezone}}`;
  cityResults.replaceChildren();
}}
function renderCityResults(results){{
  cityResults.replaceChildren();
  if(!results.length){{
    const empty=document.createElement("div");
    empty.className="search-state";
    empty.textContent="No matching cities found. Use Advanced location settings for manual entry.";
    cityResults.append(empty);
    return;
  }}
  results.forEach(result=>{{
    const button=document.createElement("button");
    button.type="button";
    button.className="city-result";
    const name=document.createElement("strong");
    name.textContent=result.display_name;
    const coordinates=document.createElement("small");
    coordinates.textContent=`${{result.latitude.toFixed(4)}}, ${{result.longitude.toFixed(4)}} · ${{result.timezone}}`;
    button.append(name,coordinates);
    button.addEventListener("click",()=>selectCity(result));
    cityResults.append(button);
  }});
}}
async function searchCities(query){{
  query=query.trim();
  if(!query){{
    cityResults.replaceChildren();
    return;
  }}
  if(citySearchController) citySearchController.abort();
  citySearchController=new AbortController();
  cityResults.innerHTML='<div class="search-state">Searching…</div>';
  try{{
    const response=await fetch(`/api/geocode?q=${{encodeURIComponent(query)}}`,{{
      signal:citySearchController.signal,
      cache:"no-store",
    }});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error||"Location search failed");
    renderCityResults(data.results);
  }}catch(error){{
    if(error.name==="AbortError") return;
    cityResults.innerHTML="";
    const failure=document.createElement("div");
    failure.className="search-state";
    failure.textContent=error.message;
    cityResults.append(failure);
  }}
}}
citySearch.addEventListener("input",()=>{{
  clearTimeout(citySearchTimer);
  citySearchTimer=setTimeout(()=>searchCities(citySearch.value),350);
}});
const csrfToken=document.querySelector('[name="csrf_token"]').value;
const deviceMessage=document.getElementById("device-message");
const connectionValue=document.getElementById("kindle-connection");
const brightnessValue=document.getElementById("kindle-brightness");
const autostartValue=document.getElementById("kindle-autostart");
const deviceLog=document.getElementById("device-log");
async function deviceApi(path,options={{}}){{
  const headers={{...(options.headers||{{}})}};
  if((options.method||"GET")!=="GET") headers["X-CSRF-Token"]=csrfToken;
  const response=await fetch(path,{{...options,headers}});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||"Device request failed");
  return data;
}}
async function loadDeviceState(){{
  try{{
    const [status,log]=await Promise.all([
      deviceApi("/api/device/status"),
      deviceApi("/api/device/log"),
    ]);
    connectionValue.textContent=status.connected?"Online":"Offline";
    brightnessValue.textContent=status.brightness;
    autostartValue.textContent=status.autostart;
    deviceLog.textContent=log.log||"No dashboard log yet.";
    const overviewKindleConn=document.getElementById("overview-kindle-connection");
    if(overviewKindleConn){{
      overviewKindleConn.textContent=status.connected?"Online":"Offline";
      overviewKindleConn.style.color=status.connected?"#2f855a":"#c53030";
    }}
  }}catch(error){{
    connectionValue.textContent="Offline";
    deviceMessage.textContent=error.message;
    const overviewKindleConn=document.getElementById("overview-kindle-connection");
    if(overviewKindleConn){{
      overviewKindleConn.textContent="Offline";
      overviewKindleConn.style.color="#c53030";
    }}
  }}
}}
async function runDeviceAction(button,path,body){{
  const original=button.textContent;
  button.disabled=true;
  deviceMessage.textContent=`Running ${{original}}…`;
  try{{
    const options={{method:"POST"}};
    if(body!==undefined){{
      options.headers={{"Content-Type":"application/json"}};
      options.body=JSON.stringify(body);
    }}
    const result=await deviceApi(path,options);
    deviceMessage.textContent=result.message||"Completed";
    if(result.brightness!==undefined){{
      brightnessValue.textContent=result.brightness;
      const persistentDisplay=document.getElementById("persistent-light-display");
      if(persistentDisplay) persistentDisplay.querySelector("strong").textContent=result.brightness;
    }}
    await loadDeviceState();
    return result;
  }}catch(error){{
    deviceMessage.textContent=error.message;
    throw error;
  }}finally{{
    button.disabled=false;
  }}
}}
document.querySelectorAll("[data-device-action]").forEach(button=>button.addEventListener("click",()=>{{
  runDeviceAction(button,`/api/device/${{button.dataset.deviceAction}}`).catch(()=>{{}});
}}));
document.querySelectorAll("[data-light]").forEach(button=>button.addEventListener("click",()=>{{
  runDeviceAction(button,"/api/device/light",{{level:Number(button.dataset.light)}}).catch(()=>{{}});
}}));
document.getElementById("push-kindle").addEventListener("click",async event=>{{
  try{{
    const result=await runDeviceAction(event.currentTarget,"/api/device/push");
    document.getElementById("last-push").textContent=result.message;
  }}catch(error){{}}
}});
document.getElementById("restart-kindle").addEventListener("click",event=>{{
  const confirmation=window.prompt("Type RESTART to reboot the Kindle.");
  if(confirmation!=="RESTART"){{deviceMessage.textContent="Restart cancelled";return;}}
  runDeviceAction(event.currentTarget,"/api/device/restart",{{confirm:confirmation}}).catch(()=>{{}});
}});
document.getElementById("restart-settings-server").addEventListener("click",async event=>{{
  const confirmed=window.confirm("Restarting the settings server will make this page unavailable for a few seconds. Continue?");
  if(!confirmed) return;
  const button=event.currentTarget;
  const maintenanceMessage=document.getElementById("maintenance-message");
  button.disabled=true;
  maintenanceMessage.textContent="Restarting settings server...";
  const started=Date.now();
  try{{
    await deviceApi("/api/maintenance/restart-settings",{{method:"POST"}});
  }}catch(error){{}}
  async function retrySettings(){{
    try{{
      const response=await fetch("/settings",{{cache:"no-store"}});
      if(response.ok){{
        const successMessage="Settings server restarted successfully.";
        window.location.href=`/settings?status=${{encodeURIComponent(successMessage)}}`;
        return;
      }}
    }}catch(error){{}}
    if(Date.now()-started>=20000){{
      maintenanceMessage.textContent="Server is still restarting. Please refresh manually or check SSH.";
      button.disabled=false;
      return;
    }}
    setTimeout(retrySettings,2000);
  }}
  setTimeout(retrySettings,5000);
}});
loadDeviceState();
</script>
</body>
</html>"""


def make_handler(config_path, regenerate, device, restart_settings, geocode):
    config_path = Path(config_path)
    csrf_token = secrets.token_urlsafe(32)
    update_lock = threading.Lock()

    class SettingsHandler(BaseHTTPRequestHandler):
        server_version = "KindleSettings"
        sys_version = ""

        def send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, payload):
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
            self.send_bytes(status, body, "application/json; charset=utf-8")

        def redirect(self, location):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def read_body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid content length") from exc
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            return self.rfile.read(length)

        def read_json(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("application/json required")
            value = json.loads(self.read_body().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def device_csrf_valid(self):
            supplied = self.headers.get("X-CSRF-Token", "")
            return hmac.compare_digest(supplied, csrf_token)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                self.send_bytes(200, b"OK\n", "text/plain; charset=utf-8")
                return
            if parsed.path == "/api/config":
                self.send_json(200, load_config(config_path))
                return
            if parsed.path == "/api/geocode":
                query = parse_qs(
                    parsed.query,
                    keep_blank_values=True,
                ).get("q", [""])[0].strip()
                if not query:
                    self.send_json(
                        400,
                        {"ok": False, "error": "city query is required"},
                    )
                    return
                if len(query) > 100:
                    self.send_json(
                        400,
                        {"ok": False, "error": "city query is too long"},
                    )
                    return
                try:
                    self.send_json(
                        200,
                        {"ok": True, "results": geocode(query)},
                    )
                except Exception:
                    self.send_json(
                        502,
                        {
                            "ok": False,
                            "error": "Location search is temporarily unavailable",
                        },
                    )
                return
            if parsed.path in (
                "/api/device/status",
                "/api/device/light",
                "/api/device/log",
            ):
                self.handle_device_get(parsed.path)
                return
            if parsed.path == "/settings":
                query = parse_qs(parsed.query)
                message = query.get("status", [""])[0]
                body = render_settings(
                    load_config(config_path),
                    csrf_token,
                    message,
                ).encode("utf-8")
                self.send_bytes(200, body, "text/html; charset=utf-8")
                return
            self.send_bytes(404, b"", "text/plain")

        def do_POST(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/api/config":
                self.handle_api_post()
                return
            if parsed.path == "/settings":
                self.handle_form_post()
                return
            if parsed.path.startswith("/api/maintenance/"):
                if parsed.path != "/api/maintenance/restart-settings":
                    self.send_bytes(404, b"", "text/plain")
                    return
                self.handle_maintenance_restart()
                return
            if parsed.path.startswith("/api/device/"):
                known_paths = {
                    "/api/device/start-dashboard",
                    "/api/device/home",
                    "/api/device/refresh",
                    "/api/device/autostart/enable",
                    "/api/device/autostart/disable",
                    "/api/device/light",
                    "/api/device/push",
                    "/api/device/restart",
                }
                if parsed.path not in known_paths:
                    self.send_bytes(404, b"", "text/plain")
                    return
                self.handle_device_post(parsed.path)
                return
            self.send_bytes(404, b"", "text/plain")

        def handle_maintenance_restart(self):
            if not self.device_csrf_valid():
                self.send_json(
                    403,
                    {"ok": False, "error": "invalid request token"},
                )
                return
            try:
                restart_settings()
                self.send_json(
                    202,
                    {
                        "ok": True,
                        "message": "Restarting settings server...",
                    },
                )
            except Exception:
                self.send_json(
                    500,
                    {"ok": False, "error": "Settings restart failed"},
                )

        def handle_device_get(self, path):
            try:
                if path == "/api/device/status":
                    payload = device.get_status()
                elif path == "/api/device/light":
                    payload = {
                        "connected": True,
                        "brightness": device.get_light(),
                    }
                else:
                    payload = {
                        "connected": True,
                        "log": device.get_log(),
                    }
                self.send_json(200, payload)
            except DeviceError:
                self.send_json(
                    503,
                    {"ok": False, "error": "Kindle is unavailable"},
                )
            except Exception:
                self.send_json(
                    500,
                    {"ok": False, "error": "Device status failed"},
                )

        def handle_device_post(self, path):
            if not self.device_csrf_valid():
                self.send_json(
                    403,
                    {"ok": False, "error": "invalid request token"},
                )
                return
            action_paths = {
                "/api/device/start-dashboard": "start",
                "/api/device/home": "home",
                "/api/device/refresh": "refresh",
                "/api/device/autostart/enable": "autostart_enable",
                "/api/device/autostart/disable": "autostart_disable",
            }
            try:
                if path in action_paths:
                    message = device.run_action(action_paths[path])
                    payload = {"ok": True, "message": message}
                elif path == "/api/device/push":
                    payload = {"ok": True, "message": device.push()}
                elif path == "/api/device/light":
                    candidate = self.read_json()
                    level = candidate.get("level")
                    if level is None or isinstance(level, bool) or not isinstance(level, int) or level not in (0, 1, 4, 8, 12, 18):
                        self.send_json(
                            400,
                            {"ok": False, "error": "invalid brightness level"},
                        )
                        return
                    try:
                        current_config = load_config(config_path)
                        current_config["kindle_frontlight"] = level
                        atomic_write_config(config_path, current_config)
                    except Exception as e:
                        print(f"Warning: Failed to save kindle_frontlight to config: {e}")
                    brightness = device.set_light(level)
                    payload = {
                        "ok": True,
                        "message": f"Brightness set to {brightness}",
                        "brightness": brightness,
                    }
                elif path == "/api/device/restart":
                    candidate = self.read_json()
                    payload = {
                        "ok": True,
                        "message": device.restart(candidate.get("confirm")),
                    }
                else:
                    self.send_bytes(404, b"", "text/plain")
                    return
                self.send_json(200, payload)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            except DeviceError:
                self.send_json(
                    503,
                    {"ok": False, "error": "Kindle command failed"},
                )
            except Exception:
                self.send_json(
                    500,
                    {"ok": False, "error": "Device action failed"},
                )

        def handle_api_post(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self.send_json(415, {"ok": False, "error": "application/json required"})
                return
            try:
                candidate = json.loads(self.read_body().decode("utf-8"))
                with update_lock:
                    saved = update_config(config_path, candidate, regenerate)
                self.send_json(200, {"ok": True, "config": saved})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            except Exception:
                self.send_json(500, {"ok": False, "error": "regeneration failed"})

        def handle_form_post(self):
            try:
                form = parse_qs(
                    self.read_body().decode("utf-8"),
                    keep_blank_values=True,
                )
                supplied_csrf = form.get("csrf_token", [""])[0]
                if not hmac.compare_digest(supplied_csrf, csrf_token):
                    raise ValueError("invalid form token")
                candidate = {
                    key: form.get(key, [""])[0]
                    for key in ("title", "location_label", "weather_query",
                                "timezone", "theme")
                }
                candidate.update({
                    "location": form.get(
                        "location",
                        [candidate["weather_query"]],
                    )[0],
                    "country": form.get("country", [""])[0],
                    "location_display": form.get(
                        "location_display",
                        [candidate["location_label"]],
                    )[0],
                })
                latitude = form.get("latitude", [""])[0].strip()
                longitude = form.get("longitude", [""])[0].strip()
                if latitude or longitude:
                    if not latitude or not longitude:
                        raise ValueError(
                            "latitude and longitude must be provided together"
                        )
                    candidate["latitude"] = float(latitude)
                    candidate["longitude"] = float(longitude)
                else:
                    candidate["latitude"] = None
                    candidate["longitude"] = None
                for key in ("show_weather", "show_forecast", "show_server",
                            "show_pihole", "show_tailscale"):
                    candidate[key] = key in form
                for key in ("prayer_method", "prayer_school", "prayer_high_latitude", "hijri_adjustment"):
                    if key in form:
                        try:
                            candidate[key] = int(form[key][0])
                        except Exception:
                            pass
                with update_lock:
                    update_config(config_path, candidate, regenerate)
                self.redirect("/settings?status=saved")
            except ValueError as exc:
                self.redirect(f"/settings?status={quote(str(exc))}")
            except Exception:
                self.redirect("/settings?status=regeneration%20failed")

        def do_OPTIONS(self):
            self.send_bytes(404, b"", "text/plain")

        def log_message(self, format_string, *args):
            return

    return SettingsHandler


def make_server(host=BIND_HOST, port=PORT, config_path=CONFIG_PATH,
                regenerate=regenerate_dashboard, device=None,
                restart_settings=schedule_settings_restart,
                geocode=geocode_locations):
    if device is None:
        device = KindleDevice()
    return ThreadingHTTPServer(
        (host, port),
        make_handler(
            config_path,
            regenerate,
            device,
            restart_settings,
            geocode,
        ),
    )


def main():
    server = make_server()
    print(f"Kindle dashboard settings listening on http://{BIND_HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

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
from weather_image import DEFAULT_CONFIG, load_config, validate_config


BIND_HOST = "0.0.0.0"
PORT = 8767
PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "dashboard_config.json"
RUN_DASHBOARD = PROJECT_DIR / "run_dashboard.sh"
MAX_REQUEST_BYTES = 16 * 1024

PRESETS = {
    "Nottingham": {
        "title": "NOTTINGHAM HOME",
        "location_label": "Nottingham, UK",
        "weather_query": "Nottingham",
        "timezone": "Europe/London",
    },
    "London": {
        "title": "LONDON DASHBOARD",
        "location_label": "London, UK",
        "weather_query": "London",
        "timezone": "Europe/London",
    },
    "Birmingham": {
        "title": "BIRMINGHAM DASHBOARD",
        "location_label": "Birmingham, UK",
        "weather_query": "Birmingham",
        "timezone": "Europe/London",
    },
    "Manchester": {
        "title": "MANCHESTER DASHBOARD",
        "location_label": "Manchester, UK",
        "weather_query": "Manchester",
        "timezone": "Europe/London",
    },
    "Istanbul": {
        "title": "ISTANBUL DASHBOARD",
        "location_label": "Istanbul, Türkiye",
        "weather_query": "Istanbul",
        "timezone": "Europe/Istanbul",
    },
    "Ankara": {
        "title": "ANKARA DASHBOARD",
        "location_label": "Ankara, Türkiye",
        "weather_query": "Ankara",
        "timezone": "Europe/Istanbul",
    },
}

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


def update_config(config_path, candidate, regenerate):
    config_path = Path(config_path)
    previous_exists = config_path.exists()
    previous_data = config_path.read_bytes() if previous_exists else None
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


def render_settings(config, csrf_token, status_message=""):
    escaped = {key: html.escape(str(value), quote=True)
               for key, value in config.items()}

    def checked(key):
        return " checked" if config[key] else ""

    preset_buttons = "".join(
        f'<button type="button" class="preset" data-preset="{html.escape(name)}">'
        f"{html.escape(name)}</button>"
        for name in PRESETS
    )
    cities = [
        {
            "city": city,
            "country": country,
            "location_label": label,
            "weather_query": city,
            "timezone": timezone,
            "title": title,
        }
        for city, country, label, timezone, title in CITY_DATA
    ]
    city_json = json.dumps(cities, ensure_ascii=False).replace("<", "\\u003c")
    city_options = "".join(
        f'<option value="{html.escape(city, quote=True)}"></option>'
        for city, _, _, _, _ in CITY_DATA
    )
    if config["timezone"] == "Europe/Istanbul":
        current_country = "Türkiye"
    elif config["timezone"] == "Europe/Amsterdam":
        current_country = "Netherlands"
    else:
        current_country = "United Kingdom"
    country_options = "".join(
        f'<option value="{html.escape(country, quote=True)}"'
        f'{" selected" if country == current_country else ""}>'
        f"{html.escape(country)}</option>"
        for country in ("United Kingdom", "Türkiye", "Netherlands", "Other")
    )
    timezone_options = "".join(
        f'<option value="{html.escape(timezone, quote=True)}"'
        f'{" selected" if timezone == config["timezone"] else ""}>'
        f"{html.escape(timezone)}</option>"
        for timezone in COMMON_TIMEZONES
    )
    if config["timezone"] not in COMMON_TIMEZONES:
        timezone_options += '<option value="custom" selected>Custom timezone…</option>'
    else:
        timezone_options += '<option value="custom">Custom timezone…</option>'
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
:root{{--bg:#f2f3f5;--card:#fff;--ink:#171717;--muted:#687078;--line:#dfe2e5;--accent:#111;--soft:#f6f7f8}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.shell{{max-width:1080px;margin:0 auto;padding:20px 14px 190px}}
.app-header{{padding:10px 4px 20px}}
.app-header h1{{font-size:1.65rem;line-height:1.15;margin:0 0 7px}}
.subtitle{{margin:0;color:var(--muted);font-size:.95rem}}
.grid{{display:grid;grid-template-columns:1fr;gap:16px}}
.card{{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:0 4px 18px rgba(20,25,30,.05);scroll-margin-top:16px}}
.card h2{{font-size:1.12rem;margin:0 0 4px}}
.section-note{{margin:0 0 16px!important;color:var(--muted);font-size:.9rem}}
.card p{{margin:8px 0}}
.field{{display:block;margin:14px 0}}
.field span{{display:block;margin-bottom:6px;font-weight:700}}
input[type=text],input[type=search],select{{width:100%;min-height:48px;padding:11px 12px;border:1px solid #aeb4ba;border-radius:12px;background:#fff;color:#111;font:inherit}}
input:focus,select:focus{{outline:3px solid rgba(17,17,17,.12);border-color:#333}}
.button-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
button{{min-height:48px;padding:10px 12px;border:1px solid #555;border-radius:12px;background:#fff;color:#111;font:700 .95rem system-ui;touch-action:manipulation}}
button:active:not(:disabled){{transform:translateY(1px)}}
button:disabled{{color:#777;background:#eee;border-color:#d2d2d2;cursor:not-allowed}}
.preset-grid{{display:flex;flex-wrap:wrap;gap:8px;margin:0 -2px 16px;padding:2px}}
.preset{{flex:0 0 auto;min-height:42px;padding:7px 14px;border-radius:999px}}
.location-grid{{display:grid;grid-template-columns:1fr;gap:0}}
.match{{margin:-4px 0 8px;padding:11px 12px;border-radius:12px;background:var(--soft);color:var(--muted);font-size:.9rem}}
.toggle-list{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}
.toggle{{display:flex;align-items:center;gap:9px;min-height:50px;margin:0;padding:9px 10px;border:1px solid var(--line);border-radius:12px;font-weight:650;cursor:pointer}}
.toggle input{{width:24px;height:24px;margin:0;accent-color:#111;flex:0 0 auto}}
.advanced{{margin-top:14px;border-top:1px solid var(--line);padding-top:14px}}
.advanced summary,.device-details summary{{min-height:44px;display:flex;align-items:center;font-weight:750;cursor:pointer}}
.future-box{{margin-top:16px;padding:14px;background:var(--soft);border-radius:14px}}
.future-box h3{{margin:0 0 4px;font-size:.95rem}}
.future-box p{{color:var(--muted);font-size:.86rem}}
.future-box input:disabled{{opacity:.65}}
.theme-list{{display:grid;gap:9px;margin-top:14px}}
.theme-choice{{display:flex;align-items:center;gap:12px;min-height:62px;padding:11px 12px;border:1px solid var(--line);border-radius:14px;cursor:pointer}}
.theme-choice:has(input:checked){{border:2px solid #111;padding:10px 11px;background:#fafafa}}
.theme-choice input{{width:22px;height:22px;accent-color:#111;flex:0 0 auto}}
.theme-choice span{{display:grid;gap:2px}}.theme-choice small{{color:var(--muted)}}
.theme-choice.disabled{{opacity:.55;cursor:not-allowed}}
.device-details summary h2{{margin:0}}.device-details[open] summary{{margin-bottom:14px}}
.coming{{color:var(--muted);font-size:.9rem}}
.device-state{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}}
.device-stat{{padding:10px 8px;background:var(--soft);border-radius:12px;text-align:center}}
.device-stat small{{display:block;color:var(--muted);font-size:.72rem}}.device-stat strong{{display:block;margin-top:3px;font-size:.9rem}}
.device-message{{min-height:44px;margin:12px 0!important;padding:11px 12px;border-radius:12px;background:var(--soft)}}
.light-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
.log-box{{max-height:240px;overflow:auto;margin:12px 0 0;padding:12px;border-radius:12px;background:#171717;color:#f2f2f2;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}
.status-list{{display:grid;gap:10px;margin:0}}
.status-row{{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #ecece8}}
.status-row:last-child{{border-bottom:0}}
.status-row dt{{color:var(--muted)}}.status-row dd{{margin:0;text-align:right;font-weight:700;overflow-wrap:anywhere}}
.message{{margin:0 0 16px;padding:12px 14px;background:#eaf6eb;border:1px solid #bedcc1;border-radius:12px}}
.action-bar{{position:fixed;z-index:20;left:0;right:0;bottom:65px;display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:9px;padding:10px max(12px,env(safe-area-inset-right)) 10px max(12px,env(safe-area-inset-left));background:rgba(255,255,255,.96);border-top:1px solid var(--line);box-shadow:0 -8px 24px rgba(20,25,30,.08);backdrop-filter:blur(12px)}}
.action-bar button{{margin:0;width:100%}}.action-bar button[type=submit]{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.bottom-nav{{position:fixed;z-index:10;left:0;right:0;bottom:0;display:grid;grid-template-columns:repeat(4,1fr);padding:8px max(8px,env(safe-area-inset-right)) calc(8px + env(safe-area-inset-bottom)) max(8px,env(safe-area-inset-left));background:rgba(255,255,255,.96);border-top:1px solid var(--line)}}
.bottom-nav a{{min-height:48px;display:flex;align-items:center;justify-content:center;color:#222;text-decoration:none;font-size:.82rem;font-weight:750;border-radius:10px}}
.bottom-nav a:active{{background:#eee}}
@media (min-width: 760px){{
  .shell{{padding:34px 24px 170px}}
  .grid{{grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}}
  .card.location{{grid-column:1/-1}}
  .location-grid{{grid-template-columns:1fr 1fr;column-gap:18px}}
  .button-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .action-bar{{left:50%;right:auto;bottom:86px;width:min(620px,calc(100% - 32px));transform:translateX(-50%);border:1px solid var(--line);border-radius:16px;padding:8px;box-shadow:0 8px 30px rgba(0,0,0,.13)}}
  .bottom-nav{{left:50%;right:auto;bottom:16px;width:min(560px,calc(100% - 32px));transform:translateX(-50%);border:1px solid var(--line);border-radius:16px;padding:6px;box-shadow:0 6px 24px rgba(0,0,0,.12)}}
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
<form method="post" action="/settings">
<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
<div class="grid">
<section class="card location" id="location">
<h2>Location</h2>
<p class="section-note">Search locally or use a quick preset. Custom cities are always allowed.</p>
<div class="preset-grid">{preset_buttons}</div>
<div class="location-grid">
<label class="field"><span>Country</span><select id="country">{country_options}</select></label>
<label class="field"><span>City search</span><input type="search" id="city-search" list="city-list" value="{escaped['weather_query']}" placeholder="Type any city" autocomplete="off"><datalist id="city-list">{city_options}</datalist></label>
</div>
<div class="match" id="city-match">Matched location: {escaped['location_label']} · {escaped['timezone']}</div>
<label class="field"><span>Dashboard title</span><input type="text" name="title" maxlength="28" value="{escaped['title']}" required></label>
<label class="field"><span>Timezone</span><select id="timezone-select">{timezone_options}</select></label>
<details class="advanced">
<summary>Advanced location fields</summary>
<label class="field"><span>Weather query</span><input type="text" name="weather_query" maxlength="100" value="{escaped['weather_query']}" required></label>
<label class="field"><span>Location label</span><input type="text" name="location_label" maxlength="80" value="{escaped['location_label']}" required></label>
<label class="field"><span>Custom timezone</span><input type="text" name="timezone" id="timezone-custom" maxlength="64" value="{escaped['timezone']}" required></label>
<div class="future-box">
<h3>Prayer location · future</h3>
<p>Prepared for Maarif Calendar. These fields are not stored yet.</p>
<label class="toggle"><input type="checkbox" id="same-prayer-location" checked disabled><span>Use weather location</span></label>
<label class="field"><span>Prayer location</span><input type="text" id="prayer-location" disabled value="{escaped['weather_query']}"></label>
<label class="field"><span>Prayer country</span><input type="text" id="prayer-country" disabled value="{html.escape(current_country, quote=True)}"></label>
</div>
</details>
</section>
<section class="card display" id="display">
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
<section class="card theme" id="theme">
<h2>Theme</h2>
<p class="section-note">Choose the dashboard’s visual focus.</p>
<div class="theme-list">{theme_cards}</div>
</section>
<section class="card device" id="device">
<details class="device-details" open>
<summary><h2>Device Controls</h2></summary>
<div class="device-state">
<div class="device-stat"><small>Connection</small><strong id="kindle-connection">Checking…</strong></div>
<div class="device-stat"><small>Brightness</small><strong id="kindle-brightness">—</strong></div>
<div class="device-stat"><small>Autostart</small><strong id="kindle-autostart">—</strong></div>
</div>
<p class="device-message" id="device-message" role="status">Ready</p>
<div class="button-grid">{device_buttons}</div>
<h3>Front light</h3>
<div class="light-grid">{light_buttons}</div>
<button type="button" id="restart-kindle">Restart Kindle</button>
<h3>Recent dashboard log</h3>
<pre class="log-box" id="device-log">Loading…</pre>
</details>
</section>
<section class="card status" id="status">
<h2>Status</h2>
<dl class="status-list">
<div class="status-row"><dt>Current title</dt><dd>{escaped['title']}</dd></div>
<div class="status-row"><dt>Weather query</dt><dd>{escaped['weather_query']}</dd></div>
<div class="status-row"><dt>Location label</dt><dd>{escaped['location_label']}</dd></div>
<div class="status-row"><dt>Timezone</dt><dd>{escaped['timezone']}</dd></div>
<div class="status-row"><dt>Selected theme</dt><dd>{escaped['theme']}</dd></div>
<div class="status-row"><dt>Last generation</dt><dd>{html.escape(status_message or 'No result in this session')}</dd></div>
<div class="status-row"><dt>Last push</dt><dd id="last-push">Not in this session</dd></div>
</dl>
</section>
</div>
<div class="action-bar">
<button type="submit">Save &amp; Regenerate</button>
<button type="button" id="push-kindle">Push to Kindle</button>
</div>
</form>
</main>
<nav class="bottom-nav" aria-label="Dashboard sections">
<a href="#location">Settings</a>
<a href="#theme">Theme</a>
<a href="#device">Device</a>
<a href="#status">Status</a>
</nav>
<script>
const cities={city_json};
const citySearch=document.getElementById("city-search");
const country=document.getElementById("country");
const timezoneSelect=document.getElementById("timezone-select");
const timezoneCustom=document.getElementById("timezone-custom");
const cityMatch=document.getElementById("city-match");
const advanced=document.querySelector("details.advanced");
const prayerLocation=document.getElementById("prayer-location");
const prayerCountry=document.getElementById("prayer-country");
const byName=name=>cities.find(city=>city.city.toLowerCase()===name.trim().toLowerCase());
function updateMatch(city){{
  cityMatch.textContent=`Matched location: ${{city.location_label}} · ${{city.timezone}}`;
}}
function applyCity(city){{
  citySearch.value=city.city;
  country.value=city.country;
  document.querySelector('[name="title"]').value=city.title;
  document.querySelector('[name="weather_query"]').value=city.weather_query;
  document.querySelector('[name="location_label"]').value=city.location_label;
  timezoneCustom.value=city.timezone;
  timezoneSelect.value=city.timezone;
  prayerLocation.value=city.weather_query;
  prayerCountry.value=city.country;
  updateMatch(city);
}}
function applyCustomCity(){{
  const value=citySearch.value.trim();
  if(!value) return;
  const match=byName(value);
  if(match){{applyCity(match);return;}}
  const suffix=country.value==="United Kingdom"?"UK":country.value;
  document.querySelector('[name="weather_query"]').value=value;
  document.querySelector('[name="location_label"]').value=`${{value}}, ${{suffix}}`;
  document.querySelector('[name="title"]').value=`${{value.toUpperCase()}} DASHBOARD`.slice(0,28);
  prayerLocation.value=value;
  prayerCountry.value=country.value;
  cityMatch.textContent=`Custom city: ${{value}} · ${{timezoneCustom.value}}`;
}}
document.querySelectorAll("[data-preset]").forEach(button=>button.addEventListener("click",()=>applyCity(byName(button.dataset.preset))));
citySearch.addEventListener("change",applyCustomCity);
timezoneSelect.addEventListener("change",()=>{{
  if(timezoneSelect.value==="custom"){{advanced.open=true;timezoneCustom.focus();return;}}
  timezoneCustom.value=timezoneSelect.value;
  cityMatch.textContent=`Location: ${{document.querySelector('[name="location_label"]').value}} · ${{timezoneCustom.value}}`;
}});
timezoneCustom.addEventListener("input",()=>{{
  const common=[...timezoneSelect.options].some(option=>option.value===timezoneCustom.value);
  timezoneSelect.value=common?timezoneCustom.value:"custom";
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
  }}catch(error){{
    connectionValue.textContent="Offline";
    deviceMessage.textContent=error.message;
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
    if(result.brightness!==undefined) brightnessValue.textContent=result.brightness;
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
loadDeviceState();
</script>
</body>
</html>"""


def make_handler(config_path, regenerate, device):
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
                    brightness = device.set_light(candidate.get("level"))
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
                for key in ("show_weather", "show_forecast", "show_server",
                            "show_pihole", "show_tailscale"):
                    candidate[key] = key in form
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
                regenerate=regenerate_dashboard, device=None):
    if device is None:
        device = KindleDevice()
    return ThreadingHTTPServer(
        (host, port),
        make_handler(config_path, regenerate, device),
    )


def main():
    server = make_server()
    print(f"Kindle dashboard settings listening on http://{BIND_HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

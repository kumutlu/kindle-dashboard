#!/usr/bin/env python3

import os, sys, re, html, subprocess
from datetime import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

LOCATION_QUERY = "Nottingham,UK"
LOCATION_LABEL = "Nottingham, UK"

KINDLE_W, KINDLE_H = 600, 800
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "kindle_weather.png")

FONT_BOLD = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
FONT_WEATHER = os.path.join(os.path.dirname(__file__), "weathericons.ttf")

WI = {
    "sun": "", "sun_cloud": "", "cloud": "", "fog": "",
    "rain_light": "", "rain": "", "rain_heavy": "",
    "snow": "", "sleet": "", "thunder": "",
}

WTTR_CODES = {
    113: ("Sunny", "sun"), 116: ("Partly Cloudy", "sun_cloud"),
    119: ("Cloudy", "cloud"), 122: ("Overcast", "cloud"),
    143: ("Mist", "fog"), 176: ("Patchy Rain", "rain_light"),
    263: ("Light Drizzle", "rain_light"), 266: ("Drizzle", "rain_light"),
    293: ("Light Rain", "rain_light"), 296: ("Light Rain", "rain_light"),
    299: ("Mod. Rain", "rain"), 302: ("Rain", "rain"),
    305: ("Heavy Rain", "rain_heavy"), 308: ("Very Hvy Rain", "rain_heavy"),
    353: ("Lt. Showers", "rain_light"), 356: ("Showers", "rain"),
    359: ("Hvy Showers", "rain_heavy"), 386: ("Thundery Rain", "thunder"),
}

FACTS = [
    "The first webcam was created at Cambridge University in 1991 to monitor a coffee pot.",
    "Honey never spoils; archaeologists found edible honey in ancient Egyptian tombs.",
    "Bananas are berries, but strawberries are not true botanical berries.",
    "Octopuses have three hearts and blue blood.",
    "The Eiffel Tower can grow taller in summer as metal expands in heat.",
    "The first computer mouse was made of wood.",
    "A day on Venus is longer than a year on Venus.",
]

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def sep(draw, y, margin=20, thickness=2):
    draw.line([(margin, y), (KINDLE_W-margin, y)], fill=0, width=thickness)

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        box = draw.textbbox((0,0), test, font=font)
        if box[2] - box[0] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def draw_icon(draw, icon_type, cx, cy, size=46):
    glyph = WI.get(icon_type)
    try:
        font = ImageFont.truetype(FONT_WEATHER, size)
        bbox = font.getbbox(glyph)
        gw, gh = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text((cx-gw//2-bbox[0], cy-gh//2-bbox[1]), glyph, fill=0, font=font)
    except Exception:
        r = max(7, size//4)
        draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], outline=0, width=2)

def draw_raindrop(draw, cx, cy, size=14):
    r = size // 2
    draw.ellipse([(cx-r, cy), (cx+r, cy+2*r)], fill=0)
    draw.polygon([(cx, cy-size), (cx-r, cy+r), (cx+r, cy+r)], fill=0)

def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

def fetch_weather():
    url = f"https://wttr.in/{LOCATION_QUERY}?format=j1"
    r = requests.get(url, timeout=20, headers={"User-Agent":"kindle-dashboard/1.0"})
    r.raise_for_status()
    return r.json()

def get_cpu_percent():
    out = run_cmd("top -bn1 | grep 'Cpu(s)'")
    nums = re.findall(r"(\d+\.\d+|\d+)", out)
    try:
        idle = float(nums[3])
        return int(round(100 - idle))
    except Exception:
        return None

def get_ram_percent():
    out = run_cmd("free -m | awk '/Mem:/ {print int($3/$2*100)}'")
    try:
        return int(out)
    except Exception:
        return None

def get_disk_percent():
    out = run_cmd("df -h / | awk 'NR==2 {print $5}' | tr -d '%'")
    try:
        return int(out)
    except Exception:
        return None

def get_pihole_stats():
    db = "/etc/pihole/pihole-FTL.db"

    q1 = f'''sudo sqlite3 {db} "SELECT COUNT(*) FROM queries WHERE timestamp >= strftime('%s','now','start of day');"'''
    q2 = f'''sudo sqlite3 {db} "SELECT COUNT(*) FROM queries WHERE timestamp >= strftime('%s','now','start of day') AND status IN (1,4,5,9,10,11);"'''
    q3 = f'''sudo sqlite3 {db} "SELECT COUNT(DISTINCT client) FROM queries WHERE timestamp >= strftime('%s','now','start of day');"'''

    queries = run_cmd(q1)
    blocked = run_cmd(q2)
    clients = run_cmd(q3)

    return {
        "queries": queries if queries else "-",
        "blocked": blocked if blocked else "-",
        "clients": clients if clients else "-",
    }
def get_tailscale_count():
    out = run_cmd(
        "tailscale status 2>/dev/null | grep -E 'active;|idle;' | wc -l"
    )
    try:
        return int(out)
    except Exception:
        return None
def fmt_num(v):
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)

def generate_image(data):
    img = Image.new("L", (KINDLE_W, KINDLE_H), color=255)
    draw = ImageDraw.Draw(img)

    fb_72 = load_font(FONT_BOLD, 72)
    fb_36 = load_font(FONT_BOLD, 36)
    fb_32 = load_font(FONT_BOLD, 32)
    fb_30 = load_font(FONT_BOLD, 30)
    fb_28 = load_font(FONT_BOLD, 28)
    fb_24 = load_font(FONT_BOLD, 24)
    fb_22 = load_font(FONT_BOLD, 22)
    fm_22 = load_font(FONT_MEDIUM, 22)
    fm_20 = load_font(FONT_MEDIUM, 20)
    fm_18 = load_font(FONT_MEDIUM, 18)

    now = datetime.now()
    current = data["current_condition"][0]
    weather = data["weather"]

    # HEADER - tarih var, saat yok
    draw.text((KINDLE_W//2, 24), LOCATION_LABEL, fill=0, font=fb_32, anchor="mm")
    draw.text((KINDLE_W//2, 50), now.strftime("%A, %d %B %Y"), fill=0, font=fm_20, anchor="mm")
    sep(draw, 62)

    # CURRENT WEATHER
    temp_c = int(current["temp_C"])
    feels_c = int(current["FeelsLikeC"])
    humidity = int(current["humidity"])
    wind_mph = int(current["windspeedMiles"])
    wind_dir = current["winddir16Point"]
    pressure = int(current["pressure"])
    wcode = int(current["weatherCode"])
    desc = current["weatherDesc"][0]["value"]
    t_max = weather[0]["maxtempC"]
    t_min = weather[0]["mintempC"]
    sunrise = weather[0]["astronomy"][0]["sunrise"]
    sunset = weather[0]["astronomy"][0]["sunset"]
    icon_t = WTTR_CODES.get(wcode, ("Weather", "sun_cloud"))[1]

    draw.text((150, 112), f"{temp_c}°C", fill=0, font=fb_72, anchor="mm")
    draw_icon(draw, icon_t, 72, 174, size=54)
    draw.text((168, 158), f"{t_max}°", fill=0, font=fb_36, anchor="mm")
    draw.text((168, 196), f"{t_min}°", fill=0, font=fb_36, anchor="mm")

    desc_font = fb_24 if len(desc) > 14 else fb_28
    draw.text((420, 77), desc, fill=0, font=desc_font, anchor="mm")
    draw.text((420, 104), f"Feels {feels_c}°C", fill=0, font=fb_24, anchor="mm")
    draw.text((420, 132), f"Humidity {humidity}%", fill=0, font=fb_24, anchor="mm")
    draw.text((420, 160), f"Wind {wind_mph} mph {wind_dir}", fill=0, font=fb_24, anchor="mm")
    draw.text((420, 188), f"Rise {sunrise}  Set {sunset}", fill=0, font=fm_20, anchor="mm")
    draw.text((420, 212), f"Pressure {pressure} hPa", fill=0, font=fm_20, anchor="mm")
    sep(draw, 228)

    # 3 DAY FORECAST
    FORE_TOP = 228
    col_w3 = KINDLE_W // 3
    try:
        d2 = datetime.strptime(weather[2]["date"], "%Y-%m-%d").strftime("%A")
    except Exception:
        d2 = "Day 3"

    dy_names = ["Today", "Tomorrow", d2]

    for i, day in enumerate(weather[:3]):
        cx = col_w3 * i + col_w3 // 2
        d_max = day["maxtempC"]
        d_min = day["mintempC"]
        d_rain = day["hourly"][4]["chanceofrain"]
        d_code = int(day["hourly"][4]["weatherCode"])
        d_desc = day["hourly"][4]["weatherDesc"][0]["value"]
        d_itype = WTTR_CODES.get(d_code, ("Weather","sun_cloud"))[1]

        draw.text((cx, FORE_TOP+22), dy_names[i], fill=0, font=fb_24, anchor="mm")
        draw_icon(draw, d_itype, cx, FORE_TOP+58, size=34)

        d_desc_font = fm_18 if len(d_desc) > 12 else fb_22
        draw.text((cx, FORE_TOP+92), d_desc, fill=0, font=d_desc_font, anchor="mm")
        draw.text((cx, FORE_TOP+122), f"{d_max}° / {d_min}°", fill=0, font=fb_28, anchor="mm")
        draw_raindrop(draw, cx-30, FORE_TOP+150, size=14)
        draw.text((cx+10, FORE_TOP+150), f"{d_rain}%", fill=0, font=fb_28, anchor="mm")

        if i < 2:
            draw.line([(col_w3*(i+1), FORE_TOP+10), (col_w3*(i+1), FORE_TOP+172)], fill=0, width=1)

    FORE_BOT = FORE_TOP + 180
    sep(draw, FORE_BOT)

    # FACT OF THE DAY
    FACT_TOP = FORE_BOT
    draw.text((KINDLE_W//2, FACT_TOP+22), "FACT OF THE DAY", fill=0, font=fb_28, anchor="mm")

    fact = FACTS[now.timetuple().tm_yday % len(FACTS)]
    lines = wrap_text(draw, fact, fb_24, KINDLE_W - 60)

    y = FACT_TOP + 58
    for line in lines[:3]:
        draw.text((KINDLE_W//2, y), line, fill=0, font=fb_24, anchor="mm")
        y += 32

    FACT_BOT = FACT_TOP + 145
    sep(draw, FACT_BOT)

    # HOME SERVER + PI-HOLE
    SERVER_TOP = FACT_BOT

    cpu = get_cpu_percent()
    ram = get_ram_percent()
    disk = get_disk_percent()
    ph = get_pihole_stats()

    draw.text((KINDLE_W//2, SERVER_TOP+22), "HOME SERVER", fill=0, font=fb_28, anchor="mm")

    draw.text((95, SERVER_TOP+65), "CPU", fill=0, font=fb_24, anchor="mm")
    draw.text((95, SERVER_TOP+98), f"{cpu if cpu is not None else '-'}%", fill=0, font=fb_30, anchor="mm")

    draw.text((215, SERVER_TOP+65), "RAM", fill=0, font=fb_24, anchor="mm")
    draw.text((215, SERVER_TOP+98), f"{ram if ram is not None else '-'}%", fill=0, font=fb_30, anchor="mm")

    draw.text((335, SERVER_TOP+65), "DISK", fill=0, font=fb_24, anchor="mm")
    draw.text((335, SERVER_TOP+98), f"{disk if disk is not None else '-'}%", fill=0, font=fb_30, anchor="mm")

    draw.line([(410, SERVER_TOP+48), (410, SERVER_TOP+118)], fill=0, width=2)

    draw.text((505, SERVER_TOP+52), "PI-HOLE", fill=0, font=fb_22, anchor="mm")
    draw.text((505, SERVER_TOP+80), "Blocked", fill=0, font=fm_18, anchor="mm")
    draw.text((505, SERVER_TOP+105), fmt_num(ph["blocked"]), fill=0, font=fb_24, anchor="mm")

    draw.text((110, SERVER_TOP+140), "Queries Today", fill=0, font=fm_20, anchor="mm")
    draw.text((110, SERVER_TOP+168), fmt_num(ph["queries"]), fill=0, font=fb_28, anchor="mm")

    draw.text((300, SERVER_TOP+140), "Clients", fill=0, font=fm_20, anchor="mm")
    draw.text((300, SERVER_TOP+168), fmt_num(ph["clients"]), fill=0, font=fb_28, anchor="mm")

    draw.text((480, SERVER_TOP+140), "Tailscale", fill=0, font=fm_20, anchor="mm")
    draw.text((480, SERVER_TOP+168), "5", fill=0, font=fb_28, anchor="mm")

    img_bw = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    img_bw.save(OUTPUT_PATH)
    print(f"Saved: {OUTPUT_PATH}")

def main():
    print(f"Fetching weather for {LOCATION_LABEL}...")
    try:
        data = fetch_weather()
    except Exception as e:
        print(f"Weather failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Generating home dashboard...")
    generate_image(data)

if __name__ == "__main__":
    main()

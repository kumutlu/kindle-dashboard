#!/usr/bin/env python3
from pathlib import Path

path = Path("settings_server.py")
text = path.read_text(encoding="utf-8")

old = '''    # Preserve custom Maarif and Display fields from existing config if not in candidate\n    for field in ("kindle_frontlight", "prayer_method", "prayer_school", "prayer_high_latitude", "hijri_adjustment", "refresh_interval_minutes", "wifi_power_save", "update_only_if_changed"):\n'''
new = '''    # Preserve custom Maarif, Display, and scheduler fields when omitted.\n    for field in ("kindle_frontlight", "prayer_method", "prayer_school", "prayer_high_latitude", "hijri_adjustment", "refresh_interval_minutes", "wifi_power_save", "update_only_if_changed", "scheduler"):\n'''
if old not in text:
    raise SystemExit("update_config preservation anchor not found")
text = text.replace(old, new, 1)

old = '''        "update_only_if_changed",\n    ):\n        if field not in candidate and field in current:\n'''
new = '''        "update_only_if_changed",\n        "scheduler",\n    ):\n        if field not in candidate and field in current:\n'''
if old not in text:
    raise SystemExit("update_device_config preservation anchor not found")
text = text.replace(old, new, 1)

old = '''        "update_only_if_changed",\n        "prayer_method",\n'''
new = '''        "update_only_if_changed",\n        "scheduler",\n        "prayer_method",\n'''
if old not in text:
    raise SystemExit("public_device_config anchor not found")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")

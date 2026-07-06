#!/usr/bin/env python3
"""Persistent, non-secret health status for dashboard devices."""

from __future__ import annotations

import hmac
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


STATUS_FIELDS = {
    "battery_percent",
    "charging",
    "battery_voltage",
    "last_seen",
    "wifi_rssi",
    "ip_address",
    "firmware_version",
    "last_refresh_at",
    "last_error",
    "loop_status",
}
TEXT_LIMITS = {
    "ip_address": 80,
    "firmware_version": 80,
    "last_error": 500,
    "loop_status": 40,
}


def utc_now():
    return datetime.now(timezone.utc)


def status_path(device):
    return Path(device.config_path).with_name("status.json")


def _parse_datetime(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("timestamp must be ISO-8601 text")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 text") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def validate_status_update(value, now=None):
    if not isinstance(value, dict):
        raise ValueError("status update must be a JSON object")
    unknown = set(value) - STATUS_FIELDS
    if unknown:
        raise ValueError("status contains unsupported fields")
    now = now or utc_now()

    status = {
        "battery_percent": None,
        "charging": None,
        "battery_voltage": None,
        "last_seen": _iso(now),
        "wifi_rssi": None,
        "ip_address": None,
        "firmware_version": None,
        "last_refresh_at": None,
        "last_error": None,
        "loop_status": None,
    }

    if "battery_percent" in value and value["battery_percent"] is not None:
        percent = value["battery_percent"]
        if isinstance(percent, bool) or not isinstance(percent, int):
            raise ValueError("battery_percent must be an integer")
        if not 0 <= percent <= 100:
            raise ValueError("battery_percent must be between 0 and 100")
        status["battery_percent"] = percent

    if "charging" in value and value["charging"] is not None:
        if not isinstance(value["charging"], bool):
            raise ValueError("charging must be true or false")
        status["charging"] = value["charging"]

    if "battery_voltage" in value and value["battery_voltage"] is not None:
        voltage = value["battery_voltage"]
        if (
            isinstance(voltage, bool)
            or not isinstance(voltage, (int, float))
            or not 0 <= float(voltage) <= 20
        ):
            raise ValueError("battery_voltage must be a number")
        status["battery_voltage"] = float(voltage)

    if "wifi_rssi" in value and value["wifi_rssi"] is not None:
        rssi = value["wifi_rssi"]
        if isinstance(rssi, bool) or not isinstance(rssi, int):
            raise ValueError("wifi_rssi must be an integer")
        if not -150 <= rssi <= 0:
            raise ValueError("wifi_rssi must be between -150 and 0")
        status["wifi_rssi"] = rssi

    for field, limit in TEXT_LIMITS.items():
        if field in value and value[field] is not None:
            item = value[field]
            if not isinstance(item, str):
                raise ValueError(f"{field} must be text")
            item = item.strip()
            if len(item) > limit:
                raise ValueError(f"{field} is too long")
            status[field] = item or None

    if "last_seen" in value and value["last_seen"]:
        status["last_seen"] = _iso(_parse_datetime(value["last_seen"]))
    if "last_refresh_at" in value and value["last_refresh_at"]:
        status["last_refresh_at"] = _iso(
            _parse_datetime(value["last_refresh_at"])
        )

    return status


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_status(device, value, now=None):
    status = validate_status_update(value, now=now)
    atomic_write_json(status_path(device), status)
    return status


def load_status(device):
    try:
        raw = json.loads(status_path(device).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    try:
        return validate_status_update(raw)
    except ValueError:
        return {}


def offline_threshold_minutes(device):
    if device.type == "esp32_epaper":
        return 30
    if device.type == "kindle_pw1":
        return 90
    return 90


def status_summary(device, status=None, now=None):
    now = now or utc_now()
    status = dict(status if status is not None else load_status(device))
    last_seen = status.get("last_seen")
    online = False
    if last_seen:
        try:
            seen_at = _parse_datetime(last_seen)
            online = now - seen_at <= timedelta(
                minutes=offline_threshold_minutes(device)
            )
        except ValueError:
            online = False
    status["online"] = online
    status["offline_threshold_minutes"] = offline_threshold_minutes(device)
    return status


def read_status_token(device):
    try:
        raw = json.loads(Path(device.config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("status_token", "device_token"):
        token = raw.get(key)
        if isinstance(token, str) and token:
            return token
    return None


def token_is_valid(device, supplied):
    expected = read_status_token(device)
    if not expected:
        return True
    return hmac.compare_digest(supplied or "", expected)

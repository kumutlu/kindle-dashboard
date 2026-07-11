#!/usr/bin/env python3
"""Persistence and rendering helpers for scheduled special-event images."""

from __future__ import annotations

import base64
import imghdr
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps


EVENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg);base64,(.+)$", re.I | re.S)


@dataclass(frozen=True)
class SpecialEvent:
    id: str
    title: str
    start_date: str
    end_date: str
    image_path: str
    enabled: bool
    devices: tuple[str, ...]
    created_at: str


def data_dir(project_root):
    return Path(project_root) / "data"


def events_path(project_root):
    return data_dir(project_root) / "special_events.json"


def images_dir(project_root):
    return data_dir(project_root) / "special_events"


def _atomic_write_bytes(path, payload):
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_json(path, payload):
    _atomic_write_bytes(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _parse_date(value, field_name):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc


def _normalize_devices(devices, valid_device_ids):
    if devices is None:
        return tuple(valid_device_ids)
    if not isinstance(devices, list) or not devices:
        raise ValueError("devices must be a non-empty list")
    normalized = []
    for item in devices:
        if not isinstance(item, str) or item not in valid_device_ids:
            raise ValueError("devices contains an unknown device")
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _validate_event_record(raw, valid_device_ids):
    if not isinstance(raw, dict):
        raise ValueError("event must be an object")
    required = {
        "id",
        "title",
        "start_date",
        "end_date",
        "image_path",
        "enabled",
        "devices",
        "created_at",
    }
    if set(raw) != required:
        raise ValueError("event schema is invalid")
    event_id = raw["id"]
    if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
        raise ValueError("event id is invalid")
    title = str(raw["title"]).strip()
    if not title or len(title) > 120:
        raise ValueError("title must contain 1-120 characters")
    start_date = _parse_date(raw["start_date"], "start_date")
    end_date = _parse_date(raw["end_date"], "end_date")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    image_path = str(raw["image_path"]).strip()
    if not image_path or Path(image_path).is_absolute() or ".." in Path(image_path).parts:
        raise ValueError("image_path is invalid")
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    devices = _normalize_devices(raw["devices"], valid_device_ids)
    created_at = raw["created_at"]
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError("created_at is invalid")
    return SpecialEvent(
        id=event_id,
        title=title,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        image_path=image_path,
        enabled=enabled,
        devices=devices,
        created_at=created_at.strip(),
    )


def load_events(project_root, valid_device_ids):
    path = events_path(project_root)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("special events store is invalid")
    return [
        _validate_event_record(item, valid_device_ids)
        for item in raw["items"]
    ]


def save_events(project_root, events):
    serializable = {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "image_path": item.image_path,
                "enabled": item.enabled,
                "devices": list(item.devices),
                "created_at": item.created_at,
            }
            for item in events
        ]
    }
    _atomic_write_json(events_path(project_root), serializable)


def _decode_image_data(image_data):
    if not isinstance(image_data, str):
        raise ValueError("image_data is required")
    match = DATA_URL_RE.fullmatch(image_data.strip())
    if match is None:
        raise ValueError("image_data must be a PNG or JPEG data URL")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ValueError("image_data is invalid") from exc
    kind = imghdr.what(None, payload)
    if kind not in {"png", "jpeg"}:
        raise ValueError("image_data must be a PNG or JPEG")
    return payload, (".png" if kind == "png" else ".jpg")


def store_uploaded_image(project_root, image_data, event_id):
    payload, suffix = _decode_image_data(image_data)
    relative = f"data/special_events/{event_id}{suffix}"
    destination = Path(project_root) / relative
    _atomic_write_bytes(destination, payload)
    return relative


def delete_event_image(project_root, relative_path):
    path = Path(project_root) / relative_path
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def create_event(project_root, payload, valid_device_ids, now=None):
    now = now or datetime.now()
    title = str(payload.get("title", "")).strip()
    if not title or len(title) > 120:
        raise ValueError("title must contain 1-120 characters")
    start_date = _parse_date(payload.get("start_date"), "start_date")
    end_raw = payload.get("end_date") or payload.get("start_date")
    end_date = _parse_date(end_raw, "end_date")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    devices = _normalize_devices(payload.get("devices"), valid_device_ids)
    image_path = store_uploaded_image(project_root, payload.get("image_data"), event_id)
    return SpecialEvent(
        id=event_id,
        title=title,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        image_path=image_path,
        enabled=enabled,
        devices=devices,
        created_at=now.isoformat(),
    )


def update_event(existing, payload, project_root, valid_device_ids):
    title = str(payload.get("title", existing.title)).strip()
    if not title or len(title) > 120:
        raise ValueError("title must contain 1-120 characters")
    start_date = _parse_date(payload.get("start_date", existing.start_date), "start_date")
    end_date = _parse_date(payload.get("end_date", existing.end_date), "end_date")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    enabled = payload.get("enabled", existing.enabled)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    devices = _normalize_devices(payload.get("devices", list(existing.devices)), valid_device_ids)
    image_path = existing.image_path
    if payload.get("image_data"):
        delete_event_image(project_root, existing.image_path)
        image_path = store_uploaded_image(project_root, payload["image_data"], existing.id)
    return SpecialEvent(
        id=existing.id,
        title=title,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        image_path=image_path,
        enabled=enabled,
        devices=devices,
        created_at=existing.created_at,
    )


def find_event(events, event_id):
    for item in events:
        if item.id == event_id:
            return item
    raise KeyError(event_id)


def event_to_public_dict(event):
    return {
        "id": event.id,
        "title": event.title,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "image_path": event.image_path,
        "enabled": event.enabled,
        "devices": list(event.devices),
        "created_at": event.created_at,
    }


def event_image_absolute(project_root, event):
    return Path(project_root) / event.image_path


def active_event_for_device(project_root, device, timezone_name, valid_device_ids, now=None):
    now = now or datetime.now(ZoneInfo(timezone_name))
    today = now.date()
    active = []
    for event in load_events(project_root, valid_device_ids):
        if not event.enabled or device.id not in event.devices:
            continue
        if event.start_date <= today.isoformat() <= event.end_date:
            active.append(event)
    if not active:
        return None
    active.sort(key=lambda item: (item.start_date, item.created_at, item.id))
    return active[0]


def render_event_image(source_path, output_path, resolution, kt4_safe=False):
    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as opened:
        fitted = ImageOps.fit(
            opened.convert("L"),
            tuple(resolution),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            save_kwargs = {"format": "PNG", "optimize": False}
            if kt4_safe:
                save_kwargs["compress_level"] = 0
            fitted.save(temporary_path, **save_kwargs)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)

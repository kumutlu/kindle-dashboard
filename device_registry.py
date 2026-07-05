#!/usr/bin/env python3
"""Validated registry and migration helpers for dashboard display devices."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DEVICE_TYPES = {"kindle_pw1", "esp32_epaper", "generic_png"}
RECORD_FIELDS = {
    "id",
    "name",
    "type",
    "resolution",
    "enabled",
    "config_path",
    "image_path",
    "connection",
}
REQUIRED_RECORD_FIELDS = RECORD_FIELDS - {"connection"}
KINDLE_CONNECTION_FIELDS = {"host", "user", "ssh_profile", "port"}
ESP32_CONNECTION_FIELDS = {"method", "host", "port"}


class RegistryValidationError(ValueError):
    """The registry contains unsupported or unsafe data."""


class DeviceNotFoundError(KeyError):
    """A requested device does not exist or is disabled."""


@dataclass(frozen=True)
class DeviceRecord:
    id: str
    name: str
    type: str
    resolution: tuple[int, int]
    enabled: bool
    config_path: Path
    image_path: Path
    connection: dict | None


def default_device_record() -> dict:
    return {
        "id": "default-kindle",
        "name": "Default Kindle",
        "type": "kindle_pw1",
        "resolution": [758, 1024],
        "enabled": True,
        "config_path": "devices/default-kindle/config.json",
        "image_path": "devices/default-kindle/image.png",
        "connection": {
            "host": "192.168.68.119",
            "user": "root",
            "ssh_profile": "kindle_dashboard",
            "port": 22,
        },
    }


class DeviceRegistry:
    def __init__(
        self,
        project_root,
        registry_path=None,
        config_validator: Callable[[dict], dict] | None = None,
        default_config: dict | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.registry_path = (
            Path(registry_path).resolve()
            if registry_path is not None
            else self.project_root / "devices.json"
        )
        self.legacy_config_path = self.project_root / "dashboard_config.json"
        self.legacy_image_path = self.project_root / "kindle_weather.png"
        self._config_validator = config_validator
        self._default_config = default_config
        self._require_under_root(self.registry_path)

    def _validator(self):
        if self._config_validator is not None:
            return self._config_validator
        from weather_image import validate_config

        return validate_config

    def _defaults(self):
        if self._default_config is not None:
            return dict(self._default_config)
        from weather_image import DEFAULT_CONFIG

        return dict(DEFAULT_CONFIG)

    def _require_under_root(self, path):
        path = Path(path).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise RegistryValidationError(
                "device path must remain inside the project directory"
            ) from exc
        return path

    def _resolve_device_path(self, value, expected):
        if not isinstance(value, str) or value != expected:
            raise RegistryValidationError(
                f"device path must be {expected}"
            )
        path = self._require_under_root(self.project_root / value)
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise RegistryValidationError("device path is unsafe")
        return path

    def _validate_connection(self, device_type, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RegistryValidationError(
                "connection must be an object"
            )
        if device_type == "kindle_pw1":
            allowed = KINDLE_CONNECTION_FIELDS
            required = {"host", "user", "ssh_profile"}
        elif device_type == "esp32_epaper":
            allowed = ESP32_CONNECTION_FIELDS
            required = {"method", "host"}
        else:
            raise RegistryValidationError(
                "connection is not supported for this device type"
            )
        unknown = set(value) - allowed
        missing = required - set(value)
        if unknown or missing:
            raise RegistryValidationError(
                "connection fields do not match the supported schema"
            )

        connection = dict(value)
        host = connection.get("host")
        if not isinstance(host, str) or not HOST_RE.fullmatch(host):
            raise RegistryValidationError("connection host is invalid")
        if "port" in connection:
            port = connection["port"]
            if (
                isinstance(port, bool)
                or not isinstance(port, int)
                or not 1 <= port <= 65535
            ):
                raise RegistryValidationError("connection port is invalid")

        if device_type == "kindle_pw1":
            user = connection.get("user")
            profile = connection.get("ssh_profile")
            if not isinstance(user, str) or not USER_RE.fullmatch(user):
                raise RegistryValidationError(
                    "connection user is invalid"
                )
            if (
                not isinstance(profile, str)
                or not PROFILE_RE.fullmatch(profile)
            ):
                raise RegistryValidationError(
                    "connection ssh_profile is invalid"
                )
        elif connection.get("method") != "http":
            raise RegistryValidationError(
                "ESP32 connection method must be http"
            )
        return connection

    def _validate_record(self, value):
        if not isinstance(value, dict):
            raise RegistryValidationError(
                "device record must be an object"
            )
        unknown = set(value) - RECORD_FIELDS
        missing = REQUIRED_RECORD_FIELDS - set(value)
        if unknown or missing:
            raise RegistryValidationError(
                "device fields do not match the supported schema"
            )

        device_id = value.get("id")
        if (
            not isinstance(device_id, str)
            or not DEVICE_ID_RE.fullmatch(device_id)
        ):
            raise RegistryValidationError("device id is invalid")
        name = value.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 100
        ):
            raise RegistryValidationError("device name is invalid")
        device_type = value.get("type")
        if device_type not in DEVICE_TYPES:
            raise RegistryValidationError("device type is invalid")
        resolution = value.get("resolution")
        if (
            not isinstance(resolution, list)
            or len(resolution) != 2
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 64 <= item <= 4096
                for item in resolution
            )
        ):
            raise RegistryValidationError(
                "device resolution is invalid"
            )
        enabled = value.get("enabled")
        if not isinstance(enabled, bool):
            raise RegistryValidationError(
                "device enabled must be true or false"
            )

        config_relative = f"devices/{device_id}/config.json"
        image_relative = f"devices/{device_id}/image.png"
        config_path = self._resolve_device_path(
            value.get("config_path"),
            config_relative,
        )
        image_path = self._resolve_device_path(
            value.get("image_path"),
            image_relative,
        )
        connection = self._validate_connection(
            device_type,
            value.get("connection"),
        )
        return DeviceRecord(
            id=device_id,
            name=name.strip(),
            type=device_type,
            resolution=(resolution[0], resolution[1]),
            enabled=enabled,
            config_path=config_path,
            image_path=image_path,
            connection=connection,
        )

    def validate_registry(self, value):
        if not isinstance(value, dict) or set(value) != {"devices"}:
            raise RegistryValidationError(
                "registry must contain only a devices list"
            )
        devices = value["devices"]
        if not isinstance(devices, list) or not devices:
            raise RegistryValidationError(
                "registry devices must be a non-empty list"
            )
        records = [self._validate_record(item) for item in devices]
        ids = [record.id for record in records]
        if len(ids) != len(set(ids)):
            raise RegistryValidationError("duplicate device id")
        return records

    def _storage_record(self, record):
        value = {
            "id": record.id,
            "name": record.name,
            "type": record.type,
            "resolution": list(record.resolution),
            "enabled": record.enabled,
            "config_path": record.config_path.relative_to(
                self.project_root
            ).as_posix(),
            "image_path": record.image_path.relative_to(
                self.project_root
            ).as_posix(),
        }
        if record.connection is not None:
            value["connection"] = dict(record.connection)
        return value

    def _public_record(self, record):
        value = {
            "id": record.id,
            "name": record.name,
            "type": record.type,
            "resolution": list(record.resolution),
            "enabled": record.enabled,
        }
        if record.connection is not None:
            value["connection"] = dict(record.connection)
        return value

    def _atomic_write_json(self, path, value):
        path = self._require_under_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _atomic_copy(self, source, destination):
        destination = self._require_under_root(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    def write_registry(self, value):
        records = self.validate_registry(value)
        canonical = {
            "devices": [
                self._storage_record(record) for record in records
            ]
        }
        self._atomic_write_json(self.registry_path, canonical)
        return records

    def _read_existing(self):
        try:
            value = json.loads(
                self.registry_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryValidationError(
                "device registry is missing or invalid"
            ) from exc
        return self.validate_registry(value)

    def _migrate_default_files(self, record):
        if not record.config_path.exists():
            config = self._defaults()
            if self.legacy_config_path.exists():
                try:
                    legacy = json.loads(
                        self.legacy_config_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    config = self._validator()(legacy)
                except (OSError, json.JSONDecodeError, ValueError):
                    config = self._validator()(config)
            else:
                config = self._validator()(config)
            self._atomic_write_json(record.config_path, config)
        if (
            not record.image_path.exists()
            and self.legacy_image_path.exists()
        ):
            self._atomic_copy(
                self.legacy_image_path,
                record.image_path,
            )

    def ensure_default_migration(self):
        if not self.registry_path.exists():
            self.write_registry({
                "devices": [default_device_record()],
            })
        records = self._read_existing()
        default = next(
            (record for record in records if record.id == "default-kindle"),
            None,
        )
        if default is None:
            raise RegistryValidationError(
                "default-kindle is required for backward compatibility"
            )
        self._migrate_default_files(default)
        return default

    def load(self):
        self.ensure_default_migration()
        return self._read_existing()

    def get(self, device_id, require_enabled=False):
        if (
            not isinstance(device_id, str)
            or not DEVICE_ID_RE.fullmatch(device_id)
        ):
            raise DeviceNotFoundError(device_id)
        for record in self.load():
            if record.id == device_id:
                if require_enabled and not record.enabled:
                    break
                return record
        raise DeviceNotFoundError(device_id)

    def public_records(self):
        return [self._public_record(record) for record in self.load()]

    def add(self, candidate):
        records = self.load()
        if any(
            record.id == candidate.get("id")
            for record in records
            if isinstance(candidate, dict)
        ):
            raise RegistryValidationError("duplicate device id")
        raw = {
            "devices": [
                self._storage_record(record) for record in records
            ] + [candidate]
        }
        records = self.write_registry(raw)
        added = records[-1]
        if not added.config_path.exists():
            self._atomic_write_json(
                added.config_path,
                self._validator()(self._defaults()),
            )
        return added

    def update(self, device_id, candidate):
        if (
            not isinstance(candidate, dict)
            or candidate.get("id") != device_id
        ):
            raise RegistryValidationError("device id cannot be changed")
        records = self.load()
        found = False
        values = []
        for record in records:
            if record.id == device_id:
                values.append(candidate)
                found = True
            else:
                values.append(self._storage_record(record))
        if not found:
            raise DeviceNotFoundError(device_id)
        records = self.write_registry({"devices": values})
        return next(record for record in records if record.id == device_id)

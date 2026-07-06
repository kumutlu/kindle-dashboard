#!/usr/bin/env python3
"""Fixed, non-interactive command runner for the Kindle dashboard."""

import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
RUN_DASHBOARD = PROJECT_DIR / "run_dashboard.sh"
SSH_KEY = Path("/home/user/.ssh/kindle_dashboard_ed25519")
KNOWN_HOSTS = Path("/home/user/.ssh/kindle_dashboard_known_hosts")
KINDLE_HOST = "root@192.168.68.119"

SSH_BASE = [
    "/usr/bin/ssh",
    "-i", str(SSH_KEY),
    "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=5",
    "-o", "ConnectionAttempts=1",
    "-o", "LogLevel=ERROR",
    KINDLE_HOST,
]

SSH_PROFILES = {
    "kindle_dashboard": {
        "key_path": Path("/home/user/.ssh/kindle_dashboard_ed25519"),
        "known_hosts": Path(
            "/home/user/.ssh/kindle_dashboard_known_hosts"
        ),
        "options": ("-o", "StrictHostKeyChecking=yes"),
    },
}

def require_script_command(path):
    return (
        f'if [ ! -x {path} ]; then '
        f'echo "missing script: {path}" >&2; exit 127; '
        f"fi; exec {path}"
    )


REFRESH_COMMAND = (
    "if [ -f /mnt/us/dashboard/device.env ] && "
    "[ -x /mnt/us/dashboard/refresh.sh ]; then "
    "exec /mnt/us/dashboard/refresh.sh; "
    "fi; "
    "if [ -x /mnt/us/dashboard/refresh-once.sh ]; then "
    "exec /mnt/us/dashboard/refresh-once.sh; "
    "fi; "
    "if [ -x /mnt/us/dashboard/refresh.sh ]; then "
    "echo \"legacy refresh.sh exists but no device.env; "
    "refusing to run possible loop\" >&2; exit 124; "
    "fi; "
    "echo \"missing script: /mnt/us/dashboard/refresh.sh "
    "or /mnt/us/dashboard/refresh-once.sh\" >&2; exit 127"
)


START_COMMAND = (
    "if [ -x /mnt/us/dashboard/start.sh ]; then "
    "exec /mnt/us/dashboard/start.sh; "
    "fi; "
    "if [ -x /mnt/us/dashboard/start-dashboard.sh ]; then "
    "exec /mnt/us/dashboard/start-dashboard.sh --manual; "
    "fi; "
    "echo \"missing script: /mnt/us/dashboard/start.sh "
    "or /mnt/us/dashboard/start-dashboard.sh\" >&2; exit 127"
)


STOP_COMMAND = (
    "if [ -x /mnt/us/dashboard/stop.sh ]; then "
    "exec /mnt/us/dashboard/stop.sh; "
    "fi; "
    "pkill -f /mnt/us/dashboard/dashboard_loop.sh 2>/dev/null || true; "
    "pkill -f /mnt/us/dashboard/watchdog.sh 2>/dev/null || true; "
    "pkill -f /mnt/us/dashboard/refresh.sh 2>/dev/null || true; "
    "rm -f /mnt/us/dashboard/dashboard_loop.pid "
    "/mnt/us/dashboard/watchdog.pid 2>/dev/null || true; "
    "echo \"Dashboard stopped\""
)


ACTION_COMMANDS = {
    "start": (
        START_COMMAND,
        "Dashboard started",
        20,
    ),
    "stop": (
        STOP_COMMAND,
        "Dashboard stopped",
        20,
    ),
    "home": (
        (
            "if [ -x /mnt/us/dashboard/home.sh ]; then "
            "exec /mnt/us/dashboard/home.sh; "
            "fi; "
            "lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home"
        ),
        "Kindle Home opened",
        20,
    ),
    "refresh": (
        REFRESH_COMMAND,
        "Dashboard refreshed",
        60,
    ),
    "autostart_enable": (
        "rm -f /mnt/us/dashboard/NOAUTOSTART",
        "Autostart enabled",
        10,
    ),
    "autostart_disable": (
        "touch /mnt/us/dashboard/NOAUTOSTART",
        "Autostart disabled",
        10,
    ),
}

LIGHT_GET = "lipc-get-prop com.lab126.powerd flIntensity"
LOG_GET = "tail -n 80 /mnt/us/dashboard/dashboard.log"
STATUS_GET = (
    "if [ -e /mnt/us/dashboard/NOAUTOSTART ]; "
    "then echo autostart=disabled; else echo autostart=enabled; fi; "
    "lipc-get-prop com.lab126.powerd flIntensity"
)
BATTERY_STATUS_GET = (
    "cap=unknown; stat=unknown; volt=unknown; "
    "for f in /sys/class/power_supply/*/capacity; do "
    "[ -r \"$f\" ] && cap=$(cat \"$f\" 2>/dev/null) && break; "
    "done; "
    "for f in /sys/class/power_supply/*/status; do "
    "[ -r \"$f\" ] && stat=$(cat \"$f\" 2>/dev/null) && break; "
    "done; "
    "for f in /sys/class/power_supply/*/voltage_now; do "
    "[ -r \"$f\" ] && volt=$(cat \"$f\" 2>/dev/null) && break; "
    "done; "
    "echo capacity=$cap; echo status=$stat; echo voltage_now=$volt"
)


def get_saved_brightness(device_id="default-kindle"):
    try:
        import json
        from device_registry import DeviceRegistry
        registry = DeviceRegistry(Path(__file__).resolve().parent)
        device = registry.get(device_id)
        config_file = (
            Path(__file__).resolve().parent / "dashboard_config.json"
            if device_id == "default-kindle"
            else device.config_path
        )
        if config_file.exists():
            raw = json.loads(config_file.read_text(encoding="utf-8"))
            val = raw.get("kindle_frontlight", 8)
            if val in (0, 1, 4, 8, 12, 18):
                return val
    except Exception as e:
        print(f"Warning: Failed to load kindle_frontlight from config: {e}")
    return 8


class DeviceError(RuntimeError):
    """A safe device-control failure suitable for API responses."""


class KindleDevice:
    def __init__(self, connection=None):
        self.connection = connection

    def _get_ssh_base(self, connection, device_id=None):
        if connection is None:
            if device_id == "default-kindle" or device_id is None:
                return SSH_BASE
            raise DeviceError("Push not configured for this device")

        profile_name = connection.get("ssh_profile")
        if not profile_name or profile_name not in SSH_PROFILES:
            raise DeviceError("invalid or missing SSH profile")

        profile = SSH_PROFILES[profile_name]
        host = connection.get("host")
        user = connection.get("user")
        port = connection.get("port")

        if not host or not user:
            raise DeviceError("missing host or user in connection")

        ssh_args = [
            "/usr/bin/ssh",
            "-i", str(profile["key_path"]),
            "-o", f"UserKnownHostsFile={profile['known_hosts']}",
        ]
        for opt in profile.get("options", ()):
            ssh_args.append(opt)

        ssh_args.extend([
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ConnectionAttempts=1",
            "-o", "LogLevel=ERROR",
        ])
        if port is not None:
            ssh_args.extend(["-p", str(port)])

        ssh_args.append(f"{user}@{host}")
        return ssh_args

    def _run(self, args, timeout, cwd=None):
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeviceError("Kindle command timed out") from exc
        except OSError as exc:
            raise DeviceError("Kindle command could not start") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                raise DeviceError(f"Kindle command failed: {detail[-500:]}")
            raise DeviceError("Kindle command failed")
        return result.stdout

    def _run_remote(self, command, ssh_base, timeout=10):
        return self._run(ssh_base + [command], timeout)

    def run_action(self, action, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        definition = ACTION_COMMANDS.get(action)
        if definition is None:
            raise ValueError("unsupported device action")
        command, message, timeout = definition
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        self._run_remote(command, ssh_base, timeout)
        if action in ("start", "refresh"):
            try:
                self.set_light(get_saved_brightness(device_id), connection=conn, device_id=device_id, device_type=device_type)
            except Exception as e:
                print(f"Warning: Failed to reapply brightness on {action}: {e}")
        return message

    def push(self, connection=None, device_id="default-kindle", device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)

        from weather_image import render_device
        from device_registry import DeviceRegistry
        registry = DeviceRegistry(PROJECT_DIR)
        render_device(device_id, force=True, registry=registry)

        refresh_cmd, _, timeout = ACTION_COMMANDS["refresh"]
        self._run_remote(refresh_cmd, ssh_base, timeout)
        try:
            self.set_light(get_saved_brightness(device_id), connection=conn, device_id=device_id, device_type=device_type)
        except Exception as e:
            print(f"Warning: Failed to reapply brightness on push: {e}")
        return "Dashboard generated and pushed"

    def set_light(self, level, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        if isinstance(level, bool) or not isinstance(level, int):
            raise ValueError("brightness must be an integer")
        if level < 0 or level > 24:
            raise ValueError("brightness must be between 0 and 24")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        self._run_remote(
            f"lipc-set-prop com.lab126.powerd flIntensity {level}",
            ssh_base,
            10,
        )
        return self.get_light(connection=conn, device_id=device_id, device_type=device_type)

    def get_light(self, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        output = self._run_remote(LIGHT_GET, ssh_base, 10)
        values = [
            int(line.strip())
            for line in output.splitlines()
            if line.strip().isdigit()
        ]
        if not values or values[-1] < 0 or values[-1] > 24:
            raise DeviceError("Kindle returned an invalid brightness value")
        return values[-1]

    def get_battery_status(self, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        output = self._run_remote(BATTERY_STATUS_GET, ssh_base, 10)
        values = {}
        for line in output.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        battery_percent = None
        capacity = values.get("capacity")
        if capacity and capacity.isdigit():
            parsed = int(capacity)
            if 0 <= parsed <= 100:
                battery_percent = parsed

        charging = None
        status = values.get("status", "").lower()
        if status in ("charging", "full"):
            charging = True
        elif status in ("discharging", "not charging"):
            charging = False

        battery_voltage = None
        voltage = values.get("voltage_now")
        if voltage and voltage.isdigit():
            parsed_voltage = int(voltage)
            if parsed_voltage > 0:
                battery_voltage = round(parsed_voltage / 1000000, 3)

        return {
            "battery_percent": battery_percent,
            "charging": charging,
            "battery_voltage": battery_voltage,
        }

    def get_status(self, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        output = self._run_remote(STATUS_GET, ssh_base, 10)
        autostart = "unknown"
        brightness = None
        for line in output.splitlines():
            value = line.strip()
            if value in ("autostart=enabled", "autostart=disabled"):
                autostart = value.split("=", 1)[1]
            elif value.isdigit() and 0 <= int(value) <= 24:
                brightness = int(value)
        if brightness is None:
            raise DeviceError("Kindle status response was invalid")
        return {
            "connected": True,
            "autostart": autostart,
            "brightness": brightness,
        }

    def get_log(self, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        output = self._run_remote(LOG_GET, ssh_base, 10)
        return output.replace("\x00", "")[-32768:]

    def restart(self, confirmation, connection=None, device_id=None, device_type="kindle_pw1"):
        if device_type != "kindle_pw1":
            raise ValueError("unsupported device type")
        if confirmation != "RESTART":
            raise ValueError("restart confirmation is required")
        conn = connection if connection is not None else self.connection
        ssh_base = self._get_ssh_base(conn, device_id)
        self._run_remote("reboot", ssh_base, 15)
        return "Kindle restart requested"

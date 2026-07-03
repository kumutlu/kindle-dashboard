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

ACTION_COMMANDS = {
    "start": (
        "/mnt/us/dashboard/start-dashboard.sh --manual",
        "Dashboard started",
        20,
    ),
    "home": (
        "/mnt/us/dashboard/home.sh",
        "Kindle Home opened",
        20,
    ),
    "refresh": (
        "/mnt/us/dashboard/refresh-once.sh",
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


class DeviceError(RuntimeError):
    """A safe device-control failure suitable for API responses."""


class KindleDevice:
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
            raise DeviceError("Kindle command failed")
        return result.stdout

    def _run_remote(self, command, timeout=10):
        return self._run(SSH_BASE + [command], timeout)

    def run_action(self, action):
        definition = ACTION_COMMANDS.get(action)
        if definition is None:
            raise ValueError("unsupported device action")
        command, message, timeout = definition
        self._run_remote(command, timeout)
        return message

    def push(self):
        self._run(
            [str(RUN_DASHBOARD)],
            timeout=180,
            cwd=PROJECT_DIR,
        )
        self._run_remote(
            ACTION_COMMANDS["refresh"][0],
            ACTION_COMMANDS["refresh"][2],
        )
        return "Dashboard generated and pushed"

    def set_light(self, level):
        if isinstance(level, bool) or not isinstance(level, int):
            raise ValueError("brightness must be an integer")
        if level < 0 or level > 24:
            raise ValueError("brightness must be between 0 and 24")
        self._run_remote(
            f"lipc-set-prop com.lab126.powerd flIntensity {level}",
            10,
        )
        return self.get_light()

    def get_light(self):
        output = self._run_remote(LIGHT_GET, 10)
        values = [
            int(line.strip())
            for line in output.splitlines()
            if line.strip().isdigit()
        ]
        if not values or values[-1] < 0 or values[-1] > 24:
            raise DeviceError("Kindle returned an invalid brightness value")
        return values[-1]

    def get_status(self):
        output = self._run_remote(STATUS_GET, 10)
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

    def get_log(self):
        output = self._run_remote(LOG_GET, 10)
        return output.replace("\x00", "")[-32768:]

    def restart(self, confirmation):
        if confirmation != "RESTART":
            raise ValueError("restart confirmation is required")
        self._run_remote("reboot", 15)
        return "Kindle restart requested"

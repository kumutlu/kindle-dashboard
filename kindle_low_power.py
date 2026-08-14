#!/usr/bin/env python3
"""Generate a reversible true low-power pilot for default-kindle only."""

from __future__ import annotations

from dataclasses import dataclass
import shlex


class LowPowerTargetError(ValueError):
    """Raised when a low-power bundle is requested for an unsafe target."""


@dataclass(frozen=True)
class LowPowerDeployment:
    device_id: str
    interval_seconds: int
    files: dict[str, str]
    file_modes: dict[str, int]
    cron_line: str


PILOT_FILES = (
    "/mnt/us/dashboard/low-power-refresh-once.sh",
    "/mnt/us/dashboard/low-power-cycle.sh",
    "/mnt/us/dashboard/low-power-wake-handler.sh",
    "/mnt/us/dashboard/low-power-manual-start.sh",
    "/etc/upstart/default-kindle-low-power.conf",
    "/mnt/us/default-kindle-low-power-rollback/rollback.sh",
)


def validate_low_power_target(device) -> None:
    if getattr(device, "id", None) != "default-kindle":
        raise LowPowerTargetError(
            "true low-power pilot is restricted to default-kindle"
        )
    if getattr(device, "type", None) not in {"kindle_pw1", "kindle_kt4"}:
        raise LowPowerTargetError("low-power target must be a Kindle")
    if not getattr(device, "enabled", False):
        raise LowPowerTargetError("low-power target must be enabled")
    connection = getattr(device, "connection", None)
    if not isinstance(connection, dict):
        raise LowPowerTargetError("low-power target requires SSH connection")
    if connection.get("host") != "192.168.68.119":
        raise LowPowerTargetError(
            "default-kindle pilot host must be 192.168.68.119"
        )


def _refresh_script(image_url: str) -> str:
    return f'''#!/bin/sh

DASH="/mnt/us/dashboard"
STATE="$DASH/low-power-state"
IMAGE="$DASH/image.png"
IMAGE_URL={shlex.quote(image_url)}
LOG="$STATE/low-power.log"
ETAG_FILE="$STATE/etag"
MODIFIED_FILE="$STATE/last-modified"
TMP_IMAGE="$STATE/image.tmp.$$"
TMP_HEADERS="$STATE/headers.tmp.$$"
TMP_STATUS="$STATE/status.tmp.$$"
CURL="/mnt/us/usbnet/bin/curl"

mkdir -p "$STATE"

log() {{
    echo "$(date '+%Y-%m-%d %H:%M:%S') refresh $*" >> "$LOG"
}}

cleanup() {{
    rm -f "$TMP_IMAGE" "$TMP_HEADERS" "$TMP_STATUS"
}}
trap cleanup EXIT HUP INT TERM

if [ -e "$DASH/DISABLE_LOW_POWER" ] || [ -e "$DASH/NOAUTOSTART" ]; then
    log "disabled marker present"
    exit 2
fi

WIFI_BEFORE=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)
log "wifi_before=$WIFI_BEFORE"
if [ "$WIFI_BEFORE" != "CONNECTED" ]; then
    lipc-set-prop com.lab126.wifid enable 1 >/dev/null 2>&1 || true
fi

NETWORK_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 15 ]; do
    WIFI_STATE=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)
    if [ "$WIFI_STATE" = "CONNECTED" ]; then
        NETWORK_READY=1
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    /bin/sleep 2
done

if [ "$NETWORK_READY" -ne 1 ]; then
    log "network timeout"
    exit 3
fi

if [ ! -x "$CURL" ]; then
    log "curl unavailable"
    exit 4
fi

set -- -sS --connect-timeout 10 --max-time 35 -D "$TMP_HEADERS" \
    -o "$TMP_IMAGE" -w "%{{http_code}}" "$IMAGE_URL"
if [ -s "$ETAG_FILE" ]; then
    ETAG=$(sed -n '1p' "$ETAG_FILE")
    set -- "$@" -H "If-None-Match: $ETAG"
fi
if [ -s "$MODIFIED_FILE" ]; then
    MODIFIED=$(sed -n '1p' "$MODIFIED_FILE")
    set -- "$@" -H "If-Modified-Since: $MODIFIED"
fi

"$CURL" "$@" > "$TMP_STATUS"
CURL_RC=$?
HTTP_STATUS=$(sed -n '1p' "$TMP_STATUS")
echo "$HTTP_STATUS" > "$STATE/last-http-status"
log "http_status=$HTTP_STATUS curl_rc=$CURL_RC"

if [ "$CURL_RC" -ne 0 ]; then
    exit 5
fi

if [ "$HTTP_STATUS" = "304" ]; then
    log "image unchanged HTTP_STATUS=304"
    exit 0
fi

if [ "$HTTP_STATUS" != "200" ] || [ ! -s "$TMP_IMAGE" ]; then
    log "invalid response"
    exit 6
fi

if ! file "$TMP_IMAGE" 2>/dev/null | grep -q "PNG image data"; then
    log "download is not PNG"
    exit 7
fi

NEW_ETAG=$(grep -i '^ETag:' "$TMP_HEADERS" | tail -n 1 | sed 's/^[^:]*:[[:space:]]*//;s/\r$//')
NEW_MODIFIED=$(grep -i '^Last-Modified:' "$TMP_HEADERS" | tail -n 1 | sed 's/^[^:]*:[[:space:]]*//;s/\r$//')
[ -n "$NEW_ETAG" ] && echo "$NEW_ETAG" > "$ETAG_FILE"
[ -n "$NEW_MODIFIED" ] && echo "$NEW_MODIFIED" > "$MODIFIED_FILE"

mv -f "$TMP_IMAGE" "$IMAGE"
md5sum "$IMAGE" | sed 's/[[:space:]].*$//' > "$STATE/last-image-md5"
/usr/sbin/eips -c
/usr/sbin/eips -f
/usr/sbin/eips -g "$IMAGE"
log "image changed and rendered"
exit 0
'''


def _cycle_script() -> str:
    return '''#!/bin/sh

DASH="/mnt/us/dashboard"
STATE="$DASH/low-power-state"
LOG="$STATE/low-power.log"
LOCK="$STATE/cycle.lock"
INTERVAL_FILE="$STATE/pilot-interval-seconds"
WAKE_HANDLER="$DASH/low-power-wake-handler.sh"
ROLLBACK="/mnt/us/default-kindle-low-power-rollback/rollback.sh"

mkdir -p "$STATE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') cycle $*" >> "$LOG"
}

cleanup() {
    rmdir "$LOCK" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

if ! mkdir "$LOCK" 2>/dev/null; then
    log "duplicate cycle lock exists"
    exit 10
fi

if [ -e "$DASH/DISABLE_LOW_POWER" ]; then
    log "DISABLE_LOW_POWER checked: present"
    exit 11
fi
log "DISABLE_LOW_POWER checked: absent"

if [ -e "$DASH/NOAUTOSTART" ]; then
    log "NOAUTOSTART checked: present"
    exit 12
fi
log "NOAUTOSTART checked: absent"

if [ ! -x "$WAKE_HANDLER" ]; then
    log "wake handler missing or not executable"
    exit 13
fi
log "wake handler executable"

if [ ! -x "$ROLLBACK" ]; then
    log "rollback script missing or not executable"
    exit 14
fi
log "rollback script executable"

INTERVAL_SECONDS=$(sed -n '1p' "$INTERVAL_FILE" 2>/dev/null)
case "$INTERVAL_SECONDS" in
    180|3600) ;;
    *) log "invalid interval=$INTERVAL_SECONDS"; exit 15 ;;
esac

SEQ=$(sed -n '1p' "$STATE/cycle-sequence" 2>/dev/null)
case "$SEQ" in ''|*[!0-9]*) SEQ=0 ;; esac
SEQ=$((SEQ + 1))
echo "$SEQ" > "$STATE/cycle-sequence"
log "sequence=$SEQ source=${1-manual} battery=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null) power_state=$(lipc-get-prop com.lab126.powerd state 2>/dev/null)"

"$DASH/low-power-refresh-once.sh"
REFRESH_RC=$?
log "refresh_rc=$REFRESH_RC"

if [ -e "$DASH/DISABLE_LOW_POWER" ] || [ -e "$DASH/NOAUTOSTART" ]; then
    log "disable marker appeared after refresh"
    exit 16
fi

if pgrep wget >/dev/null 2>&1 || pgrep curl >/dev/null 2>&1; then
    log "downloader still running"
    exit 17
fi

NOW=$(date +%s)
DUE=$((NOW + INTERVAL_SECONDS))
echo "$DUE" > "$STATE/next-cycle-due"

if ! lipc-set-prop com.lab126.powerd rtcWakeup "$INTERVAL_SECONDS"; then
    log "rtcWakeup scheduling failed interval=$INTERVAL_SECONDS"
    exit 18
fi
log "rtcWakeup verified interval=$INTERVAL_SECONDS due=$DUE"

if [ ! -x "$WAKE_HANDLER" ] || [ ! -x "$ROLLBACK" ]; then
    log "final executable prerequisite failed"
    exit 19
fi
if [ -e "$DASH/DISABLE_LOW_POWER" ]; then
    log "final DISABLE_LOW_POWER check failed"
    exit 20
fi

ps aux > "$STATE/processes-before-suspend.txt"
lipc-get-prop com.lab126.wifid cmState > "$STATE/wifi-before-suspend.txt" 2>/dev/null || true
log "suspend requested power_state=$(lipc-get-prop com.lab126.powerd state 2>/dev/null)"
lipc-set-prop com.lab126.powerd preventScreenSaver 0
lipc-set-prop com.lab126.powerd powerButton 1
exit 0
'''


def _wake_handler_script() -> str:
    return '''#!/bin/sh

DASH="/mnt/us/dashboard"
STATE="$DASH/low-power-state"
LOG="$STATE/low-power.log"

[ -e "$DASH/DISABLE_LOW_POWER" ] && exit 0
[ -e "$DASH/NOAUTOSTART" ] && exit 0
[ -d "$STATE/cycle.lock" ] && exit 0

DUE=$(sed -n '1p' "$STATE/next-cycle-due" 2>/dev/null)
case "$DUE" in ''|*[!0-9]*) exit 0 ;; esac
NOW=$(date +%s)
if [ "$NOW" -lt "$DUE" ]; then
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') wake due=$DUE now=$NOW power_state=$(lipc-get-prop com.lab126.powerd state 2>/dev/null)" >> "$LOG"
rm -f "$STATE/next-cycle-due"
"$DASH/low-power-cycle.sh" --rtc-resume >> "$LOG" 2>&1 &
exit 0
'''


def _manual_start_script() -> str:
    return '''#!/bin/sh

DASH="/mnt/us/dashboard"
STATE="$DASH/low-power-state"
INTERVAL=${1-180}

case "$INTERVAL" in 180|3600) ;; *) echo "interval must be 180 or 3600" >&2; exit 2 ;; esac
if [ -e "$DASH/DISABLE_LOW_POWER" ] || [ -e "$DASH/NOAUTOSTART" ]; then
    echo "low-power mode is disabled" >&2
    exit 3
fi
if [ ! -x "/mnt/us/default-kindle-low-power-rollback/rollback.sh" ]; then
    echo "rollback is unavailable" >&2
    exit 4
fi

mkdir -p "$STATE"
echo "$INTERVAL" > "$STATE/pilot-interval-seconds"
pkill -f "/mnt/us/dashboard/refresh.sh" 2>/dev/null || true
"$DASH/low-power-cycle.sh" --manual
'''


def _upstart_script() -> str:
    return '''description "Default Kindle true low-power pilot"

start on started lab126_gui
task

script
    [ -e /mnt/us/dashboard/DISABLE_LOW_POWER ] && exit 0
    [ -e /mnt/us/dashboard/NOAUTOSTART ] && exit 0
    stop kindle-dashboard 2>/dev/null || true
    /mnt/us/dashboard/low-power-cycle.sh --boot >> /mnt/us/dashboard/low-power-state/low-power.log 2>&1
end script
'''


def _rollback_script() -> str:
    return '''#!/bin/sh

DASH="/mnt/us/dashboard"
ROOT="/mnt/us/default-kindle-low-power-rollback"
SNAPSHOT="$ROOT/snapshot"

if [ "${1-}" = "--check" ]; then
    test -f "$SNAPSHOT/kindle-dashboard.conf" || exit 21
    test -f "$SNAPSHOT/crontab-root" || exit 22
    test -f "$DASH/start-dashboard.sh" || exit 23
    test -f "$DASH/refresh.sh" || exit 24
    test -f "$DASH/refresh-once.sh" || exit 25
    exit 0
fi

touch "$DASH/DISABLE_LOW_POWER"
pkill -f "$DASH/low-power-cycle.sh" 2>/dev/null || true
pkill -f "$DASH/low-power-refresh-once.sh" 2>/dev/null || true
rm -rf "$DASH/low-power-state/cycle.lock"
rm -f /etc/upstart/default-kindle-low-power.conf
cp -p "$SNAPSHOT/crontab-root" /etc/crontab/root
cp -p "$SNAPSHOT/kindle-dashboard.conf" /etc/upstart/kindle-dashboard.conf
chmod 755 "$DASH/start-dashboard.sh" "$DASH/refresh.sh" "$DASH/refresh-once.sh"
rm -f "$DASH/NOAUTOSTART"
rm -f "$DASH/DISABLE_LOW_POWER"
lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1 || true
"$DASH/start-dashboard.sh" --manual
echo "legacy dashboard restored"
'''


def render_low_power_bundle(
    device, config: dict, server_host: str, image_port: int
) -> dict[str, str]:
    validate_low_power_target(device)
    if not server_host or any(c.isspace() for c in server_host):
        raise ValueError("server host is invalid")
    if not isinstance(image_port, int) or not 1 <= image_port <= 65535:
        raise ValueError("image port is invalid")
    image_url = (
        f"http://{server_host}:{image_port}/device/{device.id}/image.png"
    )
    return {
        PILOT_FILES[0]: _refresh_script(image_url),
        PILOT_FILES[1]: _cycle_script(),
        PILOT_FILES[2]: _wake_handler_script(),
        PILOT_FILES[3]: _manual_start_script(),
        PILOT_FILES[4]: _upstart_script(),
        PILOT_FILES[5]: _rollback_script(),
    }


def build_low_power_deployment(
    device, config: dict, server_host: str, image_port: int
) -> LowPowerDeployment:
    files = render_low_power_bundle(device, config, server_host, image_port)
    minutes = config.get("refresh_interval_minutes", 60)
    interval_seconds = 3600 if minutes == 60 else 180
    return LowPowerDeployment(
        device_id=device.id,
        interval_seconds=interval_seconds,
        files=files,
        file_modes={path: 0o755 for path in files},
        cron_line=(
            "* * * * * /mnt/us/dashboard/low-power-wake-handler.sh"
        ),
    )

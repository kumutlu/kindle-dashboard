#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
RTC_WAKEUP="${RTC_WAKEUP:-/sys/devices/platform/mxc_rtc.0/wakeup_enable}"
POWER_STATE="${POWER_STATE:-/sys/power/state}"
PID_FILE="$DASHBOARD_DIR/mxc-rtc-scheduler.pid"
ACTIVATE_FILE="$DASHBOARD_DIR/mxc-rtc.activate"
ACTIVE_FILE="$DASHBOARD_DIR/mxc-rtc.active"
COMMIT_FILE="$DASHBOARD_DIR/mxc-rtc.commit"
COMMITTED_FILE="$DASHBOARD_DIR/mxc-rtc.committed"

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [MXC-RTC] $1"
}

get_start_time() {
    _PID="$1"
    _SF="/proc/$_PID/stat"
    [ -f "$_SF" ] || return 1
    _SL=$(cat "$_SF" 2>/dev/null) || return 1
    _REM="${_SL##*) }"
    [ -n "$_REM" ] || return 1
    set -- $_REM
    _ST="${20}"
    case "$_ST" in
        ""|*[!0-9]*) return 1 ;;
        *) printf '%s\n' "$_ST" ;;
    esac
}

cleanup() {
    if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PID_FILE"
    fi
    for _STATE_FILE in "$ACTIVE_FILE" "$COMMITTED_FILE"; do
        if [ -f "$_STATE_FILE" ]; then
            _STATE=$(cat "$_STATE_FILE" 2>/dev/null || true)
            case "$_STATE" in
                "$$ "*) rm -f "$_STATE_FILE" ;;
            esac
        fi
    done
}

trap 'cleanup; exit 0' HUP INT TERM
trap cleanup EXIT

if [ ! -w "$RTC_WAKEUP" ]; then
    log_msg "ERROR: RTC wakeup capability missing or not writable ($RTC_WAKEUP)"
    exit 1
fi
if [ ! -f "$POWER_STATE" ] || ! grep -q 'mem' "$POWER_STATE" 2>/dev/null; then
    log_msg "ERROR: Suspend-to-RAM (mem) capability missing ($POWER_STATE)"
    exit 1
fi
if [ ! -x "$DASHBOARD_DIR/refresh-once.sh" ]; then
    log_msg "ERROR: $DASHBOARD_DIR/refresh-once.sh not executable"
    exit 1
fi

printf '%s\n' "$$" > "${PID_FILE}.tmp"
mv -f "${PID_FILE}.tmp" "$PID_FILE"
MY_STARTTIME=$(get_start_time "$$") || {
    log_msg "ERROR: could not read scheduler starttime"
    exit 1
}
EXPECTED_TOKEN="$$ $MY_STARTTIME"
rm -f "$ACTIVATE_FILE" "$ACTIVE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE"
log_msg "Standby started (PID $$, ST $MY_STARTTIME). Waiting for activation."

while true; do
    if [ -f "$ACTIVATE_FILE" ]; then
        ACT_CONTENTS=$(cat "$ACTIVATE_FILE" 2>/dev/null || true)
        if [ "$ACT_CONTENTS" = "$EXPECTED_TOKEN" ]; then
            rm -f "$ACTIVATE_FILE"
            printf '%s\n' "$EXPECTED_TOKEN" > "${ACTIVE_FILE}.tmp"
            mv -f "${ACTIVE_FILE}.tmp" "$ACTIVE_FILE"
            log_msg "Activation accepted. Waiting for commit."
            break
        fi
        log_msg "WARNING: ignored stale activation token: $ACT_CONTENTS"
        rm -f "$ACTIVATE_FILE"
    fi
    sleep 1
done

while true; do
    if [ -f "$COMMIT_FILE" ]; then
        COMMIT_CONTENTS=$(cat "$COMMIT_FILE" 2>/dev/null || true)
        if [ "$COMMIT_CONTENTS" = "$EXPECTED_TOKEN" ]; then
            rm -f "$COMMIT_FILE"
            printf '%s\n' "$EXPECTED_TOKEN" > "${COMMITTED_FILE}.tmp"
            mv -f "${COMMITTED_FILE}.tmp" "$COMMITTED_FILE"
            log_msg "Commit accepted. Entering active cadence."
            break
        fi
        log_msg "WARNING: ignored stale commit token: $COMMIT_CONTENTS"
        rm -f "$COMMIT_FILE"
    fi
    sleep 1
done

while true; do
    if [ -f "$DASHBOARD_DIR/device.env" ]; then
        . "$DASHBOARD_DIR/device.env"
    fi

    INTERVAL_MINS="${REFRESH_INTERVAL_MINUTES:-60}"
    case "$INTERVAL_MINS" in
        5) INTERVAL_SECS=300 ;;
        10) INTERVAL_SECS=600 ;;
        15) INTERVAL_SECS=900 ;;
        30) INTERVAL_SECS=1800 ;;
        60) INTERVAL_SECS=3600 ;;
        *) INTERVAL_SECS=3600 ;;
    esac

    if ! "$DASHBOARD_DIR/refresh-once.sh"; then
        log_msg "WARNING: refresh-once.sh failed; preserving cadence"
    fi

    sync
    if ! printf '%s' "$INTERVAL_SECS" > "$RTC_WAKEUP" 2>/dev/null; then
        log_msg "ERROR: failed to arm RTC wakeup"
        exit 1
    fi
    VERIFIED_SECS=$(cat "$RTC_WAKEUP" 2>/dev/null || true)
    if [ "$VERIFIED_SECS" != "$INTERVAL_SECS" ]; then
        log_msg "ERROR: RTC wake verification failed (expected $INTERVAL_SECS, read $VERIFIED_SECS)"
        exit 1
    fi

    echo mem > "$POWER_STATE"
done

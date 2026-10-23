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

. "$DASHBOARD_DIR/mxc-rtc-common.sh"
SCHEDULER_LOCK_HELD=0

cleanup() {
    _SCHEDULER_RC=$?
    trap - EXIT HUP INT TERM
    [ "$SCHEDULER_LOCK_HELD" = 1 ] || exit "$_SCHEDULER_RC"
    if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PID_FILE"
    fi
    for _STATE_FILE in "$ACTIVE_FILE" "$COMMITTED_FILE"; do
        if [ -f "$_STATE_FILE" ]; then
            _STATE=$(cat "$_STATE_FILE" 2>/dev/null || true)
            case "$_STATE" in
                "${EXPECTED_TOKEN:-unset}") rm -f "$_STATE_FILE" ;;
            esac
        fi
    done
    if [ "$_SCHEDULER_RC" -ne 0 ]; then
        log_msg "ERROR: scheduler failed; suspend stopped; recovery required"
        printf '%s\n' "native scheduler failed" > "$DASHBOARD_DIR/mxc-rtc.failed" || true
    fi
    rmdir "$MXC_SCHEDULER_LOCK" || true
    exit "$_SCHEDULER_RC"
}
trap 'exit 0' HUP INT TERM
trap cleanup EXIT

if ! mkdir "$MXC_SCHEDULER_LOCK" 2>/dev/null; then
    log_msg "ERROR: another native scheduler owns the lock"
    exit 1
fi
SCHEDULER_LOCK_HELD=1

load_interval() {
    if [ -f "$DASHBOARD_DIR/device.env" ]; then
        . "$DASHBOARD_DIR/device.env"
    fi
    case "${REFRESH_INTERVAL_MINUTES:-60}" in
        5) INTERVAL_SECS=300 ;;
        10) INTERVAL_SECS=600 ;;
        15) INTERVAL_SECS=900 ;;
        30) INTERVAL_SECS=1800 ;;
        60) INTERVAL_SECS=3600 ;;
        *) INTERVAL_SECS=3600 ;;
    esac
}

arm_rtc() {
    if ! printf '%s' "$INTERVAL_SECS" > "$RTC_WAKEUP" 2>/dev/null; then
        log_msg "ERROR: failed to arm RTC wakeup"
        return 1
    fi
    VERIFIED_SECS=$(cat "$RTC_WAKEUP" 2>/dev/null || true)
    if [ "$VERIFIED_SECS" != "$INTERVAL_SECS" ]; then
        log_msg "ERROR: RTC wake verification failed (expected $INTERVAL_SECS, read $VERIFIED_SECS)"
        return 1
    fi
}

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

MY_STARTTIME=$(get_start_time "$$") || {
    log_msg "ERROR: could not read scheduler starttime"
    exit 1
}
EXPECTED_TOKEN="$$ $MY_STARTTIME"
rm -f "$ACTIVATE_FILE" "$ACTIVE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE" "$DASHBOARD_DIR/mxc-rtc.failed"
printf '%s\n' "$$" > "${PID_FILE}.tmp"
mv -f "${PID_FILE}.tmp" "$PID_FILE"
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
            persistence_ready || {
                log_msg "ERROR: commit refused without persistence receipt"
                exit 1
            }
            # Flush installer state before ACK. The ACK is an ephemeral message,
            # not a boot credential. Never suspend before RTC readback succeeds.
            sync
            load_interval
            arm_rtc || exit 1
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
    load_interval
    if ! "$DASHBOARD_DIR/refresh-once.sh"; then
        log_msg "WARNING: refresh-once.sh failed; preserving cadence"
    fi
    sync
    arm_rtc || exit 1
    echo mem > "$POWER_STATE"
done

#!/bin/sh
set -u

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
PROC_DIR="${PROC_DIR:-/proc}"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
PID_FILE="$DASHBOARD_DIR/mxc-rtc-scheduler.pid"
ACTIVE_FILE="$DASHBOARD_DIR/mxc-rtc.active"
ACTIVATE_FILE="$DASHBOARD_DIR/mxc-rtc.activate"
COMMIT_FILE="$DASHBOARD_DIR/mxc-rtc.commit"
COMMITTED_FILE="$DASHBOARD_DIR/mxc-rtc.committed"

. "$DASHBOARD_DIR/mxc-rtc-common.sh"

clear_state_files() {
    if native_processes_present; then
        echo "ERROR: native process exists without usable PID state; refusing cleanup" >&2
        return 1
    fi
    # Only a proven absence of native processes permits stale-lock recovery.
    if [ -d "$MXC_SCHEDULER_LOCK" ]; then
        rmdir "$MXC_SCHEDULER_LOCK" || return 1
    fi
    rm -f "$PID_FILE" "$ACTIVE_FILE" "$ACTIVATE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE"
}

if [ ! -f "$PID_FILE" ]; then
    clear_state_files || exit 1
    exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)
case "$PID" in ""|*[!0-9]*) clear_state_files || exit 1; exit 0 ;; esac
if [ -z "$PID" ] || [ ! -d "$PROC_DIR/$PID" ]; then
    clear_state_files || exit 1
    exit 0
fi

if ! tr '\000' '\n' < "$PROC_DIR/$PID/cmdline" | grep -Fx "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >/dev/null 2>&1; then
    # Stale PID may name an unrelated process or an unreaped zombie. Leave it
    # untouched; cleanup is safe only after scanning for every native owner.
    clear_state_files || exit 1
    exit 0
fi

START=$(get_start_time "$PID" 2>/dev/null || true)
if [ -z "$START" ]; then
    echo "ERROR: refusing to kill PID $PID; starttime unavailable" >&2
    exit 1
fi

if [ "${MXC_INTERNAL_STOP:-0}" != 1 ]; then
    printf '%s\n' "$PID $START" > "$DASHBOARD_DIR/mxc-rtc.stop-requested" || exit 1
fi
kill "$PID" 2>/dev/null || true
"$SLEEP_BIN" 1

if [ -d "$PROC_DIR/$PID" ]; then
    NOW=$(get_start_time "$PID" 2>/dev/null || true)
    if [ "$NOW" = "$START" ] && tr '\000' '\n' < "$PROC_DIR/$PID/cmdline" | grep -Fx "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >/dev/null 2>&1; then
        kill -9 "$PID" 2>/dev/null || true
        "$SLEEP_BIN" 1
    fi
fi

if [ -d "$PROC_DIR/$PID" ]; then
    NOW=$(get_start_time "$PID" 2>/dev/null || true)
    if [ "$NOW" = "$START" ] && tr '\000' '\n' < "$PROC_DIR/$PID/cmdline" | grep -Fx "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >/dev/null 2>&1; then
        echo "ERROR: native scheduler PID $PID survived SIGKILL" >&2
        exit 1
    fi
fi

clear_state_files || exit 1
exit 0

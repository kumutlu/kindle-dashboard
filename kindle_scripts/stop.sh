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

get_start_time() {
    _PID="$1"
    _SF="$PROC_DIR/$_PID/stat"
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

clear_state_files() {
    rm -f "$PID_FILE" "$ACTIVE_FILE" "$ACTIVATE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE"
}

if [ ! -f "$PID_FILE" ]; then
    clear_state_files
    exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)
if [ -z "$PID" ] || [ ! -d "$PROC_DIR/$PID" ]; then
    clear_state_files
    exit 0
fi

if ! grep -F 'mxc-rtc-scheduler.sh' "$PROC_DIR/$PID/cmdline" >/dev/null 2>&1; then
    echo "ERROR: refusing to kill PID $PID; cmdline does not match mxc-rtc-scheduler.sh" >&2
    exit 1
fi

START=$(get_start_time "$PID" 2>/dev/null || true)
if [ -z "$START" ]; then
    echo "ERROR: refusing to kill PID $PID; starttime unavailable" >&2
    exit 1
fi

kill "$PID" 2>/dev/null || true
"$SLEEP_BIN" 1

if [ -d "$PROC_DIR/$PID" ]; then
    NOW=$(get_start_time "$PID" 2>/dev/null || true)
    if [ "$NOW" = "$START" ] && grep -F 'mxc-rtc-scheduler.sh' "$PROC_DIR/$PID/cmdline" >/dev/null 2>&1; then
        kill -9 "$PID" 2>/dev/null || true
        "$SLEEP_BIN" 1
    fi
fi

if [ -d "$PROC_DIR/$PID" ]; then
    NOW=$(get_start_time "$PID" 2>/dev/null || true)
    if [ "$NOW" = "$START" ] && grep -F 'mxc-rtc-scheduler.sh' "$PROC_DIR/$PID/cmdline" >/dev/null 2>&1; then
        echo "ERROR: native scheduler PID $PID survived SIGKILL" >&2
        exit 1
    fi
fi

clear_state_files
exit 0

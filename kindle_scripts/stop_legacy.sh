#!/bin/sh
set -u

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"

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

matches_process() {
    _PID="$1"
    _EXPECTED="$2"
    [ -d "/proc/$_PID" ] || return 1
    grep -F "$_EXPECTED" "/proc/$_PID/cmdline" >/dev/null 2>&1
}

safe_kill_pid() {
    _PID="$1"
    _EXPECTED="$2"
    [ -n "$_PID" ] || return 0
    [ -d "/proc/$_PID" ] || return 0
    matches_process "$_PID" "$_EXPECTED" || return 0

    _START=$(get_start_time "$_PID" 2>/dev/null || true)
    [ -n "$_START" ] || return 1

    kill "$_PID" 2>/dev/null || true
    sleep 1

    if [ -d "/proc/$_PID" ]; then
        _NOW=$(get_start_time "$_PID" 2>/dev/null || true)
        if [ "$_NOW" = "$_START" ] && matches_process "$_PID" "$_EXPECTED"; then
            kill -9 "$_PID" 2>/dev/null || true
            sleep 1
        fi
    fi

    if [ -d "/proc/$_PID" ]; then
        _NOW=$(get_start_time "$_PID" 2>/dev/null || true)
        if [ "$_NOW" = "$_START" ] && matches_process "$_PID" "$_EXPECTED"; then
            echo "ERROR: failed to stop $_EXPECTED ($_PID)" >&2
            return 1
        fi
    fi
    return 0
}

safe_kill_file() {
    _PID_FILE="$1"
    _EXPECTED="$2"
    [ -f "$_PID_FILE" ] || return 0
    _PID=$(cat "$_PID_FILE" 2>/dev/null || true)
    if safe_kill_pid "$_PID" "$_EXPECTED"; then
        rm -f "$_PID_FILE"
        return 0
    fi
    return 1
}

kill_scanned_dashboard_processes() {
    _SCRIPT="$1"
    _EXPECTED="$DASHBOARD_DIR/$_SCRIPT"
    _PIDS=$(ps auxww 2>/dev/null | grep -F "$_EXPECTED" | grep -v grep | awk '{print $2}' || true)
    _RC=0
    for _PID in $_PIDS; do
        safe_kill_pid "$_PID" "$_EXPECTED" || _RC=1
    done
    return "$_RC"
}

RC=0
safe_kill_file "$DASHBOARD_DIR/refresh.pid" "$DASHBOARD_DIR/refresh.sh" || RC=1
safe_kill_file "$DASHBOARD_DIR/watchdog.pid" "$DASHBOARD_DIR/watchdog.sh" || RC=1
safe_kill_file "$DASHBOARD_DIR/dashboard_loop.pid" "$DASHBOARD_DIR/dashboard_loop.sh" || RC=1

# Older PW1 installs launched refresh.sh without a PID file. Scan only for
# dashboard-owned absolute script paths and still validate /proc identity.
kill_scanned_dashboard_processes "refresh.sh" || RC=1
kill_scanned_dashboard_processes "watchdog.sh" || RC=1
kill_scanned_dashboard_processes "dashboard_loop.sh" || RC=1

exit "$RC"

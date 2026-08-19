#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
PROC_DIR="${PROC_DIR:-/proc}"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
PID_FILE="$DASHBOARD_DIR/mxc-rtc-scheduler.pid"
ACTIVATE_FILE="$DASHBOARD_DIR/mxc-rtc.activate"
ACTIVE_FILE="$DASHBOARD_DIR/mxc-rtc.active"
COMMIT_FILE="$DASHBOARD_DIR/mxc-rtc.commit"
COMMITTED_FILE="$DASHBOARD_DIR/mxc-rtc.committed"

log_msg() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [MXC-START] $1" >> "$DASHBOARD_DIR/mxc-rtc.log"
}

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

verify_identity() {
    _PID="$1"
    _START="$2"
    [ -d "$PROC_DIR/$_PID" ] || return 1
    _NOW=$(get_start_time "$_PID" 2>/dev/null || true)
    [ -n "$_NOW" ] && [ "$_NOW" = "$_START" ] || return 1
    grep -F 'mxc-rtc-scheduler.sh' "$PROC_DIR/$_PID/cmdline" >/dev/null 2>&1
}

rollback_to_legacy() {
    if [ -x "$DASHBOARD_DIR/stop.sh" ]; then
        "$DASHBOARD_DIR/stop.sh" >/dev/null 2>&1 || true
    fi
    if [ -x "$DASHBOARD_DIR/start-dashboard.sh" ]; then
        "$DASHBOARD_DIR/start-dashboard.sh" >/dev/null 2>&1 || true
    fi
}

if [ -f "$DASHBOARD_DIR/NOAUTOSTART" ]; then
    log_msg "NOAUTOSTART present; native scheduler start skipped"
    exit 0
fi

if [ -f "$PID_FILE" ]; then
    _OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    _OLD_ST=$(get_start_time "$_OLD_PID" 2>/dev/null || true)
    _OLD_ACK=$(cat "$COMMITTED_FILE" 2>/dev/null || true)
    if [ -n "$_OLD_PID" ] && [ -n "$_OLD_ST" ] && verify_identity "$_OLD_PID" "$_OLD_ST" && [ "$_OLD_ACK" = "$_OLD_PID $_OLD_ST" ]; then
        log_msg "native scheduler already committed"
        exit 0
    fi
fi

rm -f "$ACTIVATE_FILE" "$ACTIVE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE"
if [ -x "$DASHBOARD_DIR/stop.sh" ]; then
    "$DASHBOARD_DIR/stop.sh" >/dev/null 2>&1 || true
fi

[ -x "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" ] || {
    log_msg "missing mxc-rtc-scheduler.sh"
    exit 1
}

"$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >> "$DASHBOARD_DIR/mxc-rtc.log" 2>&1 &

PID=""
STARTTIME=""
COUNT=0
while [ "$COUNT" -lt 5 ]; do
    "$SLEEP_BIN" 1
    if [ -f "$PID_FILE" ]; then
        CANDIDATE=$(cat "$PID_FILE" 2>/dev/null || true)
        ST=$(get_start_time "$CANDIDATE" 2>/dev/null || true)
        if [ -n "$CANDIDATE" ] && [ -n "$ST" ] && verify_identity "$CANDIDATE" "$ST"; then
            PID="$CANDIDATE"
            STARTTIME="$ST"
            break
        fi
    fi
    COUNT=$((COUNT + 1))
done

if [ -z "$PID" ]; then
    log_msg "native standby identity verification failed"
    rollback_to_legacy
    exit 1
fi

if [ -x "$DASHBOARD_DIR/stop_legacy.sh" ]; then
    if ! "$DASHBOARD_DIR/stop_legacy.sh"; then
        log_msg "legacy shutdown failed"
        rollback_to_legacy
        exit 1
    fi
fi

TOKEN="$PID $STARTTIME"
printf '%s\n' "$TOKEN" > "${ACTIVATE_FILE}.tmp"
mv -f "${ACTIVATE_FILE}.tmp" "$ACTIVATE_FILE"

COUNT=0
ACK=""
while [ "$COUNT" -lt 5 ]; do
    "$SLEEP_BIN" 1
    ACK=$(cat "$ACTIVE_FILE" 2>/dev/null || true)
    [ "$ACK" = "$TOKEN" ] && break
    COUNT=$((COUNT + 1))
done

if [ "$ACK" != "$TOKEN" ] || ! verify_identity "$PID" "$STARTTIME"; then
    log_msg "activation ACK failed; rolling back"
    rollback_to_legacy
    exit 1
fi

printf '%s\n' "$TOKEN" > "${COMMIT_FILE}.tmp"
mv -f "${COMMIT_FILE}.tmp" "$COMMIT_FILE"

COUNT=0
COMMITTED=""
while [ "$COUNT" -lt 5 ]; do
    "$SLEEP_BIN" 1
    COMMITTED=$(cat "$COMMITTED_FILE" 2>/dev/null || true)
    [ "$COMMITTED" = "$TOKEN" ] && break
    COUNT=$((COUNT + 1))
done

if [ "$COMMITTED" != "$TOKEN" ] || ! verify_identity "$PID" "$STARTTIME"; then
    log_msg "commit ACK failed; rolling back"
    rollback_to_legacy
    exit 1
fi

log_msg "native scheduler committed: $TOKEN"
exit 0

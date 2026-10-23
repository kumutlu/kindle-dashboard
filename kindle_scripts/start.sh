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

TX_STARTED=0
LEGACY_TOUCHED=1
CONTROL_LOCK_HELD=0
. "$DASHBOARD_DIR/mxc-rtc-common.sh"

finish() {
    _EXIT_RC=$?
    trap - EXIT HUP INT TERM
    if [ "$_EXIT_RC" -ne 0 ] && [ "$TX_STARTED" = 1 ]; then
        rollback_to_legacy || log_msg "ERROR: ROLLBACK FAILED; manual recovery required"
    fi
    if [ "$CONTROL_LOCK_HELD" = 1 ]; then
        rmdir "$MXC_CONTROL_LOCK" || true
    fi
    exit "$_EXIT_RC"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM

verify_identity() {
    [ -d "$PROC_DIR/$1" ] || return 1
    [ "$(get_start_time "$1" 2>/dev/null || true)" = "$2" ] || return 1
    tr '\000' '\n' < "$PROC_DIR/$1/cmdline" | grep -Fx "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >/dev/null 2>&1
}

supervise() {
    # Monitor even an already committed process: wait(1) only handles our children.
    rmdir "$MXC_CONTROL_LOCK"
    CONTROL_LOCK_HELD=0
    TX_STARTED=0
    while verify_identity "$1" "$2"; do
        "$SLEEP_BIN" 1
    done
    log_msg "ERROR: native scheduler exited; attempting recovery"
    acquire_control_lock || exit 1
    TX_STARTED=1
    exit 1
}

if [ -f "$DASHBOARD_DIR/NOAUTOSTART" ]; then
    log_msg "NOAUTOSTART present; native scheduler start skipped"
    exit 0
fi

acquire_control_lock || exit 1
[ -x "$DASHBOARD_DIR/stop.sh" ] && [ -x "$DASHBOARD_DIR/stop_legacy.sh" ] && [ -x "$DASHBOARD_DIR/start-dashboard.sh" ] || {
    log_msg "ERROR: recovery scripts missing; refusing native startup"
    exit 1
}
TX_STARTED=1
persistence_ready || {
    log_msg "ERROR: missing or invalid persistence receipt"
    exit 1
}

if [ -f "$PID_FILE" ]; then
    _OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    _OLD_ST=$(get_start_time "$_OLD_PID" 2>/dev/null || true)
    _OLD_ACK=$(cat "$COMMITTED_FILE" 2>/dev/null || true)
    if [ -n "$_OLD_PID" ] && [ -n "$_OLD_ST" ] && verify_identity "$_OLD_PID" "$_OLD_ST" && [ "$_OLD_ACK" = "$_OLD_PID $_OLD_ST" ]; then
        log_msg "native scheduler already committed"
        TX_STARTED=0
        if [ "${1:-}" = --supervise ]; then supervise "$_OLD_PID" "$_OLD_ST"; fi
        exit 0
    fi
fi

rm -f "$ACTIVATE_FILE" "$ACTIVE_FILE" "$COMMIT_FILE" "$COMMITTED_FILE"
if [ -x "$DASHBOARD_DIR/stop.sh" ]; then
    "$DASHBOARD_DIR/stop.sh" || exit 1
    native_processes_present && exit 1
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
    exit 1
fi

if ! "$DASHBOARD_DIR/stop_legacy.sh" || legacy_processes_present; then
    log_msg "legacy shutdown failed"
    exit 1
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
    exit 1
fi

TX_STARTED=0
log_msg "native scheduler committed: $TOKEN"
if [ "${1:-}" = --supervise ]; then supervise "$PID" "$STARTTIME"; fi
exit 0

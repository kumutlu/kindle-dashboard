#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
LOG_FILE="${LOG_FILE:-$DASHBOARD_DIR/mxc-rtc-install.log}"
MNTROOT_BIN="${MNTROOT_BIN:-mntroot}"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
PROC_DIR="${PROC_DIR:-/proc}"
ROOTFS_RW=0

log_msg() {
    LINE="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$LINE"
    echo "$LINE" >> "$LOG_FILE"
}

fail() {
    log_msg "ERROR: $1" >&2
    exit 1
}

restore_rootfs() {
    if [ "${ROOTFS_RW:-0}" = "1" ]; then
        "$MNTROOT_BIN" ro 2>/dev/null || true
        ROOTFS_RW=0
    fi
}

on_signal() {
    restore_rootfs
    exit 1
}

trap restore_rootfs EXIT
trap on_signal HUP INT TERM

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

stop_native_scheduler() {
    if [ -x "$DASHBOARD_DIR/stop.sh" ]; then
        "$DASHBOARD_DIR/stop.sh" >/dev/null 2>&1 || true
    fi
    rm -f \
        "$DASHBOARD_DIR/mxc-rtc.activate" \
        "$DASHBOARD_DIR/mxc-rtc.active" \
        "$DASHBOARD_DIR/mxc-rtc.commit" \
        "$DASHBOARD_DIR/mxc-rtc.committed"
}

legacy_processes_present() {
    ps auxww 2>/dev/null | grep -F "$DASHBOARD_DIR/" | grep -e 'refresh.sh' -e 'watchdog.sh' -e 'dashboard_loop.sh' | grep -v grep >/dev/null 2>&1
}

rollback_to_legacy() {
    rm -f "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART"
    stop_native_scheduler || true
    if [ -x "$DASHBOARD_DIR/start-dashboard.sh" ]; then
        log_msg "Restarting legacy using proven startup path"
        "$DASHBOARD_DIR/start-dashboard.sh" >/dev/null 2>&1 || true
    fi
}

install_native_upstart() {
    if [ -d /etc/upstart ]; then
        _UPSTART_DIR=/etc/upstart
    elif [ -d /etc/init ]; then
        _UPSTART_DIR=/etc/init
    else
        fail "no Upstart configuration directory found"
    fi
    _NATIVE_CONF="$_UPSTART_DIR/kindle-dashboard-native.conf"

    ROOTFS_RW=1
    "$MNTROOT_BIN" rw || fail "could not make rootfs writable for Upstart install"
    cat > "$_NATIVE_CONF" <<'UPSTART'
start on started lab126
stop on stopping lab126

task
script
    if [ -f /mnt/us/dashboard/ENABLE_MXC_RTC_AUTOSTART ] && [ ! -f /mnt/us/dashboard/NOAUTOSTART ] && [ -x /mnt/us/dashboard/start.sh ]; then
        exec /mnt/us/dashboard/start.sh
    fi
end script
UPSTART
    "$MNTROOT_BIN" ro || fail "could not restore rootfs read-only"
    ROOTFS_RW=0
    log_msg "installed native scheduler Upstart job: $_NATIVE_CONF"
}

install_native() {
    [ -x "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" ] || fail "missing executable mxc-rtc-scheduler.sh"
    [ -x "$DASHBOARD_DIR/stop_legacy.sh" ] || fail "missing executable stop_legacy.sh"
    [ -x "$DASHBOARD_DIR/refresh-once.sh" ] || fail "missing executable refresh-once.sh"
    [ -x "$DASHBOARD_DIR/start.sh" ] || fail "missing executable start.sh"
    [ -x "$DASHBOARD_DIR/stop.sh" ] || fail "missing executable stop.sh"

    rm -f \
        "$DASHBOARD_DIR/mxc-rtc.activate" \
        "$DASHBOARD_DIR/mxc-rtc.active" \
        "$DASHBOARD_DIR/mxc-rtc.commit" \
        "$DASHBOARD_DIR/mxc-rtc.committed"
    stop_native_scheduler || true

    log_msg "Starting mxc-rtc-scheduler.sh in standby mode"
    "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >> "$DASHBOARD_DIR/mxc-rtc.log" 2>&1 &

    MXC_PID=""
    MXC_START=""
    COUNT=0
    while [ "$COUNT" -lt 5 ]; do
        "$SLEEP_BIN" 1
        if [ -f "$DASHBOARD_DIR/mxc-rtc-scheduler.pid" ]; then
            PID=$(cat "$DASHBOARD_DIR/mxc-rtc-scheduler.pid" 2>/dev/null || true)
            if [ -n "$PID" ] && [ -d "$PROC_DIR/$PID" ]; then
                STARTTIME=$(get_start_time "$PID" 2>/dev/null || true)
                if [ -n "$STARTTIME" ] && verify_identity "$PID" "$STARTTIME"; then
                    MXC_PID="$PID"
                    MXC_START="$STARTTIME"
                    break
                fi
            fi
        fi
        COUNT=$((COUNT + 1))
    done

    if [ -z "$MXC_PID" ]; then
        stop_native_scheduler || true
        fail "mxc standby startup or identity verification failed"
    fi

    log_msg "Standby identity verified (PID $MXC_PID, ST $MXC_START)"
    "$SLEEP_BIN" 3
    verify_identity "$MXC_PID" "$MXC_START" || {
        stop_native_scheduler || true
        fail "mxc standby identity lost during stability wait"
    }

    log_msg "Stopping legacy scheduler"
    if ! "$DASHBOARD_DIR/stop_legacy.sh"; then
        stop_native_scheduler || true
        fail "legacy shutdown failed; native scheduler not activated"
    fi
    if legacy_processes_present; then
        stop_native_scheduler || true
        fail "legacy processes remain after shutdown; native scheduler not activated"
    fi
    log_msg "Legacy shutdown verified"

    log_msg "Activating mxc scheduler"
    TOKEN="$MXC_PID $MXC_START"
    printf '%s\n' "$TOKEN" > "$DASHBOARD_DIR/mxc-rtc.activate.tmp"
    mv -f "$DASHBOARD_DIR/mxc-rtc.activate.tmp" "$DASHBOARD_DIR/mxc-rtc.activate"

    COUNT=0
    ACK=""
    while [ "$COUNT" -lt 5 ]; do
        "$SLEEP_BIN" 1
        ACK=$(cat "$DASHBOARD_DIR/mxc-rtc.active" 2>/dev/null || true)
        [ "$ACK" = "$TOKEN" ] && break
        COUNT=$((COUNT + 1))
    done

    if [ "$ACK" != "$TOKEN" ] || ! verify_identity "$MXC_PID" "$MXC_START"; then
        log_msg "Rollback: activation failed (no matching active ACK received)"
        rollback_to_legacy
        exit 1
    fi
    log_msg "Activation ACK verified"

    if ! install_native_upstart; then
        log_msg "Rollback: reboot persistence install failed"
        rollback_to_legacy
        exit 1
    fi
    : > "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART"
    log_msg "native mxc_rtc reboot persistence enabled"

    log_msg "Committing mxc scheduler cadence"
    printf '%s\n' "$TOKEN" > "$DASHBOARD_DIR/mxc-rtc.commit.tmp"
    mv -f "$DASHBOARD_DIR/mxc-rtc.commit.tmp" "$DASHBOARD_DIR/mxc-rtc.commit"

    COUNT=0
    COMMITTED=""
    while [ "$COUNT" -lt 5 ]; do
        "$SLEEP_BIN" 1
        COMMITTED=$(cat "$DASHBOARD_DIR/mxc-rtc.committed" 2>/dev/null || true)
        [ "$COMMITTED" = "$TOKEN" ] && break
        COUNT=$((COUNT + 1))
    done

    if [ "$COMMITTED" != "$TOKEN" ] || ! verify_identity "$MXC_PID" "$MXC_START"; then
        log_msg "Rollback: commit failed (no matching committed ACK received)"
        rollback_to_legacy
        exit 1
    fi

    log_msg "Activation successful"
}

ACTION="${1:-install}"
case "$ACTION" in
    install) install_native ;;
    *) fail "unknown action: $ACTION" ;;
esac

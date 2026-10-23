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

TX_STARTED=0
LEGACY_TOUCHED=0
CONTROL_LOCK_HELD=0
. "$DASHBOARD_DIR/mxc-rtc-common.sh"

finish() {
    _EXIT_RC=$?
    trap - EXIT HUP INT TERM
    if [ "$_EXIT_RC" -ne 0 ] && [ "$TX_STARTED" = 1 ]; then
        log_msg "Rollback: incomplete native handoff"
        if [ "$CONTROL_LOCK_HELD" = 1 ] || acquire_control_lock; then
            rollback_to_legacy || log_msg "ERROR: ROLLBACK FAILED; manual recovery required"
        else
            log_msg "ERROR: ROLLBACK FAILED; handoff lock busy; manual recovery required"
        fi
    fi
    restore_rootfs
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

stop_native_scheduler() {
    MXC_INTERNAL_STOP=1 "$DASHBOARD_DIR/stop.sh" || return 1
    ! native_processes_present
}

install_native_upstart() {
    if [ -n "${UPSTART_DIR:-}" ]; then
        _UPSTART_DIR="$UPSTART_DIR"
    elif [ -d /etc/upstart ]; then
        _UPSTART_DIR=/etc/upstart
    elif [ -d /etc/init ]; then
        _UPSTART_DIR=/etc/init
    else
        fail "no Upstart configuration directory found"
    fi
    _NATIVE_CONF="$_UPSTART_DIR/kindle-dashboard-native.conf"

    ROOTFS_RW=1
    "$MNTROOT_BIN" rw || fail "could not make rootfs writable for Upstart install"
    cat > "${_NATIVE_CONF}.tmp" <<'UPSTART' || fail "Upstart write failed"
start on started lab126
stop on stopping lab126

task
script
    # lab126 may start before the user partition is ready. Do not miss boot.
    count=0
    while [ ! -x /mnt/us/dashboard/start.sh ] && [ "$count" -lt 60 ]; do
        sleep 1
        count=$((count + 1))
    done
    if [ -f /mnt/us/dashboard/ENABLE_MXC_RTC_AUTOSTART ] && [ ! -f /mnt/us/dashboard/NOAUTOSTART ] && [ -x /mnt/us/dashboard/start.sh ]; then
        exec /mnt/us/dashboard/start.sh --supervise
    fi
end script
UPSTART
    mv -f "${_NATIVE_CONF}.tmp" "$_NATIVE_CONF" || fail "Upstart publish failed"
    sync || fail "Upstart sync failed"
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

    [ -x "$DASHBOARD_DIR/start-dashboard.sh" ] || fail "missing proven legacy startup path"
    command -v nohup >/dev/null 2>&1 || fail "nohup is required for recovery monitoring"
    acquire_control_lock || exit 1
    # Reinstall also displaces a cadence owner before legacy shutdown.
    if native_processes_present; then LEGACY_TOUCHED=1; fi
    stop_native_scheduler || fail "previous native scheduler could not be stopped"
    TX_STARTED=1
    rm -f "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART"

    rm -f \
        "$DASHBOARD_DIR/mxc-rtc.activate" \
        "$DASHBOARD_DIR/mxc-rtc.active" \
        "$DASHBOARD_DIR/mxc-rtc.commit" \
        "$DASHBOARD_DIR/mxc-rtc.committed"

    rm -f "$DASHBOARD_DIR/mxc-rtc.stop-requested"
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
        fail "mxc standby startup or identity verification failed"
    fi

    log_msg "Standby identity verified (PID $MXC_PID, ST $MXC_START)"
    "$SLEEP_BIN" 3
    verify_identity "$MXC_PID" "$MXC_START" || {
        fail "mxc standby identity lost during stability wait"
    }

    LEGACY_TOUCHED=1
    log_msg "Stopping legacy scheduler"
    if ! "$DASHBOARD_DIR/stop_legacy.sh"; then
        fail "legacy shutdown failed; native scheduler not activated"
    fi
    if legacy_processes_present; then
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
        exit 1
    fi
    log_msg "Activation ACK verified"

    if ! install_native_upstart; then
        log_msg "Rollback: reboot persistence install failed"
        exit 1
    fi
    printf '%s\n' mxc-rtc-v1 > "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART.tmp"
    mv -f "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART.tmp" "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART"
    sync || fail "persistence receipt sync failed"
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
        exit 1
    fi

    # Keep a process monitor in the initial install session too. start.sh binds
    # its readiness receipt to this native token and its own PID/starttime.
    rmdir "$MXC_CONTROL_LOCK"
    CONTROL_LOCK_HELD=0
    nohup "$DASHBOARD_DIR/start.sh" --supervise >> "$DASHBOARD_DIR/mxc-rtc.log" 2>&1 &
    MONITOR_PID=$!
    COUNT=0
    MONITOR_READY=0
    while [ "$COUNT" -lt 5 ]; do
        "$SLEEP_BIN" 1
        MONITOR_ACK=$(cat "$DASHBOARD_DIR/mxc-rtc.supervised" 2>/dev/null || true)
        set -- $MONITOR_ACK
        if [ "$#" = 4 ] && [ "$1 $2" = "$TOKEN" ] && [ "$3" = "$MONITOR_PID" ] && [ "$(get_start_time "$3" 2>/dev/null || true)" = "$4" ] && verify_identity "$MXC_PID" "$MXC_START"; then
            MONITOR_READY=1
            break
        fi
        COUNT=$((COUNT + 1))
    done
    [ "$MONITOR_READY" = 1 ] || fail "native recovery monitor did not acknowledge readiness"
    TX_STARTED=0
    log_msg "Activation successful"
}

ACTION="${1:-install}"
case "$ACTION" in
    install) install_native ;;
    *) fail "unknown action: $ACTION" ;;
esac

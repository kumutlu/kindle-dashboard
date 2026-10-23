#!/bin/sh
# Shared by the native PW1 scripts only; never sourced by KindleCron.
PROC_DIR="${PROC_DIR:-/proc}"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
MXC_CONTROL_LOCK="${MXC_CONTROL_LOCK:-/var/run/kindle-dashboard-mxc-control.lock}"
MXC_SCHEDULER_LOCK="${MXC_SCHEDULER_LOCK:-/var/run/kindle-dashboard-mxc-scheduler.lock}"

get_start_time() {
    case "$1" in ""|*[!0-9]*) return 1 ;; esac
    _SF="$PROC_DIR/$1/stat"
    [ -f "$_SF" ] || return 1
    _SL=$(cat "$_SF" 2>/dev/null) || return 1
    _REM="${_SL##*) }"
    set -- $_REM
    [ "$#" -ge 20 ] || return 1
    case "${20}" in
        ""|*[!0-9]*) return 1 ;;
        *) printf '%s\n' "${20}" ;;
    esac
}

native_processes_present() {
    for _CMD in "$PROC_DIR"/[0-9]*/cmdline; do
        [ -r "$_CMD" ] || continue
        tr '\000' '\n' < "$_CMD" 2>/dev/null | grep -Fx "$DASHBOARD_DIR/mxc-rtc-scheduler.sh" >/dev/null 2>&1 && return 0
    done
    return 1
}

legacy_processes_present() {
    for _CMD in "$PROC_DIR"/[0-9]*/cmdline; do
        [ -r "$_CMD" ] || continue
        tr '\000' '\n' < "$_CMD" 2>/dev/null | grep -Fx \
            -e "$DASHBOARD_DIR/refresh.sh" \
            -e "$DASHBOARD_DIR/watchdog.sh" \
            -e "$DASHBOARD_DIR/dashboard_loop.sh" >/dev/null 2>&1 && return 0
    done
    return 1
}

persistence_ready() {
    [ "$(cat "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART" 2>/dev/null || true)" = "mxc-rtc-v1" ]
}

# Abandoned locks fail closed. /var/run must be volatile on the target device;
# never reclaim a lock by guessing whether its PID belongs to an earlier boot.
acquire_control_lock() {
    if ! mkdir "$MXC_CONTROL_LOCK" 2>/dev/null; then
        log_msg "ERROR: native handoff lock is busy; no scheduler state changed"
        return 1
    fi
    CONTROL_LOCK_HELD=1
}

rollback_to_legacy() {
    _ROLLBACK_RC=0
    # Always stop native, including when disabling persistence cannot be saved.
    rm -f "$DASHBOARD_DIR/ENABLE_MXC_RTC_AUTOSTART" || _ROLLBACK_RC=1
    sync || _ROLLBACK_RC=1
    if ! MXC_INTERNAL_STOP=1 "$DASHBOARD_DIR/stop.sh"; then
        log_msg "ERROR: native shutdown failed; refusing to start legacy"
        return 1
    fi
    if native_processes_present; then
        log_msg "ERROR: native process remains; refusing to start legacy"
        return 1
    fi
    [ "${LEGACY_TOUCHED:-1}" = 1 ] || return "$_ROLLBACK_RC"
    if ! "$DASHBOARD_DIR/stop_legacy.sh" || legacy_processes_present; then
        log_msg "ERROR: partial legacy shutdown failed during rollback"
        return 1
    fi
    if ! "$DASHBOARD_DIR/start-dashboard.sh"; then
        log_msg "ERROR: legacy startup command failed"
        return 1
    fi
    _RECOVERY_COUNT=0
    while [ "$_RECOVERY_COUNT" -lt 5 ]; do
        if legacy_processes_present; then
            log_msg "Legacy rollback verified"
            return "$_ROLLBACK_RC"
        fi
        "$SLEEP_BIN" 1
        _RECOVERY_COUNT=$((_RECOVERY_COUNT + 1))
    done
    log_msg "ERROR: legacy startup returned without a cadence owner"
    return 1
}

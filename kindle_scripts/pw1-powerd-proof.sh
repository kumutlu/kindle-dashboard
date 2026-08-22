#!/bin/sh
set -eu

INTERVAL="${1:-120}"
LOG_FILE="/tmp/pw1-powerd-proof.log"

log() {
	MSG="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$MSG"
	echo "$MSG" >> "$LOG_FILE"
}

log "=== PW1 Powerd Event Proof Test (Interval: ${INTERVAL}s) ==="
log "Initial state: powerd state=$(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown)"
log "Initial wifi: cmState=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)"
log "Initial uptime: $(uptime)"
log "Initial battery: $(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo unknown)%"

# Unset preventScreenSaver so powerd can transition to screenSaver / readyToSuspend
log "Releasing preventScreenSaver..."
lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null || true

# Start listening for readyToSuspend event in background or subshell before triggering powerButton
log "Triggering powerButton suspend..."
lipc-set-prop com.lab126.powerd powerButton 1 2>/dev/null || true

log "Waiting for readyToSuspend event (timeout 30s)..."
EVENT=$(lipc-wait-event -s 30 com.lab126.powerd readyToSuspend 2>/dev/null || echo "timeout")
log "Event received: '$EVENT'"

CURRENT_STATE=$(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo "unknown")
log "Current powerd state after event: $CURRENT_STATE"

log "Setting rtcWakeup to $INTERVAL seconds..."
if lipc-set-prop -i com.lab126.powerd rtcWakeup "$INTERVAL" 2>/dev/null; then
	log "SUCCESS: rtcWakeup set to $INTERVAL"
else
	log "ERROR: lipc-set-prop rtcWakeup failed"
fi

BEFORE_SUSPEND_TIME=$(date +%s)
log "Entering suspend now at epoch $BEFORE_SUSPEND_TIME..."

# Wait for system to wake up
/bin/sleep 5

# System should be suspended here. Execution pauses until hardware RTC wakes system.

AFTER_WAKE_TIME=$(date +%s)
ELAPSED=$((AFTER_WAKE_TIME - BEFORE_SUSPEND_TIME))
log "=== RESUMED ==="
log "Resumed at epoch $AFTER_WAKE_TIME (elapsed: ${ELAPSED}s)"
log "Post-resume state: powerd state=$(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown)"
log "Post-resume wifi: cmState=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)"
log "Post-resume uptime: $(uptime)"

# Re-assert preventScreenSaver
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null || true
log "Test complete."

#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
LOG_FILE="$DASHBOARD_DIR/rollback.log"

log() {
	MSG="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$MSG"
	echo "$MSG" >> "$LOG_FILE" 2>/dev/null || true
}

log "=== Initiating PW1 Legacy Rollback ==="

# Set disable safety marker immediately
touch "$DASHBOARD_DIR/DISABLE_LOW_POWER"

# Kill all experimental low-power processes
pkill -f "pw1-low-power-cycle.sh" 2>/dev/null || true
pkill -f "pw1-powerd-proof.sh" 2>/dev/null || true
pkill -f "mxc-rtc" 2>/dev/null || true
rm -f /tmp/pw1-dashboard.pid /tmp/pw1-dashboard.lock /tmp/kindle-refresh.lock 2>/dev/null || true

# Re-enable preventScreenSaver for legacy mode
if command -v lipc-set-prop >/dev/null 2>&1; then
	lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null || true
fi

# Ensure device.env is set to legacy
if [ -f "$DASHBOARD_DIR/device.env" ]; then
	sed -i 's/SCHEDULER_BACKEND=.*/SCHEDULER_BACKEND="legacy"/' "$DASHBOARD_DIR/device.env" 2>/dev/null || true
fi

# Remove any extra upstart experimental configs
rm -f /etc/upstart/dashboard.conf /etc/upstart/default-kindle-low-power.conf 2>/dev/null || true

# Remove safety marker
rm -f "$DASHBOARD_DIR/DISABLE_LOW_POWER" "$DASHBOARD_DIR/NOAUTOSTART" 2>/dev/null || true

# Ensure exactly one legacy refresh.sh process is started if none running
if ! pgrep -f "/mnt/us/dashboard/refresh.sh" >/dev/null 2>&1; then
	if [ -x "$DASHBOARD_DIR/start-dashboard.sh" ]; then
		log "starting legacy start-dashboard.sh..."
		"$DASHBOARD_DIR/start-dashboard.sh" >/dev/null 2>&1 &
	elif [ -x "$DASHBOARD_DIR/refresh.sh" ]; then
		log "starting legacy refresh.sh..."
		"$DASHBOARD_DIR/refresh.sh" >/dev/null 2>&1 &
	fi
fi

/bin/sleep 2

LEGACY_PID=$(pgrep -f "/mnt/us/dashboard/refresh.sh" 2>/dev/null || echo "")
log "Rollback complete. Active legacy refresh.sh PID: '${LEGACY_PID:-none}'"

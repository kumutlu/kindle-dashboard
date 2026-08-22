#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ENV_FILE="$DASHBOARD_DIR/device.env"
PID_FILE="/tmp/pw1-dashboard.pid"
LOCK_FILE="/tmp/pw1-dashboard.lock"
LOG_FILE="$DASHBOARD_DIR/pw1-dashboard.log"
REFRESH_ONCE_SH="$DASHBOARD_DIR/refresh-once.sh"

log() {
	MSG="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$MSG"
	echo "$MSG" >> "$LOG_FILE" 2>/dev/null || true
	if [ -f "$LOG_FILE" ]; then
		SIZE=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
		if [ "$SIZE" -gt 200000 ]; then
			tail -n 500 "$LOG_FILE" > "${LOG_FILE}.tmp" 2>/dev/null && mv -f "${LOG_FILE}.tmp" "$LOG_FILE"
		fi
	fi
}

# Process lock check
if [ -f "$PID_FILE" ]; then
	OLD_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
	if [ -n "$OLD_PID" ] && [ "$OLD_PID" != "$$" ] && kill -0 "$OLD_PID" 2>/dev/null; then
		log "another pw1-dashboard process ($OLD_PID) is running, exiting"
		exit 0
	fi
fi
echo $$ > "$PID_FILE"

cleanup() {
	if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ]; then
		rm -f "$PID_FILE"
	fi
	rm -f "$LOCK_FILE" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

log "=== Starting PW1 Low-Power Dashboard Cadence (PID $$) ==="

MODE="${1:-loop}"

run_one_cycle() {
	OVERRIDE_INTERVAL="${REFRESH_INTERVAL_MINUTES:-}"

	if [ -f "$DEVICE_ENV_FILE" ]; then
		. "$DEVICE_ENV_FILE"
	fi

	if [ -n "$OVERRIDE_INTERVAL" ]; then
		REFRESH_INTERVAL_MINUTES="$OVERRIDE_INTERVAL"
	fi

	if [ -f "$DASHBOARD_DIR/DISABLE_LOW_POWER" ] || [ -f "$DASHBOARD_DIR/NOAUTOSTART" ]; then
		log "safety marker present (DISABLE_LOW_POWER/NOAUTOSTART), skipping cycle"
		return 2
	fi

	SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
	case "$SLEEP_MINUTES" in
		1|2|3|5|10|15|30|60) ;;
		*) SLEEP_MINUTES=60 ;;
	esac
	INTERVAL_SECONDS=$((SLEEP_MINUTES * 60))

	# Ensure legacy refresh.sh is not running concurrently
	if pgrep -f "/mnt/us/dashboard/refresh.sh" >/dev/null 2>&1; then
		log "stopping legacy refresh.sh loop..."
		pkill -f "/mnt/us/dashboard/refresh.sh" 2>/dev/null || true
		/bin/sleep 1
	fi

	log "executing refresh cycle..."
	REFRESH_RC=0
	if [ -x "$REFRESH_ONCE_SH" ]; then
		"$REFRESH_ONCE_SH" || REFRESH_RC=$?
	else
		log "ERROR: $REFRESH_ONCE_SH is missing or not executable"
		return 1
	fi

	if [ "$REFRESH_RC" -ne 0 ]; then
		log "refresh cycle failed with rc=$REFRESH_RC"
	else
		log "refresh cycle completed successfully"
	fi

	# Post-display settle period
	log "post-display settle (4s)..."
	/bin/sleep 4

	if [ "$MODE" = "--once" ]; then
		log "single run requested (--once), exiting without suspend"
		return 0
	fi

	# Initiate low-power stock powerd suspend sequence
	log "initiating stock powerd suspend sequence for interval ${INTERVAL_SECONDS}s..."
	lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null || true

	log "triggering powerButton suspend..."
	lipc-set-prop com.lab126.powerd powerButton 1 2>/dev/null || true

	log "waiting for readyToSuspend event (timeout 30s)..."
	EVENT=$(lipc-wait-event -s 30 com.lab126.powerd readyToSuspend 2>/dev/null || echo "timeout")
	log "powerd event result: '$EVENT'"

	STATE=$(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo "unknown")
	log "powerd state after event: $STATE"

	POWERD_SUSPENDED=0
	if [ "$EVENT" = "readyToSuspend" ] || [ "$STATE" = "readyToSuspend" ] || [ "$STATE" = "screenSaver" ]; then
		log "setting rtcWakeup=$INTERVAL_SECONDS via powerd..."
		if lipc-set-prop -i com.lab126.powerd rtcWakeup "$INTERVAL_SECONDS" 2>/dev/null; then
			log "powerd rtcWakeup set successfully: ${INTERVAL_SECONDS}s"
			POWERD_SUSPENDED=1
		else
			log "WARNING: rtcWakeup lipc set call failed"
		fi
	fi

	BEFORE_EPOCH=$(date +%s)

	if [ "$POWERD_SUSPENDED" -eq 1 ]; then
		log "entering stock powerd kernel suspend at epoch $BEFORE_EPOCH..."
		/bin/sleep 5
	else
		log "powerd event timed out; setting sysfs rtc1 wakealarm target $((BEFORE_EPOCH + INTERVAL_SECONDS))..."
		RTC_DIR="/sys/class/rtc/rtc1"
		if [ ! -d "$RTC_DIR" ]; then
			RTC_DIR="/sys/class/rtc/rtc0"
		fi
		echo 1 > /sys/devices/platform/mxc_rtc.0/wakeup_enable 2>/dev/null || true
		NOW=$(cat "$RTC_DIR/since_epoch" 2>/dev/null || date +%s)
		TARGET=$((NOW + INTERVAL_SECONDS))
		echo 0 > "$RTC_DIR/wakealarm" 2>/dev/null || true
		echo "$TARGET" > "$RTC_DIR/wakealarm" 2>/dev/null || true
		log "entering direct kernel mem suspend at epoch $BEFORE_EPOCH..."
		echo mem > /sys/power/state 2>/dev/null || true
	fi

	AFTER_EPOCH=$(date +%s)
	ELAPSED=$((AFTER_EPOCH - BEFORE_EPOCH))
	log "=== RESUMED FROM SUSPEND ==="
	log "resumed at epoch $AFTER_EPOCH (elapsed time: ${ELAPSED}s)"
	log "post-resume powerd state: $(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown)"
	log "post-resume wifid cmState: $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)"

	# Ensure Wi-Fi is enabled if powerd didn't automatically reconnect
	WIFI_CURR=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null || echo unknown)
	if [ "$WIFI_CURR" != "CONNECTED" ]; then
		log "requesting wifid enable post-resume..."
		lipc-set-prop com.lab126.wifid enable 1 2>/dev/null || true
	fi

	return 0
}

if [ "$MODE" = "--once" ]; then
	run_one_cycle
	exit 0
fi

CYCLES_RUN=0
while true; do
	CYCLES_RUN=$((CYCLES_RUN + 1))
	log "--- Starting cycle iteration $CYCLES_RUN ---"
	run_one_cycle || {
		RC=$?
		if [ "$RC" -eq 2 ]; then
			log "exiting cadence due to safety marker"
			exit 0
		fi
		log "cycle error (rc=$RC), sleeping 60s before retry"
		/bin/sleep 60
	}
	MAX_CYCLES="${MAX_CYCLES:-0}"
	if [ "$MAX_CYCLES" -gt 0 ] && [ "$CYCLES_RUN" -ge "$MAX_CYCLES" ]; then
		log "completed requested $CYCLES_RUN cycle(s) (MAX_CYCLES=$MAX_CYCLES), exiting"
		break
	fi
done

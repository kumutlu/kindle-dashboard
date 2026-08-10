#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ENV_FILE="$DASHBOARD_DIR/device.env"
PID_FILE="$DASHBOARD_DIR/dashboard_loop.pid"
REFRESH_ONCE_SH="$DASHBOARD_DIR/refresh-once.sh"
PROC_DIR="${PROC_DIR:-/proc}"
RTC_SYS_DIR="${RTC_SYS_DIR:-/sys/class/rtc/rtc1}"
LOG_FILE="${DASHBOARD_DIR}/dashboard.log"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"

log_msg() {
	MSG="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$MSG"
	echo "$MSG" >> "$LOG_FILE"
	if [ -f "$LOG_FILE" ]; then
		SIZE=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
		if [ "$SIZE" -gt 100000 ]; then
			tail -n 500 "$LOG_FILE" > "${LOG_FILE}.tmp" 2>/dev/null && mv -f "${LOG_FILE}.tmp" "$LOG_FILE"
		fi
	fi
}
KILL_CMD="${KILL_CMD:-kill}"

# Single-instance protection with stale PID command verification
if [ -f "$PID_FILE" ]; then
	OLD_LPID=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$OLD_LPID" ] && [ "$OLD_LPID" != "$$" ] && "$KILL_CMD" -0 "$OLD_LPID" 2>/dev/null; then
		OLD_CMDLINE=$(cat "$PROC_DIR/$OLD_LPID/cmdline" 2>/dev/null | tr '\0\n\r' '   ')
		PAD_CMDLINE=" $OLD_CMDLINE "
		case "$PAD_CMDLINE" in
			*" $DASHBOARD_DIR/dashboard_loop.sh "*|*" /mnt/us/dashboard/dashboard_loop.sh "*|*" $DASHBOARD_DIR/refresh.sh "*|*" /mnt/us/dashboard/refresh.sh "*)
				exit 0
				;;
		esac
	fi
fi
echo $$ > "$PID_FILE"

cleanup() {
	if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ]; then
		rm -f "$PID_FILE"
	fi
}
trap cleanup EXIT
trap "cleanup; exit 0" HUP INT TERM

while true
do
	if [ -f "$DEVICE_ENV_FILE" ]; then
		. "$DEVICE_ENV_FILE"
	fi

	if [ "${LOW_POWER_MODE:-0}" -eq 1 ]; then
		log_msg "[low-power] refresh started"
		if [ ! -x "$REFRESH_ONCE_SH" ]; then
			log_msg "[low-power] ERROR: missing $REFRESH_ONCE_SH"
		else
			"$REFRESH_ONCE_SH" || true
		fi
		log_msg "[low-power] refresh completed"

		SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
		case "$SLEEP_MINUTES" in
			5|10|15|30|60) ;;
			*) SLEEP_MINUTES=60 ;;
		esac
		INTERVAL=$((SLEEP_MINUTES * 60))
		log_msg "[low-power] selected interval: ${INTERVAL}s"

		# RTC wakealarm validation and setup
		RTC_DIR="${RTC_SYS_DIR:-/sys/class/rtc/rtc1}"
		if [ ! -d "$RTC_DIR" ] || [ ! -r "$RTC_DIR/since_epoch" ] || [ ! -w "$RTC_DIR/wakealarm" ]; then
			log_msg "[low-power] fallback reason: rtc1 missing or permissions invalid"
			"$SLEEP_BIN" "$INTERVAL"
			continue
		fi

		if ! echo 0 > "$RTC_DIR/wakealarm" 2>/dev/null; then
			log_msg "[low-power] fallback reason: failed to clear existing wakealarm"
			"$SLEEP_BIN" "$INTERVAL"
			continue
		fi

		NOW=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')
		case "$NOW" in
			""|*[!0-9]*)
				log_msg "[low-power] fallback reason: invalid current epoch time: $NOW"
				"$SLEEP_BIN" "$INTERVAL"
				continue
				;;
		esac
		log_msg "[low-power] RTC current time: $NOW"

		TARGET=$((NOW + INTERVAL))
		log_msg "[low-power] requested alarm: $TARGET"

		if [ "$TARGET" -le "$NOW" ]; then
			log_msg "[low-power] fallback reason: alarm not in the future (requested $TARGET <= current $NOW)"
			"$SLEEP_BIN" "$INTERVAL"
			continue
		fi

		if ! echo "$TARGET" > "$RTC_DIR/wakealarm" 2>/dev/null; then
			log_msg "[low-power] fallback reason: wakealarm write failure"
			"$SLEEP_BIN" "$INTERVAL"
			continue
		fi

		ACTUAL=$(cat "$RTC_DIR/wakealarm" 2>/dev/null | tr -d '\r\n')
		if [ "$ACTUAL" != "$TARGET" ]; then
			log_msg "[low-power] fallback reason: wakealarm verification mismatch (expected $TARGET, got $ACTUAL)"
			"$SLEEP_BIN" "$INTERVAL"
			continue
		fi
		log_msg "[low-power] verified alarm: $ACTUAL"

		sync

		log_msg "[low-power] suspend attempt"
		BEFORE=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')
		POWER_STATE_FILE="${POWER_STATE_FILE:-/sys/power/state}"
		if echo mem > "$POWER_STATE_FILE" 2>/dev/null; then
			AFTER=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')
			case "$AFTER" in
				""|*[!0-9]*)
					log_msg "[low-power] warning: invalid post-resume RTC time: $AFTER"
					"$SLEEP_BIN" 10
					;;
				*)
					SLEPT=$((AFTER - BEFORE))
					if [ "$SLEPT" -lt 0 ] 2>/dev/null || [ "$SLEPT" -lt 10 ] 2>/dev/null; then
						log_msg "[low-power] warning: unexpectedly short sleep duration: ${SLEPT}s"
						"$SLEEP_BIN" 10
					else
						log_msg "[low-power] resume detected (slept ${SLEPT}s)"
					fi
					;;
			esac
		else
			log_msg "[low-power] fallback reason: suspend command failed"
			"$SLEEP_BIN" "$INTERVAL"
		fi
		continue
	fi

	# Normal Mode Flow
	if [ ! -x "$REFRESH_ONCE_SH" ]; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: missing $REFRESH_ONCE_SH" >&2
		exit 1
	fi
	if ! "$REFRESH_ONCE_SH"; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: refresh cycle failed" >&2
		exit 1
	fi

	SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
	case "$SLEEP_MINUTES" in
		5|10|15|30|60) ;;
		*) SLEEP_MINUTES=60 ;;
	esac
	SLEEP_SECONDS=$((SLEEP_MINUTES * 60))
	echo "$(date '+%Y-%m-%d %H:%M:%S') next wake cycle in ${SLEEP_MINUTES} minutes"
	"$SLEEP_BIN" "$SLEEP_SECONDS"
done

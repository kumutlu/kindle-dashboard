#!/bin/sh

# Legacy Fallbacks
LEGACY_LOCAL_URL="http://192.168.68.167:8765/weather.png"
LEGACY_CONFIG_URL="http://192.168.68.167:8767/api/config"

SERVER_HOST="${SERVER_HOST:-192.168.68.167}"
DEVICE_ID="${DEVICE_ID:-default-kindle}"
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ID_FILE="$DASHBOARD_DIR/device-id"

if [ -s "$DEVICE_ID_FILE" ] && [ "$DEVICE_ID" = "default-kindle" ]; then
	CANDIDATE=$(sed -n '1p' "$DEVICE_ID_FILE" | tr -d '\r\n')
	case "$CANDIDATE" in
		*[!a-zA-Z0-9_-]*|"") ;;
		*) DEVICE_ID="$CANDIDATE" ;;
	esac
fi

LOCAL_URL="http://$SERVER_HOST:8765/device/$DEVICE_ID/image.png"
PUBLIC_URL="https://user-zbox-ci320nano-series.taildabdfd.ts.net/weather.png"
CONFIG_URL="http://$SERVER_HOST:8767/api/device/$DEVICE_ID/config"
# TODO: Support device-qualified public endpoints when implemented on public server

TOKEN_FILE="$DASHBOARD_DIR/public-token"
IMG="$DASHBOARD_DIR/weather.png"
TMP="$DASHBOARD_DIR/weather.tmp"
LOCK_FILE="${LOCK_FILE:-/tmp/kindle-refresh.lock}"
STATUS_SENDER="${STATUS_SENDER:-$(dirname "$0")/send-status.sh}"
EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"

PROC_DIR="${PROC_DIR:-/proc}"
RTC_SYS_DIR="${RTC_SYS_DIR:-/sys/class/rtc/rtc1}"
LOG_FILE="${DASHBOARD_DIR:-/mnt/us/dashboard}/dashboard.log"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
KILL_CMD="${KILL_CMD:-kill}"

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

cleanup() {
	if [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$$" ]; then
		rm -f "$LOCK_FILE"
	fi
	LOOP_PID_FILE="${DASHBOARD_DIR:-/mnt/us/dashboard}/dashboard_loop.pid"
	if [ -f "$LOOP_PID_FILE" ] && [ "$(cat "$LOOP_PID_FILE" 2>/dev/null)" = "$$" ]; then
		rm -f "$LOOP_PID_FILE"
	fi
}
trap cleanup EXIT HUP INT TERM

# Single-instance protection with stale PID command verification
if [ -f "$LOCK_FILE" ]; then
	OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
	if [ -n "$OLD_PID" ]; then
		if "$KILL_CMD" -0 "$OLD_PID" 2>/dev/null; then
			OLD_CMDLINE=$(cat "$PROC_DIR/$OLD_PID/cmdline" 2>/dev/null | tr '\0\n\r' '   ')
			PAD_CMDLINE=" $OLD_CMDLINE "
			case "$PAD_CMDLINE" in
				*" $DASHBOARD_DIR/refresh.sh "*|*" /mnt/us/dashboard/refresh.sh "*)
					echo "$(date '+%Y-%m-%d %H:%M:%S') another active refresh process ($OLD_PID) is running, exiting"
					exit 0
					;;
				*)
					echo "$(date '+%Y-%m-%d %H:%M:%S') removing stale lock file for PID $OLD_PID"
					rm -f "$LOCK_FILE"
					;;
			esac
		else
			# Stale PID, safe to ignore and overwrite
			echo "$(date '+%Y-%m-%d %H:%M:%S') removing stale lock file for PID $OLD_PID"
			rm -f "$LOCK_FILE"
		fi
	fi
fi
echo $$ > "$LOCK_FILE"

INTERVAL=600

while true
do
	# Source env
	if [ -f "$DASHBOARD_DIR/device.env" ]; then
		. "$DASHBOARD_DIR/device.env"
	fi

	if [ "${LOW_POWER_MODE:-0}" -eq 1 ]; then
		log_msg "[low-power] refresh started"
		ONCE_SCRIPT="$(dirname "$0")/refresh-once.sh"
		if [ -x "$ONCE_SCRIPT" ]; then
			sh "$ONCE_SCRIPT"
		else
			log_msg "[low-power] fallback reason: refresh-once.sh not found/executable at $ONCE_SCRIPT"
		fi
		log_msg "[low-power] refresh completed"

		# Determine refresh interval
		CONFIG_JSON=$(wget -q -O- "$CONFIG_URL" 2>/dev/null)
		if [ -z "$CONFIG_JSON" ]; then
			CONFIG_JSON=$(wget -q -O- "$LEGACY_CONFIG_URL" 2>/dev/null)
		fi
		REFRESH_MINS=$(echo "$CONFIG_JSON" | grep -o '"refresh_interval_minutes":\s*[0-9][0-9]*' | grep -o '[0-9][0-9]*')
		if [ -n "$REFRESH_MINS" ] && { [ "$REFRESH_MINS" -eq 5 ] || [ "$REFRESH_MINS" -eq 10 ] || [ "$REFRESH_MINS" -eq 15 ] || [ "$REFRESH_MINS" -eq 30 ] || [ "$REFRESH_MINS" -eq 60 ]; }; then
			INTERVAL=$((REFRESH_MINS * 60))
		elif [ -n "${REFRESH_INTERVAL_MINUTES:-}" ]; then
			INTERVAL=$((REFRESH_INTERVAL_MINUTES * 60))
		else
			INTERVAL=600
		fi
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

	HOUR=$(date +%H)
	HR=${HOUR#0}
	HR=${HR:-0}
	if [ "$HR" -ge 23 ] || [ "$HR" -lt 7 ]; then
		FALLBACK_LIGHT=1
	else
		FALLBACK_LIGHT=8
	fi


	# Get config json directly (local LAN request is fast)
	CONFIG_JSON=$(wget -q -O- "$CONFIG_URL" 2>/dev/null)
	if [ -z "$CONFIG_JSON" ]; then
		CONFIG_JSON=$(wget -q -O- "$LEGACY_CONFIG_URL" 2>/dev/null)
	fi

	LIGHT=$(echo "$CONFIG_JSON" | grep -o '"kindle_frontlight":\s*[0-9][0-9]*' | grep -o '[0-9][0-9]*')
	if [ -n "$LIGHT" ] && { [ "$LIGHT" -eq 0 ] || [ "$LIGHT" -eq 1 ] || [ "$LIGHT" -eq 4 ] || [ "$LIGHT" -eq 8 ] || [ "$LIGHT" -eq 12 ] || [ "$LIGHT" -eq 18 ]; }; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') frontlight: $LIGHT"
		lipc-set-prop com.lab126.powerd flIntensity "$LIGHT" 2>/dev/null
	else
		echo "$(date '+%Y-%m-%d %H:%M:%S') config unavailable, using fallback frontlight $FALLBACK_LIGHT"
		lipc-set-prop com.lab126.powerd flIntensity "$FALLBACK_LIGHT" 2>/dev/null
	fi

	lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null

	SOURCE=""
	rm -f "$TMP"

	# Download local image
	if wget -q -O "$TMP" "$LOCAL_URL" 2>/dev/null && [ -s "$TMP" ]; then
		SOURCE="local"
	elif wget -q -O "$TMP" "$LEGACY_LOCAL_URL" 2>/dev/null && [ -s "$TMP" ]; then
		SOURCE="local"
	else
		rm -f "$TMP"

		if [ -s "$TOKEN_FILE" ]; then
			TOKEN=$(sed -n '1p' "$TOKEN_FILE")

			if [ -n "$TOKEN" ] &&
				/mnt/us/usbnet/bin/curl -fsS \
					--connect-timeout 15 \
					--max-time 45 \
					-H "Authorization: Bearer $TOKEN" \
					-o "$TMP" \
					"$PUBLIC_URL" 2>/dev/null &&
				[ -s "$TMP" ]; then
				SOURCE="public"
			fi

			unset TOKEN
		fi
	fi

	if [ -n "$SOURCE" ]; then
		mv -f "$TMP" "$IMG"
		echo "$(date '+%Y-%m-%d %H:%M:%S') image updated via $SOURCE"
	else
		rm -f "$TMP"
		echo "$(date '+%Y-%m-%d %H:%M:%S') download failed; using previous image"
	fi

	DISPLAY_OK=0
	if [ -s "$IMG" ]; then
		if ! "$EIPS_BIN" -c; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -c)"
		elif ! "$EIPS_BIN" -f; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -f)"
		elif ! "$EIPS_BIN" -g "$IMG"; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -g)"
		else
			DISPLAY_OK=1
		fi
	fi

	if [ "$DISPLAY_OK" -eq 1 ] && [ -f "$STATUS_SENDER" ]; then
		SERVER_HOST="$SERVER_HOST" DEVICE_ID="$DEVICE_ID" DASHBOARD_DIR="$DASHBOARD_DIR" sh "$STATUS_SENDER" >/dev/null 2>&1 || true
	fi


	REFRESH_MINS=$(echo "$CONFIG_JSON" | grep -o '"refresh_interval_minutes":\s*[0-9][0-9]*' | grep -o '[0-9][0-9]*')
	if [ -n "$REFRESH_MINS" ] && { [ "$REFRESH_MINS" -eq 5 ] || [ "$REFRESH_MINS" -eq 10 ] || [ "$REFRESH_MINS" -eq 15 ] || [ "$REFRESH_MINS" -eq 30 ] || [ "$REFRESH_MINS" -eq 60 ]; }; then
		INTERVAL=$((REFRESH_MINS * 60))
		echo "$(date '+%Y-%m-%d %H:%M:%S') refresh interval: $REFRESH_MINS min / $INTERVAL sec"
	else
		INTERVAL=600
		echo "$(date '+%Y-%m-%d %H:%M:%S') config unavailable, using fallback refresh interval 600 sec"
	fi

	# Call the external /bin/sleep binary to avoid shell built-in process forking
	"$SLEEP_BIN" "$INTERVAL"
done

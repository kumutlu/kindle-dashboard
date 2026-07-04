#!/bin/sh

LOCAL_URL="http://192.168.68.167:8765/weather.png"
PUBLIC_URL="https://user-zbox-ci320nano-series.taildabdfd.ts.net/weather.png"
CONFIG_URL="http://192.168.68.167:8767/api/config"
TOKEN_FILE="/mnt/us/dashboard/public-token"
IMG="/mnt/us/dashboard/weather.png"
TMP="/mnt/us/dashboard/weather.tmp"
LOCK_FILE="/tmp/kindle-refresh.lock"

# POSIX sh compatible timeout helper
timeout_cmd() {
	TIMEOUT_SEC=$1
	shift
	"$@" &
	CMD_PID=$!
	(
		trap 'kill -9 $sp 2>/dev/null' EXIT
		sleep "$TIMEOUT_SEC" &
		sp=$!
		wait "$sp" 2>/dev/null
		kill -0 "$CMD_PID" 2>/dev/null && kill -9 "$CMD_PID" 2>/dev/null
	) &
	TIMER_PID=$!
	wait "$CMD_PID" 2>/dev/null
	EXIT_CODE=$?
	kill -0 "$TIMER_PID" 2>/dev/null && kill -9 "$TIMER_PID" 2>/dev/null
	wait "$TIMER_PID" 2>/dev/null
	return $EXIT_CODE
}

cleanup() {
	rm -f "$LOCK_FILE"
}
trap cleanup EXIT HUP INT TERM

INTERVAL=600

while true
do
	HOUR=$(date +%H)
	HR=${HOUR#0}
	HR=${HR:-0}
	if [ "$HR" -ge 23 ] || [ "$HR" -lt 7 ]; then
		FALLBACK_LIGHT=1
	else
		FALLBACK_LIGHT=8
	fi

	# Acquire lock for active refresh
	if [ -f "$LOCK_FILE" ]; then
		OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
		if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') another active refresh process ($OLD_PID) is running, skipping cycle"
			sleep "$INTERVAL"
			continue
		fi
	fi
	echo $$ > "$LOCK_FILE"

	CONFIG_JSON=$(timeout_cmd 5 wget -q -O- "$CONFIG_URL")

	LIGHT=$(echo "$CONFIG_JSON" | grep -o '"kindle_frontlight":\s*[0-9][0-9]*' | grep -o '[0-9][0-9]*')
	if [ -n "$LIGHT" ] && { [ "$LIGHT" -eq 0 ] || [ "$LIGHT" -eq 1 ] || [ "$LIGHT" -eq 4 ] || [ "$LIGHT" -eq 8 ] || [ "$LIGHT" -eq 12 ] || [ "$LIGHT" -eq 18 ]; }; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') frontlight: $LIGHT"
		timeout_cmd 5 lipc-set-prop com.lab126.powerd flIntensity "$LIGHT" 2>/dev/null
	else
		echo "$(date '+%Y-%m-%d %H:%M:%S') config unavailable, using fallback frontlight $FALLBACK_LIGHT"
		timeout_cmd 5 lipc-set-prop com.lab126.powerd flIntensity "$FALLBACK_LIGHT" 2>/dev/null
	fi

	timeout_cmd 5 lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null

	SOURCE=""
	rm -f "$TMP"

	if timeout_cmd 20 wget -q -O "$TMP" "$LOCAL_URL" && [ -s "$TMP" ]; then
		SOURCE="local"
	else
		rm -f "$TMP"

		if [ -s "$TOKEN_FILE" ]; then
			TOKEN=$(sed -n '1p' "$TOKEN_FILE")

			if [ -n "$TOKEN" ] &&
				timeout_cmd 50 /mnt/us/usbnet/bin/curl -fsS \
					--connect-timeout 15 \
					--max-time 45 \
					-H "Authorization: Bearer $TOKEN" \
					-o "$TMP" \
					"$PUBLIC_URL" &&
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

	if [ -s "$IMG" ]; then
		if ! timeout_cmd 15 /usr/sbin/eips -c; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -c)"
		elif ! timeout_cmd 15 /usr/sbin/eips -f; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -f)"
		elif ! timeout_cmd 20 /usr/sbin/eips -g "$IMG"; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -g)"
		fi
	fi

	# Release lock
	rm -f "$LOCK_FILE"

	REFRESH_MINS=$(echo "$CONFIG_JSON" | grep -o '"refresh_interval_minutes":\s*[0-9][0-9]*' | grep -o '[0-9][0-9]*')
	if [ -n "$REFRESH_MINS" ] && { [ "$REFRESH_MINS" -eq 5 ] || [ "$REFRESH_MINS" -eq 10 ] || [ "$REFRESH_MINS" -eq 15 ] || [ "$REFRESH_MINS" -eq 30 ] || [ "$REFRESH_MINS" -eq 60 ]; }; then
		INTERVAL=$((REFRESH_MINS * 60))
		echo "$(date '+%Y-%m-%d %H:%M:%S') refresh interval: $REFRESH_MINS min / $INTERVAL sec"
	else
		INTERVAL=600
		echo "$(date '+%Y-%m-%d %H:%M:%S') config unavailable, using fallback refresh interval 600 sec"
	fi

	sleep "$INTERVAL"
done

#!/bin/sh

# Legacy Fallbacks
LEGACY_LOCAL_URL="http://192.168.68.167:8765/weather.png"
LEGACY_CONFIG_URL="http://192.168.68.167:8767/api/config"

SERVER_HOST="192.168.68.167"
DEVICE_ID="default-kindle"
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ID_FILE="$DASHBOARD_DIR/device-id"

if [ -s "$DEVICE_ID_FILE" ]; then
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
LOCK_FILE="/tmp/kindle-refresh.lock"

# Note: On Kindle BusyBox ash, using custom background watchdogs, command evaluations,
# or even the shell built-in 'sleep' command forces the shell to fork duplicate child
# processes named 'refresh.sh' that pollute the process table.
# To keep exactly one 'refresh.sh' daemon process active:
#   1. We do not use any custom timeout wrapper or subshell.
#   2. We run all utilities (lipc, eips, wget, curl) directly in the foreground.
#   3. For curl, we rely on its native --connect-timeout and --max-time flags.
#   4. We execute the external '/bin/sleep' binary instead of the built-in 'sleep'
#      so that wait intervals run as a separate 'sleep' process name.

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
			/bin/sleep "$INTERVAL"
			continue
		fi
	fi
	echo $$ > "$LOCK_FILE"

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

	if [ -s "$IMG" ]; then
		if ! /usr/sbin/eips -c; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -c)"
		elif ! /usr/sbin/eips -f; then
			echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -f)"
		elif ! /usr/sbin/eips -g "$IMG"; then
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

	# Call the external /bin/sleep binary to avoid shell built-in process forking
	/bin/sleep "$INTERVAL"
done

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
TMP="$DASHBOARD_DIR/weather.once.$$"
LOCK_FILE="/tmp/kindle-refresh.lock"
STATUS_SENDER="${STATUS_SENDER:-$(dirname "$0")/send-status.sh}"
EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"

# Detect if a native timeout command is available
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
	if timeout 1 true >/dev/null 2>&1; then
		TIMEOUT_CMD="timeout"
	elif timeout -t 1 true >/dev/null 2>&1; then
		TIMEOUT_CMD="timeout -t"
	fi
elif busybox | grep -q "\btimeout\b" >/dev/null 2>&1; then
	if busybox timeout 1 true >/dev/null 2>&1; then
		TIMEOUT_CMD="busybox timeout"
	elif busybox timeout -t 1 true >/dev/null 2>&1; then
		TIMEOUT_CMD="busybox timeout -t"
	fi
fi

# POSIX sh compatible timeout helper
# Note: On Kindle BusyBox ash, spawning a background watchdog subshell leaves
# orphaned processes and leaks shell wrappers. We only use native timeout if available;
# otherwise, we execute directly without custom timeout wrappers.
timeout_cmd() {
	TIMEOUT_SEC=$1
	shift
	if [ -n "$TIMEOUT_CMD" ]; then
		$TIMEOUT_CMD "$TIMEOUT_SEC" "$@"
		return $?
	else
		# Fallback: run directly without timeout wrapper
		"$@"
		return $?
	fi
}

cleanup()
{
	rm -f "$TMP"
	rm -f "$LOCK_FILE"
	unset TOKEN
}

trap cleanup EXIT HUP INT TERM

# Acquire lock
if [ -f "$LOCK_FILE" ]; then
	OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
	if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') another active refresh process ($OLD_PID) is running, exiting"
		exit 0
	fi
fi
echo $$ > "$LOCK_FILE"

HOUR=$(date +%H)
HR=${HOUR#0}
HR=${HR:-0}
if [ "$HR" -ge 23 ] || [ "$HR" -lt 7 ]; then
	FALLBACK_LIGHT=1
else
	FALLBACK_LIGHT=8
fi

CONFIG_JSON=$(timeout_cmd 5 wget -q -O- "$CONFIG_URL")
if [ -z "$CONFIG_JSON" ]; then
	CONFIG_JSON=$(timeout_cmd 5 wget -q -O- "$LEGACY_CONFIG_URL")
fi

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
elif timeout_cmd 20 wget -q -O "$TMP" "$LEGACY_LOCAL_URL" && [ -s "$TMP" ]; then
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
	echo "$(date '+%Y-%m-%d %H:%M:%S') download failed; using previous image"
fi

DISPLAY_OK=0
if [ -s "$IMG" ]; then
	if ! timeout_cmd 15 "$EIPS_BIN" -c; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -c)"
		exit 1
	elif ! timeout_cmd 15 "$EIPS_BIN" -f; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -f)"
		exit 1
	elif ! timeout_cmd 20 "$EIPS_BIN" -g "$IMG"; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed/timed out (eips -g)"
		exit 1
	else
		DISPLAY_OK=1
	fi
fi

if [ "$DISPLAY_OK" -eq 1 ] && [ -f "$STATUS_SENDER" ]; then
	SERVER_HOST="$SERVER_HOST" DEVICE_ID="$DEVICE_ID" DASHBOARD_DIR="$DASHBOARD_DIR" sh "$STATUS_SENDER" >/dev/null 2>&1 || true
fi

exit 0

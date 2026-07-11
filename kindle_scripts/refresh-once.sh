#!/bin/sh
set -eu

LEGACY_LOCAL_URL="http://192.168.68.167:8765/weather.png"
LEGACY_CONFIG_URL="http://192.168.68.167:8767/api/config"

SERVER_HOST="${SERVER_HOST:-192.168.68.167}"
DEVICE_ID="${DEVICE_ID:-default-kindle}"
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ID_FILE="$DASHBOARD_DIR/device-id"
DEVICE_ENV_FILE="$DASHBOARD_DIR/device.env"

if [ -f "$DEVICE_ENV_FILE" ]; then
	. "$DEVICE_ENV_FILE"
fi

if [ -s "$DEVICE_ID_FILE" ] && [ "$DEVICE_ID" = "default-kindle" ]; then
	CANDIDATE=$(sed -n '1p' "$DEVICE_ID_FILE" | tr -d '\r\n')
	case "$CANDIDATE" in
		*[!a-zA-Z0-9_-]*|"") ;;
		*) DEVICE_ID="$CANDIDATE" ;;
	esac
fi

LOCAL_URL="${IMAGE_URL:-http://$SERVER_HOST:8765/device/$DEVICE_ID/image.png}"
PUBLIC_URL="https://user-zbox-ci320nano-series.taildabdfd.ts.net/weather.png"
CONFIG_URL="${CONFIG_URL:-http://$SERVER_HOST:8767/api/device/$DEVICE_ID/config}"
TOKEN_FILE="$DASHBOARD_DIR/public-token"
IMG="$DASHBOARD_DIR/image.png"
LEGACY_IMG="$DASHBOARD_DIR/weather.png"
TMP="$DASHBOARD_DIR/image.once.$$"
HDR_TMP="$DASHBOARD_DIR/image.once.headers.$$"
LOCK_FILE="/tmp/kindle-refresh.lock"
ETAG_FILE="$DASHBOARD_DIR/image.etag"
LAST_MODIFIED_FILE="$DASHBOARD_DIR/image.last_modified"
STATUS_SENDER="${STATUS_SENDER:-$(dirname "$0")/send-status.sh}"
EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"
WIFI_POWER_SAVE="${WIFI_POWER_SAVE:-1}"
UPDATE_ONLY_IF_CHANGED="${UPDATE_ONLY_IF_CHANGED:-1}"

cleanup() {
	rm -f "$TMP" "$HDR_TMP"
	rm -f "$LOCK_FILE"
	unset TOKEN
}

trap cleanup EXIT HUP INT TERM

if [ -f "$LOCK_FILE" ]; then
	OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
	if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') another active refresh process ($OLD_PID) is running, exiting"
		exit 0
	fi
fi
echo $$ > "$LOCK_FILE"

find_curl_bin() {
	if command -v curl >/dev/null 2>&1; then
		command -v curl
		return 0
	fi
	if [ -x /mnt/us/usbnet/bin/curl ]; then
		echo /mnt/us/usbnet/bin/curl
		return 0
	fi
	return 1
}

wait_for_network() {
	COUNT=0
	while [ "$COUNT" -lt 20 ]; do
		if command -v ifconfig >/dev/null 2>&1; then
			IP=$(ifconfig wlan0 2>/dev/null | sed -n 's/.*inet addr:\([0-9.][0-9.]*\).*/\1/p' | head -n 1)
			if [ -z "$IP" ]; then
				IP=$(ifconfig 2>/dev/null | sed -n 's/.*inet addr:\([0-9.][0-9.]*\).*/\1/p' | grep -v '^127\.' | head -n 1)
			fi
			if [ -n "$IP" ]; then
				return 0
			fi
		fi
		if command -v ip >/dev/null 2>&1 && ip route get 1.1.1.1 >/dev/null 2>&1; then
			return 0
		fi
		COUNT=$((COUNT + 1))
		/bin/sleep 1
	done
	return 1
}

wifi_enable() {
	if [ "$WIFI_POWER_SAVE" != "1" ]; then
		return 0
	fi
	lipc-set-prop com.lab126.wifid enable 1 2>/dev/null || true
	wait_for_network || true
}

wifi_disable() {
	if [ "$WIFI_POWER_SAVE" != "1" ]; then
		return 0
	fi
	lipc-set-prop com.lab126.wifid enable 0 2>/dev/null || true
}

extract_json_int() {
	KEY=$1
	echo "$CONFIG_JSON" | grep -o "\"$KEY\":[[:space:]]*[0-9][0-9]*" | tail -n 1 | grep -o '[0-9][0-9]*' || true
}

extract_json_bool() {
	KEY=$1
	echo "$CONFIG_JSON" | grep -o "\"$KEY\":[[:space:]]*\\(true\\|false\\)" | tail -n 1 | sed 's/.*:[[:space:]]*//' || true
}

apply_runtime_config() {
	HOUR=$(date +%H)
	HR=${HOUR#0}
	HR=${HR:-0}
	if [ "$HR" -ge 23 ] || [ "$HR" -lt 7 ]; then
		FALLBACK_LIGHT=1
	else
		FALLBACK_LIGHT=8
	fi

	CONFIG_JSON=$(wget -q -O- "$CONFIG_URL" 2>/dev/null || true)
	if [ -z "$CONFIG_JSON" ]; then
		CONFIG_JSON=$(wget -q -O- "$LEGACY_CONFIG_URL" 2>/dev/null || true)
	fi

	LIGHT=$(extract_json_int "kindle_frontlight")
	if [ -n "$LIGHT" ] && { [ "$LIGHT" -eq 0 ] || [ "$LIGHT" -eq 1 ] || [ "$LIGHT" -eq 4 ] || [ "$LIGHT" -eq 8 ] || [ "$LIGHT" -eq 12 ] || [ "$LIGHT" -eq 18 ]; }; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') frontlight: $LIGHT"
		lipc-set-prop com.lab126.powerd flIntensity "$LIGHT" 2>/dev/null || true
	else
		echo "$(date '+%Y-%m-%d %H:%M:%S') config unavailable, using fallback frontlight $FALLBACK_LIGHT"
		lipc-set-prop com.lab126.powerd flIntensity "$FALLBACK_LIGHT" 2>/dev/null || true
	fi

	POWER_SAVE=$(extract_json_bool "wifi_power_save")
	case "$POWER_SAVE" in
		true) WIFI_POWER_SAVE=1 ;;
		false) WIFI_POWER_SAVE=0 ;;
	esac

	ONLY_CHANGED=$(extract_json_bool "update_only_if_changed")
	case "$ONLY_CHANGED" in
		true) UPDATE_ONLY_IF_CHANGED=1 ;;
		false) UPDATE_ONLY_IF_CHANGED=0 ;;
	esac
}

save_response_headers() {
	if [ ! -f "$HDR_TMP" ]; then
		return 0
	fi
	ETAG=$(grep -i '^ETag:' "$HDR_TMP" | tail -n 1 | sed 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*//' | tr -d '\r' || true)
	if [ -n "$ETAG" ]; then
		printf '%s\n' "$ETAG" > "$ETAG_FILE"
	fi
	LAST_MODIFIED=$(grep -i '^Last-Modified:' "$HDR_TMP" | tail -n 1 | sed 's/^[Ll][Aa][Ss][Tt]-[Mm][Oo][Dd][Ii][Ff][Ii][Ee][Dd]:[[:space:]]*//' | tr -d '\r' || true)
	if [ -n "$LAST_MODIFIED" ]; then
		printf '%s\n' "$LAST_MODIFIED" > "$LAST_MODIFIED_FILE"
	fi
}

download_with_curl() {
	URL=$1
	CURL_BIN=$(find_curl_bin) || return 1
	rm -f "$TMP" "$HDR_TMP"

	if [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		"$CURL_BIN" -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			-H "If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-o "$TMP" \
			"$URL" >/dev/null 2>&1 || return 1
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ]; then
		"$CURL_BIN" -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			-o "$TMP" \
			"$URL" >/dev/null 2>&1 || return 1
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		"$CURL_BIN" -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-o "$TMP" \
			"$URL" >/dev/null 2>&1 || return 1
	else
		"$CURL_BIN" -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-o "$TMP" \
			"$URL" >/dev/null 2>&1 || return 1
	fi

	HTTP_CODE=$(awk '/^HTTP/{code=$2} END{print code}' "$HDR_TMP")
	case "$HTTP_CODE" in
		304)
			return 3
			;;
		200)
			[ -s "$TMP" ] || return 1
			save_response_headers
			return 0
			;;
	esac
	return 1
}

download_with_wget() {
	URL=$1
	rm -f "$TMP" "$HDR_TMP"
	if [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		wget --server-response \
			--header="If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			--header="If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-q -O "$TMP" "$URL" 2>"$HDR_TMP" || return 1
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ]; then
		wget --server-response \
			--header="If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			-q -O "$TMP" "$URL" 2>"$HDR_TMP" || return 1
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		wget --server-response \
			--header="If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-q -O "$TMP" "$URL" 2>"$HDR_TMP" || return 1
	else
		wget --server-response -q -O "$TMP" "$URL" 2>"$HDR_TMP" || return 1
	fi

	if grep -q " 304 " "$HDR_TMP"; then
		return 3
	fi
	[ -s "$TMP" ] || return 1
	save_response_headers
	return 0
}

download_image() {
	URL=$1
	download_with_curl "$URL"
	STATUS=$?
	if [ "$STATUS" -eq 0 ]; then
		return 0
	fi
	if [ "$STATUS" -eq 3 ]; then
		return 3
	fi
	download_with_wget "$URL"
	STATUS=$?
	if [ "$STATUS" -eq 0 ]; then
		return 0
	fi
	if [ "$STATUS" -eq 3 ]; then
		return 3
	fi
	return 1
}

wifi_enable
apply_runtime_config
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null || true

SOURCE=""
CHANGED=0
CYCLE_OK=0

if download_image "$LOCAL_URL"; then
	SOURCE="local"
else
	STATUS=$?
	if [ "$STATUS" -eq 3 ]; then
		SOURCE="local-304"
		CYCLE_OK=1
	elif download_image "$LEGACY_LOCAL_URL"; then
		SOURCE="legacy-local"
	else
		STATUS=$?
		if [ "$STATUS" -eq 3 ]; then
			SOURCE="legacy-local-304"
			CYCLE_OK=1
		else
			rm -f "$TMP"
			if [ -s "$TOKEN_FILE" ]; then
				TOKEN=$(sed -n '1p' "$TOKEN_FILE")
				CURL_BIN=$(find_curl_bin || true)
				if [ -n "${CURL_BIN:-}" ] && [ -n "$TOKEN" ]; then
					"$CURL_BIN" -fsS --connect-timeout 15 --max-time 45 \
						-H "Authorization: Bearer $TOKEN" \
						-o "$TMP" \
						"$PUBLIC_URL" >/dev/null 2>&1 || true
					if [ -s "$TMP" ]; then
						SOURCE="public"
					fi
				fi
				unset TOKEN
			fi
		fi
	fi
fi

if [ "$SOURCE" = "local-304" ] || [ "$SOURCE" = "legacy-local-304" ]; then
	echo "$(date '+%Y-%m-%d %H:%M:%S') image not modified via $SOURCE"
elif [ -n "$SOURCE" ] && [ -s "$TMP" ]; then
	if [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$IMG" ] && cmp -s "$TMP" "$IMG" 2>/dev/null; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') downloaded image unchanged via $SOURCE"
		CYCLE_OK=1
	else
		mv -f "$TMP" "$IMG"
		cp -f "$IMG" "$LEGACY_IMG" 2>/dev/null || true
		echo "$(date '+%Y-%m-%d %H:%M:%S') image updated via $SOURCE"
		CHANGED=1
		CYCLE_OK=1
	fi
else
	echo "$(date '+%Y-%m-%d %H:%M:%S') download failed; using previous image"
fi

DISPLAY_OK=0
if [ "$CHANGED" -eq 1 ] && [ -s "$IMG" ]; then
	if ! "$EIPS_BIN" -c; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed (eips -c)"
	elif ! "$EIPS_BIN" -f; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed (eips -f)"
	elif ! "$EIPS_BIN" -g "$IMG"; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') display update failed (eips -g)"
	else
		DISPLAY_OK=1
	fi
fi

if [ "$CYCLE_OK" -eq 1 ] && [ -f "$STATUS_SENDER" ]; then
	SERVER_HOST="$SERVER_HOST" DEVICE_ID="$DEVICE_ID" DASHBOARD_DIR="$DASHBOARD_DIR" sh "$STATUS_SENDER" >/dev/null 2>&1 || true
fi

wifi_disable
exit 0

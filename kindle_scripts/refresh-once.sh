#!/bin/sh
set -eu

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
CONFIG_URL="${CONFIG_URL:-http://$SERVER_HOST:8767/api/device/$DEVICE_ID/config}"
TOKEN_FILE="$DASHBOARD_DIR/public-token"
IMG="$DASHBOARD_DIR/image.png"
TMP="$DASHBOARD_DIR/image.once.$$"
HDR_TMP="$DASHBOARD_DIR/image.once.headers.$$"
ERR_TMP="$DASHBOARD_DIR/image.once.error.$$"
LOCK_FILE="/tmp/kindle-refresh.lock"
ETAG_FILE="$DASHBOARD_DIR/image.etag"
LAST_MODIFIED_FILE="$DASHBOARD_DIR/image.last_modified"
SERVER_SHA_FILE="$DASHBOARD_DIR/image.server.sha256"
STATUS_SENDER="${STATUS_SENDER:-$(dirname "$0")/send-status.sh}"
EIPS_BIN="${EIPS_BIN:-/usr/sbin/eips}"
WIFI_POWER_SAVE="${WIFI_POWER_SAVE:-1}"
UPDATE_ONLY_IF_CHANGED="${UPDATE_ONLY_IF_CHANGED:-1}"

cleanup() {
	rm -f "$TMP" "$HDR_TMP" "$ERR_TMP"
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

fail() {
	MESSAGE=$1
	echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: $MESSAGE" >&2
	wifi_disable
	exit 1
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
	SERVER_SHA=$(grep -i '^X-Image-SHA256:' "$HDR_TMP" | tail -n 1 | sed 's/^[Xx]-[Ii][Mm][Aa][Gg][Ee]-[Ss][Hh][Aa]256:[[:space:]]*//' | tr -d '\r' || true)
	if [ -n "$SERVER_SHA" ]; then
		printf '%s\n' "$SERVER_SHA" > "$SERVER_SHA_FILE"
	fi
}

is_valid_png() {
	[ -s "$TMP" ] || return 1
	MAGIC=$(hexdump -n 8 -e '8/1 "%02x"' "$TMP" 2>/dev/null)
	[ "$MAGIC" = "89504e470d0a1a0a" ]
}

request_url() {
	printf '%s?t=%s\n' "$1" "$(date +%s)"
}

download_with_curl() {
	CURL_BIN=$(find_curl_bin) || return 1
	REQUEST_URL=$(request_url "$LOCAL_URL")
	rm -f "$TMP" "$HDR_TMP" "$ERR_TMP"
	echo "$(date '+%Y-%m-%d %H:%M:%S') requested_url=$REQUEST_URL"

	set +e
	if [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		"$CURL_BIN" -fL -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "Cache-Control: no-cache" -H "Pragma: no-cache" \
			-H "If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			-H "If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-o "$TMP" \
			"$REQUEST_URL" 2>"$ERR_TMP"
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$ETAG_FILE" ]; then
		"$CURL_BIN" -fL -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "Cache-Control: no-cache" -H "Pragma: no-cache" \
			-H "If-None-Match: $(sed -n '1p' "$ETAG_FILE")" \
			-o "$TMP" \
			"$REQUEST_URL" 2>"$ERR_TMP"
	elif [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$LAST_MODIFIED_FILE" ]; then
		"$CURL_BIN" -fL -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "Cache-Control: no-cache" -H "Pragma: no-cache" \
			-H "If-Modified-Since: $(sed -n '1p' "$LAST_MODIFIED_FILE")" \
			-o "$TMP" \
			"$REQUEST_URL" 2>"$ERR_TMP"
	else
		"$CURL_BIN" -fL -sS --connect-timeout 15 --max-time 45 \
			-D "$HDR_TMP" \
			-H "Cache-Control: no-cache" -H "Pragma: no-cache" \
			-o "$TMP" \
			"$REQUEST_URL" 2>"$ERR_TMP"
	fi
	STATUS=$?
	set -e
	if [ "$STATUS" -ne 0 ]; then
		cat "$ERR_TMP" >&2
		return 1
	fi

	HTTP_CODE=$(awk '/^HTTP/{code=$2} END{print code}' "$HDR_TMP")
	case "$HTTP_CODE" in
		304)
			return 3
			;;
		200)
			is_valid_png || { echo "downloaded response is not a valid PNG" >&2; return 1; }
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
	download_with_curl
}

wifi_enable
apply_runtime_config
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null || true

CHANGED=0
CYCLE_OK=0

download_image || STATUS=$?
STATUS=${STATUS:-0}
if [ "$STATUS" -eq 3 ]; then
	CYCLE_OK=1
	echo "$(date '+%Y-%m-%d %H:%M:%S') image not modified via $LOCAL_URL"
elif [ "$STATUS" -ne 0 ]; then
	fail "download failed for $LOCAL_URL"
elif [ -s "$TMP" ]; then
	DOWNLOADED_SHA=$(sha256sum "$TMP" | awk '{print $1}')
	SERVER_SHA=$(cat "$SERVER_SHA_FILE" 2>/dev/null || echo unknown)
	echo "$(date '+%Y-%m-%d %H:%M:%S') downloaded_file=$TMP downloaded_sha256=$DOWNLOADED_SHA server_sha256=$SERVER_SHA"
	if [ "$UPDATE_ONLY_IF_CHANGED" = "1" ] && [ -s "$IMG" ] && cmp -s "$TMP" "$IMG" 2>/dev/null; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') image unchanged via $LOCAL_URL"
		rm -f "$TMP"
		CYCLE_OK=1
	else
		mv -f "$TMP" "$IMG" || fail "atomic image replacement failed"
		CHANGED=1
		CYCLE_OK=1
		echo "$(date '+%Y-%m-%d %H:%M:%S') image atomically replaced: $IMG"
	fi
else
	fail "device-specific download produced no file: $LOCAL_URL"
fi

DISPLAY_OK=0
if [ "$CHANGED" -eq 1 ] && [ -s "$IMG" ]; then
	"$EIPS_BIN" -c; EIPS_STATUS=$?; echo "$(date '+%Y-%m-%d %H:%M:%S') eips_clear_exit=$EIPS_STATUS"; [ "$EIPS_STATUS" -eq 0 ] || fail "eips -c failed"
	"$EIPS_BIN" -f; EIPS_STATUS=$?; echo "$(date '+%Y-%m-%d %H:%M:%S') eips_full_exit=$EIPS_STATUS"; [ "$EIPS_STATUS" -eq 0 ] || fail "eips -f failed"
	"$EIPS_BIN" -g "$IMG"; EIPS_STATUS=$?; echo "$(date '+%Y-%m-%d %H:%M:%S') eips_display_file=$IMG eips_exit=$EIPS_STATUS"; [ "$EIPS_STATUS" -eq 0 ] || fail "eips -g failed"
	DISPLAY_OK=1
fi

if [ "$CYCLE_OK" -eq 1 ] && [ -f "$STATUS_SENDER" ]; then
	SERVER_HOST="$SERVER_HOST" DEVICE_ID="$DEVICE_ID" DASHBOARD_DIR="$DASHBOARD_DIR" sh "$STATUS_SENDER" >/dev/null 2>&1 || true
fi

wifi_disable
exit 0

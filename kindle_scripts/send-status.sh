#!/bin/sh

SERVER_HOST="${SERVER_HOST:-192.168.68.167}"
DEVICE_ID="${DEVICE_ID:-default-kindle}"
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ID_FILE="$DASHBOARD_DIR/device-id"
STATUS_TOKEN_FILE="$DASHBOARD_DIR/status-token"
POWER_SUPPLY_DIR="${POWER_SUPPLY_DIR:-/sys/class/power_supply}"
FIRMWARE_VERSION="${FIRMWARE_VERSION:-kindle-refresh-1.0}"

if [ -s "$DEVICE_ID_FILE" ] && [ "$DEVICE_ID" = "default-kindle" ]; then
	CANDIDATE=$(sed -n '1p' "$DEVICE_ID_FILE" | tr -d '\r\n')
	case "$CANDIDATE" in
		*[!a-zA-Z0-9_-]*|"") ;;
		*) DEVICE_ID="$CANDIDATE" ;;
	esac
fi

STATUS_URL="http://$SERVER_HOST:8767/api/device/$DEVICE_ID/status"

json_escape() {
	echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

BATTERY_PERCENT=""
for f in "$POWER_SUPPLY_DIR"/*/capacity
do
	if [ -r "$f" ]; then
		V=$(sed -n '1p' "$f" 2>/dev/null | tr -d '\r\n')
		case "$V" in
			""|*[!0-9]*) ;;
			*) BATTERY_PERCENT="$V"; break ;;
		esac
	fi
done

CHARGING=""
for f in "$POWER_SUPPLY_DIR"/*/status
do
	if [ -r "$f" ]; then
		S=$(sed -n '1p' "$f" 2>/dev/null | tr -d '\r\n')
		case "$S" in
			Charging|Full) CHARGING="true"; break ;;
			Discharging|"Not charging") CHARGING="false"; break ;;
		esac
	fi
done

IP_ADDRESS=""
if command -v ip >/dev/null 2>&1; then
	IP_ADDRESS=$(ip route get "$SERVER_HOST" 2>/dev/null | sed -n 's/.* src \([0-9.][0-9.]*\).*/\1/p' | sed -n '1p')
	if [ -z "$IP_ADDRESS" ]; then
		IP_ADDRESS=$(ip addr show 2>/dev/null | sed -n 's/.*inet \([0-9.][0-9.]*\)\/.*/\1/p' | grep -v '^127\.' | sed -n '1p')
	fi
fi
if [ -z "$IP_ADDRESS" ] && command -v ifconfig >/dev/null 2>&1; then
	IP_ADDRESS=$(ifconfig 2>/dev/null | sed -n 's/.*inet addr:\([0-9.][0-9.]*\).*/\1/p' | grep -v '^127\.' | sed -n '1p')
fi

LAST_REFRESH_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
if [ -z "$LAST_REFRESH_AT" ]; then
	LAST_REFRESH_AT=$(date '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)
fi

JSON="{"
SEP=""
if [ -n "$BATTERY_PERCENT" ]; then
	JSON="${JSON}${SEP}\"battery_percent\":$BATTERY_PERCENT"
	SEP=","
fi
if [ -n "$CHARGING" ]; then
	JSON="${JSON}${SEP}\"charging\":$CHARGING"
	SEP=","
fi
if [ -n "$IP_ADDRESS" ]; then
	JSON="${JSON}${SEP}\"ip_address\":\"$(json_escape "$IP_ADDRESS")\""
	SEP=","
fi
JSON="${JSON}${SEP}\"firmware_version\":\"$(json_escape "$FIRMWARE_VERSION")\""
SEP=","
if [ -n "$LAST_REFRESH_AT" ]; then
	JSON="${JSON}${SEP}\"last_refresh_at\":\"$(json_escape "$LAST_REFRESH_AT")\""
fi
JSON="${JSON}}"

STATUS_TOKEN="${STATUS_TOKEN:-}"
if [ -z "$STATUS_TOKEN" ] && [ -s "$STATUS_TOKEN_FILE" ]; then
	STATUS_TOKEN=$(sed -n '1p' "$STATUS_TOKEN_FILE" | tr -d '\r\n')
fi

CURL_BIN=""
if command -v curl >/dev/null 2>&1; then
	CURL_BIN=$(command -v curl)
elif [ -x /mnt/us/usbnet/bin/curl ]; then
	CURL_BIN="/mnt/us/usbnet/bin/curl"
fi

if [ -n "$CURL_BIN" ]; then
	if [ -n "$STATUS_TOKEN" ]; then
		"$CURL_BIN" -fsS --connect-timeout 5 --max-time 15 \
			-H "Content-Type: application/json" \
			-H "Authorization: Bearer $STATUS_TOKEN" \
			--data "$JSON" \
			"$STATUS_URL" >/dev/null 2>&1 || true
	else
		"$CURL_BIN" -fsS --connect-timeout 5 --max-time 15 \
			-H "Content-Type: application/json" \
			--data "$JSON" \
			"$STATUS_URL" >/dev/null 2>&1 || true
	fi
	unset STATUS_TOKEN
	exit 0
fi

if command -v wget >/dev/null 2>&1; then
	if [ -n "$STATUS_TOKEN" ]; then
		wget -q -O- \
			--header="Content-Type: application/json" \
			--header="Authorization: Bearer $STATUS_TOKEN" \
			--post-data="$JSON" \
			"$STATUS_URL" >/dev/null 2>&1 || true
	else
		wget -q -O- \
			--header="Content-Type: application/json" \
			--post-data="$JSON" \
			"$STATUS_URL" >/dev/null 2>&1 || true
	fi
fi

unset STATUS_TOKEN
exit 0

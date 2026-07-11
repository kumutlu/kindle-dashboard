#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ENV_FILE="$DASHBOARD_DIR/device.env"
PID_FILE="$DASHBOARD_DIR/dashboard_loop.pid"
REFRESH_ONCE_SH="$DASHBOARD_DIR/refresh-once.sh"

if [ -f "$DEVICE_ENV_FILE" ]; then
	. "$DEVICE_ENV_FILE"
fi

cleanup() {
	rm -f "$PID_FILE"
}

trap cleanup EXIT HUP INT TERM
echo $$ > "$PID_FILE"

while true
do
	if [ -x "$REFRESH_ONCE_SH" ]; then
		"$REFRESH_ONCE_SH" || true
	fi

	if [ -f "$DEVICE_ENV_FILE" ]; then
		. "$DEVICE_ENV_FILE"
	fi

	SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
	case "$SLEEP_MINUTES" in
		5|10|15|30|60) ;;
		*) SLEEP_MINUTES=60 ;;
	esac
	SLEEP_SECONDS=$((SLEEP_MINUTES * 60))
	echo "$(date '+%Y-%m-%d %H:%M:%S') next wake cycle in ${SLEEP_MINUTES} minutes"
	/bin/sleep "$SLEEP_SECONDS"
done

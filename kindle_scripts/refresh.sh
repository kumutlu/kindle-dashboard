#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
REFRESH_ONCE_SH="${REFRESH_ONCE_SH:-$DASHBOARD_DIR/refresh-once.sh}"
LOG_FILE="${LOG_FILE:-$DASHBOARD_DIR/dashboard.log}"

MESSAGE="$(date '+%Y-%m-%d %H:%M:%S') legacy refresh.sh invoked; KindleCron owns scheduling"
echo "$MESSAGE"
echo "$MESSAGE" >> "$LOG_FILE"

if [ ! -x "$REFRESH_ONCE_SH" ]; then
	echo "ERROR: missing executable $REFRESH_ONCE_SH" >&2
	exit 1
fi

exec "$REFRESH_ONCE_SH"

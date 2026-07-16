#!/bin/sh
set -eu

DASHBOARD_DIR=/mnt/us/dashboard
ROLLBACK_DIR=/mnt/us/kindle-131-low-power-rollback-20260716-115926
COUNT_FILE="$ROLLBACK_DIR/consecutive-failures"
ROLLBACK="$ROLLBACK_DIR/rollback.sh"
MAX_FAILURES=3

read_count() {
    COUNT=0
    if [ -s "$COUNT_FILE" ]; then
        COUNT=$(sed -n '1p' "$COUNT_FILE")
    fi
    case "$COUNT" in
        ''|*[!0-9]*) COUNT=0 ;;
    esac
}

case "${1-}" in
    --reset)
        printf '0\n' > "$COUNT_FILE"
        ;;
    --record-failure)
        read_count
        COUNT=$((COUNT + 1))
        printf '%s\n' "$COUNT" > "$COUNT_FILE"
        if [ "$COUNT" -ge "$MAX_FAILURES" ]; then
            exec "$ROLLBACK"
        fi
        ;;
    --boot-check)
        if [ ! -f "$DASHBOARD_DIR/LOW_POWER_ACTIVE" ]; then
            exit 0
        fi
        read_count
        if [ "$COUNT" -ge "$MAX_FAILURES" ]; then
            exec "$ROLLBACK"
        fi
        ;;
    *)
        echo "Usage: $0 --reset|--record-failure|--boot-check" >&2
        exit 2
        ;;
esac

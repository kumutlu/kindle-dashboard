# Kindle status updates

The repo-provided Kindle refresh scripts can report device health back to the
settings server after a successful wake / refresh cycle.

Install these files together on the Kindle dashboard folder:

- `refresh.sh`
- `refresh-once.sh`
- `send-status.sh`

After `refresh.sh` or `refresh-once.sh` successfully checks the dashboard
endpoint and completes its cycle, it runs:

```sh
SERVER_HOST="$SERVER_HOST" DEVICE_ID="$DEVICE_ID" DASHBOARD_DIR="$DASHBOARD_DIR" sh "$(dirname "$0")/send-status.sh"
```

`send-status.sh` is BusyBox `/bin/sh` compatible. It reads:

- `battery_percent` from `/sys/class/power_supply/*/capacity`
- `charging` from `/sys/class/power_supply/*/status`
- local IP address from `ip` or `ifconfig` when available
- `firmware_version` as `kindle-refresh-1.0`
- `last_refresh_at` from `date -u`

It posts JSON to:

```text
http://<SERVER_HOST>:8767/api/device/<DEVICE_ID>/status
```

Configuration:

- `SERVER_HOST` defaults to `192.168.68.167`.
- `DEVICE_ID` defaults to `default-kindle`, or the first line of
  `$DASHBOARD_DIR/device-id` when present.
- `STATUS_TOKEN` is optional. If not set, `$DASHBOARD_DIR/status-token` is used
  when present.

When a token is configured, the script sends:

```text
Authorization: Bearer <token>
```

Status update failures are ignored so dashboard refreshes continue even if the
settings server is unreachable.

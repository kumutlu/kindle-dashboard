#!/usr/bin/env bash

KEY="/home/user/.ssh/kindle_ed25519"
REMOTE_PATH="/mnt/us/dashboard"
REMOTE_IMAGE="/mnt/us/dashboard/image.png"
EIPS="/usr/sbin/eips"

DEVICES=(
  "default-kindle:192.168.68.119"
  "kindle-no4:192.168.68.131"
  "kitchen-kindle:192.168.68.122"
)

ok() { echo "✓ $1"; }
fail() { echo "✗ $1"; }

for item in "${DEVICES[@]}"; do
  DEVICE_ID="${item%%:*}"
  IP="${item##*:}"

  echo
  echo "=============================="
  echo "Checking $DEVICE_ID ($IP)"
  echo "=============================="

  READY=true

  ping -c 1 -W 2 "$IP" >/dev/null 2>&1 \
    && ok "Ping" \
    || {  fail "Ping skipped/non-critical"; }

  ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 root@"$IP" "echo ssh-ok" >/dev/null 2>&1 \
    && ok "SSH key login" \
    || { fail "SSH key login"; READY=false; }

  ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 root@"$IP" "test -x $EIPS" >/dev/null 2>&1 \
    && ok "eips exists: $EIPS" \
    || { fail "eips missing"; READY=false; }

  ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 root@"$IP" "mkdir -p $REMOTE_PATH && test -d $REMOTE_PATH" >/dev/null 2>&1 \
    && ok "Dashboard folder exists" \
    || { fail "Dashboard folder"; READY=false; }

  if [ -d "devices/$DEVICE_ID" ]; then
    ok "Local device config folder exists"
  else
    fail "Local device config folder missing: devices/$DEVICE_ID"
    READY=false
  fi

  if [ -f "devices/$DEVICE_ID/config.json" ]; then
    ok "Local config.json exists"
  else
    fail "Local config.json missing"
    READY=false
  fi

  if python3 - <<PY >/tmp/kindle_render_${DEVICE_ID}.log 2>&1
import weather_image
print(weather_image.render_device("$DEVICE_ID", force=True))
PY
  then
    ok "Render works"
  else
    fail "Render failed"
    cat "/tmp/kindle_render_${DEVICE_ID}.log"
    READY=false
  fi

  if [ -f "devices/$DEVICE_ID/image.png" ]; then
    ok "Local image exists"
  else
    fail "Local image missing"
    READY=false
  fi

  scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 "devices/$DEVICE_ID/image.png" root@"$IP":"$REMOTE_IMAGE" >/dev/null 2>&1 \
    && ok "SCP image copy" \
    || { fail "SCP image copy"; READY=false; }

  ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 root@"$IP" "$EIPS -c; $EIPS -c; $EIPS -g $REMOTE_IMAGE" >/dev/null 2>&1 \
    && ok "Screen refresh" \
    || { fail "Screen refresh"; READY=false; }

  if [ "$READY" = true ]; then
    echo "RESULT: READY"
  else
    echo "RESULT: NOT READY"
  fi
done

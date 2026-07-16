#!/bin/sh
set -eu

DASHBOARD_DIR=/mnt/us/dashboard
ROLLBACK_DIR=/mnt/us/kindle-131-low-power-rollback-20260716-115926
SNAPSHOT_DIR="$ROLLBACK_DIR/snapshot"

check_snapshot() {
    test -d "$SNAPSHOT_DIR/dashboard"
    test -f "$SNAPSHOT_DIR/dashboard/refresh.sh"
    test -f "$SNAPSHOT_DIR/dashboard/refresh-once.sh"
    test -f "$SNAPSHOT_DIR/dashboard/watchdog.sh"
    test -f "$SNAPSHOT_DIR/system/dashboard.conf"
    test -f "$SNAPSHOT_DIR/system/usbnet-config"
    test -x "$DASHBOARD_DIR/stop.sh"
}

if [ "${1-}" = "--check" ]; then
    check_snapshot
    echo "kindle-131 rollback snapshot ready"
    exit 0
fi

check_snapshot
touch "$DASHBOARD_DIR/DISABLE_LOW_POWER"

/sbin/stop kindle-131-low-power 2>/dev/null || true
/sbin/stop dashboard 2>/dev/null || true
"$DASHBOARD_DIR/stop.sh" 2>/dev/null || true

/usr/sbin/mntroot rw
rm -f /etc/upstart/kindle-131-low-power.conf
rm -f /etc/upstart/kindle-131-low-power-failsafe.conf
cp "$SNAPSHOT_DIR/system/dashboard.conf" /etc/upstart/dashboard.conf
chmod 644 /etc/upstart/dashboard.conf
/usr/sbin/mntroot ro

cp -a "$SNAPSHOT_DIR/dashboard/." "$DASHBOARD_DIR/"
rm -f "$DASHBOARD_DIR/LOW_POWER_ACTIVE"
cp "$SNAPSHOT_DIR/system/usbnet-config" /mnt/us/usbnet/etc/config
if [ -f "$SNAPSHOT_DIR/system/usbnet-auto" ]; then
    cp "$SNAPSHOT_DIR/system/usbnet-auto" /mnt/us/usbnet/auto
fi

lipc-set-prop com.lab126.wifid enable 1 2>/dev/null || true
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null || true
/sbin/start framework 2>/dev/null || true
/sbin/start lab126_gui 2>/dev/null || true
/sbin/start dashboard 2>/dev/null || true

echo "kindle-131 legacy dashboard restored"

#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
KRON_BIN="${KRON_BIN:-/mnt/us/kron/kron}"
LOG_FILE="${LOG_FILE:-$DASHBOARD_DIR/kindlecron-install.log}"
EXPECTED_VERSION="KindleCron v0.2.0"
UPSTART_CONF="${UPSTART_CONF:-/etc/upstart/dashboard.conf}"
MNTROOT_BIN="${MNTROOT_BIN:-mntroot}"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
ROOTFS_RW=0

log_msg() {
	LINE="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$LINE"
	echo "$LINE" >> "$LOG_FILE"
}

fail() {
	log_msg "ERROR: $1" >&2
	exit 1
}

restore_rootfs() {
	if [ "${ROOTFS_RW:-0}" = "1" ]; then
		"$MNTROOT_BIN" ro 2>/dev/null || true
		ROOTFS_RW=0
	fi
}

on_signal() {
	restore_rootfs
	exit 1
}

trap restore_rootfs EXIT
trap on_signal HUP INT TERM

verify_kron() {
	if [ ! -x "$KRON_BIN" ]; then
		fail "required KindleCron binary is missing or not executable: $KRON_BIN"
	fi
	VERSION_OUTPUT=$("$KRON_BIN" version 2>&1) || \
		fail "KindleCron version command failed: $KRON_BIN"
	case "$VERSION_OUTPUT" in
		"$EXPECTED_VERSION"*)
			log_msg "verified $EXPECTED_VERSION at $KRON_BIN"
			;;
		*)
			fail "expected $EXPECTED_VERSION, got: $VERSION_OUTPUT"
			;;
	esac
}

schedule_for_minutes() {
	case "$1" in
		5) echo "every 5m" ;;
		10) echo "every 10m" ;;
		15) echo "every 15m" ;;
		30) echo "every 30m" ;;
		60) echo "every 1h" ;;
		*) echo "every 1h" ;;
	esac
}

load_interval_from_device_env() {
	REFRESH_INTERVAL_MINUTES=60
	if [ -f "$DASHBOARD_DIR/device.env" ]; then
		. "$DASHBOARD_DIR/device.env"
	fi
	case "${REFRESH_INTERVAL_MINUTES:-60}" in
		5|10|15|30|60) ;;
		*) REFRESH_INTERVAL_MINUTES=60 ;;
	esac
	SCHEDULE=$(schedule_for_minutes "$REFRESH_INTERVAL_MINUTES")
	log_msg "dashboard interval: $REFRESH_INTERVAL_MINUTES minutes ($SCHEDULE)"
}

disable_owned_legacy_upstart_config() {
	if [ ! -f "$UPSTART_CONF" ]; then
		return 0
	fi
	if ! grep -q '/mnt/us/dashboard' "$UPSTART_CONF"; then
		fail "refusing to modify non-dashboard Upstart file: $UPSTART_CONF"
	fi
	ROOTFS_RW=1
	"$MNTROOT_BIN" rw || fail "could not make rootfs writable"
	if ! mv -f "$UPSTART_CONF" "$DASHBOARD_DIR/dashboard.conf.legacy-disabled"; then
		fail "could not disable legacy dashboard Upstart config: $UPSTART_CONF"
	fi
	"$MNTROOT_BIN" ro || fail "could not restore rootfs read-only"
	ROOTFS_RW=0
	log_msg "disabled legacy dashboard Upstart config: $UPSTART_CONF"
}

daemon_count() {
	if [ -n "${KRON_DAEMON_STATE_FILE:-}" ]; then
		cat "$KRON_DAEMON_STATE_FILE" 2>/dev/null || echo 0
		return 0
	fi
	ps auxww 2>/dev/null | grep -F "$KRON_BIN daemon" | grep -v grep | \
		wc -l | tr -d ' '
}

start_daemon_once() {
	COUNT=$(daemon_count)
	case "$COUNT" in
		0)
			"$KRON_BIN" daemon >> "${KRON_DAEMON_LOG:-/mnt/us/kron/kron.log}" 2>&1 &
			"$SLEEP_BIN" 2
			if [ -n "${KRON_DAEMON_STATE_FILE:-}" ]; then
				printf '1\n' > "$KRON_DAEMON_STATE_FILE"
			fi
			if [ "$(daemon_count)" != "1" ]; then
				fail "KindleCron daemon did not start exactly once"
			fi
			log_msg "KindleCron daemon started"
			;;
		1)
			log_msg "KindleCron daemon already running"
			;;
		*)
			fail "multiple KindleCron daemons detected: $COUNT"
			;;
	esac
}

show_registered_jobs() {
	LIST_OUTPUT=$("$KRON_BIN" list 2>&1) || fail "KindleCron job listing failed"
	log_msg "KindleCron jobs: $LIST_OUTPUT"
}

install_integration() {
	verify_kron
	load_interval_from_device_env
	if [ ! -x "$DASHBOARD_DIR/refresh-once.sh" ]; then
		fail "missing executable $DASHBOARD_DIR/refresh-once.sh"
	fi
	if [ -x "$DASHBOARD_DIR/stop.sh" ]; then
		"$DASHBOARD_DIR/stop.sh" || fail "legacy dashboard scheduler shutdown failed"
	fi
	disable_owned_legacy_upstart_config
	if ! "$KRON_BIN" add -timeout 2m dashboard "$SCHEDULE" "$DASHBOARD_DIR/refresh-once.sh"; then
		fail "KindleCron dashboard job registration failed"
	fi
	log_msg "registered dashboard job: $SCHEDULE, timeout 2m"
	start_daemon_once
	show_registered_jobs
	log_msg "persistent startup is not configured; reboot persistence remains unresolved"
}

ACTION="${1:-install}"
case "$ACTION" in
	install)
		install_integration
		;;
	start-daemon)
		verify_kron
		start_daemon_once
		show_registered_jobs
		log_msg "persistent startup is not configured; reboot persistence remains unresolved"
		;;
	*)
		fail "unknown action: $ACTION"
		;;
esac

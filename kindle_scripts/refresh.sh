#!/bin/sh
set -eu

DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
DEVICE_ENV_FILE="$DASHBOARD_DIR/device.env"
PID_FILE="$DASHBOARD_DIR/dashboard_loop.pid"
REFRESH_ONCE_SH="$DASHBOARD_DIR/refresh-once.sh"
PROC_DIR="${PROC_DIR:-/proc}"
RTC_SYS_DIR="${RTC_SYS_DIR:-/sys/class/rtc/rtc1}"
LOG_FILE="${DASHBOARD_DIR}/dashboard.log"
SLEEP_BIN="${SLEEP_BIN:-/bin/sleep}"
LIPC_BIN="${LIPC_BIN:-lipc-set-prop}"
LIPC_GET_BIN="${LIPC_GET_BIN:-lipc-get-prop}"
KILL_CMD="${KILL_CMD:-kill}"

log_msg() {
	MSG="$(date '+%Y-%m-%d %H:%M:%S') $1"
	echo "$MSG"
	echo "$MSG" >> "$LOG_FILE"
	if [ -f "$LOG_FILE" ]; then
		SIZE=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
		if [ "$SIZE" -gt 100000 ]; then
			tail -n 500 "$LOG_FILE" > "${LOG_FILE}.tmp" 2>/dev/null && mv -f "${LOG_FILE}.tmp" "$LOG_FILE"
		fi
	fi
}

# Single-instance protection with stale PID command verification
if [ -f "$PID_FILE" ]; then
	OLD_LPID=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$OLD_LPID" ] && [ "$OLD_LPID" != "$$" ] && "$KILL_CMD" -0 "$OLD_LPID" 2>/dev/null; then
		OLD_CMDLINE=$(cat "$PROC_DIR/$OLD_LPID/cmdline" 2>/dev/null | tr '\0\n\r' '   ')
		PAD_CMDLINE=" $OLD_CMDLINE "
		case "$PAD_CMDLINE" in
			*" $DASHBOARD_DIR/dashboard_loop.sh "*|*" /mnt/us/dashboard/dashboard_loop.sh "*|*" $DASHBOARD_DIR/refresh.sh "*|*" /mnt/us/dashboard/refresh.sh "*)
				exit 0
				;;
		esac
	fi
fi
echo $$ > "$PID_FILE"

cleanup() {
	if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ]; then
		rm -f "$PID_FILE"
	fi
}
trap cleanup EXIT
trap "cleanup; exit 0" HUP INT TERM

while true
do
	if [ -f "$DEVICE_ENV_FILE" ]; then
		. "$DEVICE_ENV_FILE"
	fi

	# Native RTC Scheduler path is ONLY enabled when NATIVE_RTC_SCHEDULER=1
	ENABLE_RTC_SCHEDULER=0
	if [ "${NATIVE_RTC_SCHEDULER:-0}" -eq 1 ]; then
		ENABLE_RTC_SCHEDULER=1
	fi

	if [ "$ENABLE_RTC_SCHEDULER" -eq 1 ]; then
		log_msg "native scheduler enabled"

		# Execute refresh-once.sh and log exact status
		if [ -x "$REFRESH_ONCE_SH" ]; then
			if "$REFRESH_ONCE_SH"; then
				log_msg "refresh completed"
			else
				REFRESH_RC=$?
				log_msg "refresh failed rc=$REFRESH_RC"
			fi
		else
			log_msg "ERROR: missing $REFRESH_ONCE_SH"
		fi

		SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
		case "$SLEEP_MINUTES" in
			5|10|15|30|60) ;;
			*) SLEEP_MINUTES=60 ;;
		esac
		INTERVAL_SECONDS=$((SLEEP_MINUTES * 60))
		log_msg "interval seconds: $INTERVAL_SECONDS"

		RTC_DIR="${RTC_SYS_DIR:-/sys/class/rtc/rtc1}"
		if [ ! -d "$RTC_DIR" ] || [ ! -r "$RTC_DIR/since_epoch" ] || [ ! -w "$RTC_DIR/wakealarm" ]; then
			log_msg "failure: rtc1 missing or permissions invalid"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		NOW=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')
		case "$NOW" in
			""|*[!0-9]*)
				log_msg "failure: invalid current epoch time: $NOW"
				"$SLEEP_BIN" "$INTERVAL_SECONDS"
				continue
				;;
		esac
		log_msg "rtc now: $NOW"

		if ! echo 0 > "$RTC_DIR/wakealarm" 2>/dev/null; then
			log_msg "failure: clear wakealarm failed"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		TARGET=$((NOW + INTERVAL_SECONDS))
		log_msg "requested alarm: $TARGET"

		if ! echo "$TARGET" > "$RTC_DIR/wakealarm" 2>/dev/null; then
			log_msg "failure: wakealarm write failed"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		ACTUAL=$(cat "$RTC_DIR/wakealarm" 2>/dev/null | tr -d '\r\n')
		if [ "$ACTUAL" != "$TARGET" ]; then
			log_msg "failure: wakealarm verification mismatch (expected $TARGET, got $ACTUAL)"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi
		log_msg "verified alarm: $ACTUAL"

		if command -v "$LIPC_BIN" >/dev/null 2>&1; then
			"$LIPC_BIN" com.lab126.powerd preventScreenSaver 0 2>/dev/null || true
			log_msg "preventScreenSaver release"
		fi

		sync

		BEFORE=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')
		log_msg "native suspend requested"

		SUSPEND_OK=0
		if command -v "$LIPC_BIN" >/dev/null 2>&1; then
			if "$LIPC_BIN" -i com.lab126.powerd powerButton 1 2>/dev/null; then
				SUSPEND_OK=1
				log_msg "suspend command exit: 0"
			else
				log_msg "suspend command exit: non-zero"
			fi
		else
			log_msg "failure: lipc-set-prop missing"
		fi

		if [ "$SUSPEND_OK" -ne 1 ]; then
			log_msg "failure: powerButton suspend failed"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		SEEN_SCREENSAVER=0
		TRANSITION_TIMEOUT="${SUSPEND_ENTRY_TIMEOUT:-120}"
		POLL_COUNT=0
		LAST_LOGGED_STATE=""
		CURR_STATE=""

		while true
		do
			if command -v "$LIPC_GET_BIN" >/dev/null 2>&1; then
				CURR_STATE=$("$LIPC_GET_BIN" com.lab126.powerd state 2>/dev/null | tr -d '\r\n' || true)
			fi

			if [ -n "$CURR_STATE" ] && [ "$CURR_STATE" != "$LAST_LOGGED_STATE" ]; then
				log_msg "powerd state: $CURR_STATE"
				LAST_LOGGED_STATE="$CURR_STATE"
			fi

			if [ "$CURR_STATE" = "screenSaver" ]; then
				SEEN_SCREENSAVER=1
				log_msg "entering passive suspend handoff wait"
				break
			fi

			POLL_COUNT=$((POLL_COUNT + 1))
			if [ "$POLL_COUNT" -ge "$TRANSITION_TIMEOUT" ]; then
				log_msg "failure: suspend entry timeout (never reached screenSaver state)"
				break
			fi

			"$SLEEP_BIN" 1
		done

		if [ "$SEEN_SCREENSAVER" -ne 1 ]; then
			log_msg "failure: powerd suspend lifecycle incomplete"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		# PHASE A — Passive Pre-TARGET Wait (V3.5):
		# Do not use a long relative sleep (CLOCK_MONOTONIC pauses during suspend).
		# Use zero-IPC sysfs RTC checks (STEP_SLEEP=10s) until since_epoch >= TARGET.
		# ZERO LIPC queries occur during Phase A after screenSaver entry to leave powerd undisturbed.
		# While in deep kernel suspend, userspace is frozen and consumes 0 CPU.
		STEP_SLEEP="${SUSPEND_STEP_SLEEP:-10}"
		PHASE_A_RESULT=""
		PHASE_A_TICKS=0

		while true
		do
			CURR_RTC=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n' || echo 0)
			case "$CURR_RTC" in
				[0-9]*)
					if [ "$CURR_RTC" -ge "$TARGET" ]; then
						PHASE_A_RESULT="target_reached"
						log_msg "phase A awake ticks: $PHASE_A_TICKS"
						log_msg "RTC target reached: $CURR_RTC"
						break
					fi
					;;
				*)
					PHASE_A_RESULT="rtc_invalid"
					break
					;;
			esac

			PHASE_A_TICKS=$((PHASE_A_TICKS + 1))
			"$SLEEP_BIN" "$STEP_SLEEP"
		done

		if [ "$PHASE_A_RESULT" != "target_reached" ]; then
			log_msg "failure: passive RTC wait aborted (result: ${PHASE_A_RESULT:-failed})"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		# PHASE B — Post-TARGET Grace Poll (V3.5):
		# Entered ONLY after Hardware RTC alarm has fired (CURR_RTC >= TARGET).
		# Allow a bounded grace window (DEADLINE = TARGET + GRACE_SECONDS) for powerd to transition to "active".
		GRACE_SECONDS="${RESUME_GRACE_SECONDS:-180}"
		DEADLINE=$((TARGET + GRACE_SECONDS))
		GOT_ACTIVE=0
		POLL_INTERVAL="${RESUME_POLL_INTERVAL:-2}"
		LAST_LOGGED_STATE=""

		while true
		do
			CURR_RTC=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n' || echo 0)
			if command -v "$LIPC_GET_BIN" >/dev/null 2>&1; then
				CURR_STATE=$("$LIPC_GET_BIN" com.lab126.powerd state 2>/dev/null | tr -d '\r\n' || true)
				if [ -n "$CURR_STATE" ] && [ "$CURR_STATE" != "$LAST_LOGGED_STATE" ]; then
					log_msg "powerd state: $CURR_STATE"
					LAST_LOGGED_STATE="$CURR_STATE"
				fi
				if [ "$CURR_STATE" = "active" ]; then
					GOT_ACTIVE=1
					break
				fi
			fi

			case "$CURR_RTC" in
				[0-9]*)
					if [ "$CURR_RTC" -ge "$DEADLINE" ]; then
						break
					fi
					;;
			esac

			"$SLEEP_BIN" "$POLL_INTERVAL"
		done

		if [ "$GOT_ACTIVE" -ne 1 ]; then
			log_msg "failure: resume transition timeout (never reached active state within grace)"
			log_msg "failure: powerd suspend lifecycle incomplete"
			"$SLEEP_BIN" "$INTERVAL_SECONDS"
			continue
		fi

		AFTER=$(cat "$RTC_DIR/since_epoch" 2>/dev/null | tr -d '\r\n')

		case "$AFTER" in
			""|*[!0-9]*)
				log_msg "failure: invalid post-resume RTC time: $AFTER"
				"$SLEEP_BIN" 60
				;;
			*)
				ELAPSED=$((AFTER - BEFORE))
				log_msg "elapsed suspend time: ${ELAPSED}s"

				MIN_SLEEP=$((INTERVAL_SECONDS * 8 / 10))
				if [ "$MIN_SLEEP" -lt 60 ]; then
					MIN_SLEEP=60
				fi

				if [ "$ELAPSED" -lt "$MIN_SLEEP" ]; then
					log_msg "early wake: slept ${ELAPSED}s < min ${MIN_SLEEP}s"
					BACKOFF=$((INTERVAL_SECONDS - ELAPSED))
					if [ "$BACKOFF" -lt 60 ]; then
						BACKOFF=60
					fi
					log_msg "early wake backoff: ${BACKOFF}s"
					"$SLEEP_BIN" "$BACKOFF"
				else
					log_msg "resume confirmed (slept ${ELAPSED}s)"
				fi
				;;
		esac
		continue
	fi

	# Standard Awake Sleep Loop (when NATIVE_RTC_SCHEDULER != 1)
	if [ ! -x "$REFRESH_ONCE_SH" ]; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: missing $REFRESH_ONCE_SH" >&2
		exit 1
	fi
	if ! "$REFRESH_ONCE_SH"; then
		echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: refresh cycle failed" >&2
		exit 1
	fi

	SLEEP_MINUTES="${REFRESH_INTERVAL_MINUTES:-60}"
	case "$SLEEP_MINUTES" in
		5|10|15|30|60) ;;
		*) SLEEP_MINUTES=60 ;;
	esac
	SLEEP_SECONDS=$((SLEEP_MINUTES * 60))
	echo "$(date '+%Y-%m-%d %H:%M:%S') next wake cycle in ${SLEEP_MINUTES} minutes"
	"$SLEEP_BIN" "$SLEEP_SECONDS"
done

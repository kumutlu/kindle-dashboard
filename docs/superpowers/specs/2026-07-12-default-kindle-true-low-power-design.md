# Default Kindle True Low-Power Mode Design

## Goal

Replace the permanent refresh daemon on `default-kindle` (`192.168.68.119`) with a one-shot, powerd-managed wake, refresh, and genuine suspend cycle. The pilot must prove autonomous operation without changing any other Kindle or claiming a 30–60 day battery result from a short test.

## Scope and safety boundary

- The pilot applies only to device ID `default-kindle` at `192.168.68.119`.
- Existing legacy files remain present and unmodified during the pilot:
  - `/mnt/us/dashboard/start-dashboard.sh`
  - `/mnt/us/dashboard/refresh.sh`
  - `/mnt/us/dashboard/refresh-once.sh`
  - `/etc/upstart/kindle-dashboard.conf`
- Pilot files use separate `low-power-*` names.
- A full rollback snapshot is stored outside `/mnt/us/dashboard` before installation.
- One command must restore legacy mode.
- `DISABLE_LOW_POWER` and existing `NOAUTOSTART` markers prevent a low-power cycle from suspending the device.
- USBNetwork, Dropbear, and telnetd remain running for the first two 180-second cycles.
- Physical power-button recovery is reserved for a failed pilot.

## Current power blockers

The audited device currently has:

- `com.lab126.powerd preventScreenSaver=1`.
- `com.lab126.powerd state=active`.
- Wi-Fi enabled and connected continuously.
- `/mnt/us/dashboard/refresh.sh` running as a permanent `while true` daemon.
- `/bin/sleep` used as the refresh timer.
- `lab126_gui` stopped permanently.
- USBNetwork, Dropbear, and telnetd running continuously.

This state cannot enter normal Kindle suspend and therefore cannot provide true low-power operation.

## Wake mechanism

Use the firmware-native LIPC property:

```sh
lipc-set-prop com.lab126.powerd rtcWakeup "$INTERVAL_SECONDS"
```

Inspection of `/usr/bin/powerd` confirms that `rtcWakeup` is a delay in seconds after suspend. The firmware logs describe it as `Setting RTC wakeup to <n>s after suspend` and emit `EVENT_WAKEUP_FROM_SUSPEND` on resume.

Direct writes to `/sys/class/rtc/rtc1/wakealarm` and `/sys/power/state` are retained only as diagnostic fallbacks. They are not the primary pilot mechanism because bypassing powerd could desynchronize Kindle power-management state.

## Pilot components

### `/mnt/us/dashboard/low-power-refresh-once.sh`

A bounded one-shot refresh operation that:

1. Acquires a PID/lock directory without leaving a daemon.
2. Exits safely if `DISABLE_LOW_POWER` or `NOAUTOSTART` exists.
3. Records battery, powerd state, Wi-Fi state, and timestamps.
4. Enables Wi-Fi only when required.
5. Waits for connectivity for a bounded period.
6. Requests `/device/default-kindle/image.png` using saved ETag and Last-Modified validators.
7. Treats HTTP 304 as unchanged and performs no e-ink refresh.
8. Atomically installs a valid changed PNG and runs `/usr/sbin/eips` only for changed content.
9. Leaves no `wget`, `curl`, watchdog, or refresh subprocess behind.
10. In the first pilot, leaves USBNetwork, Dropbear, telnetd, and Wi-Fi policy unchanged after the request so SSH recovery remains possible.

### `/mnt/us/dashboard/low-power-cycle.sh`

The single cycle coordinator. It runs one refresh, validates every suspend prerequisite, schedules the next wake, and requests suspend. It contains no permanent loop and uses no shell sleep as its primary interval mechanism.

Immediately before suspend it must verify all of the following and abort suspend if any check fails:

- `DISABLE_LOW_POWER` is absent.
- `NOAUTOSTART` is absent.
- The configured interval is valid.
- `rtcWakeup` was accepted by powerd, with success recorded in the log.
- `/mnt/us/dashboard/low-power-wake-handler.sh` exists and is executable.
- `/mnt/us/default-kindle-low-power-rollback/rollback.sh` exists and is executable.
- No duplicate low-power cycle owns the lock.
- No `wget`, `curl`, or child refresh process remains.

It then clears `preventScreenSaver`, records the pre-suspend process and power state, and asks powerd to enter suspend. It never stops USBNetwork, Dropbear, or telnetd during the first pilot.

### `/mnt/us/dashboard/low-power-wake-handler.sh`

A short executable handler invoked by the existing Kindle `crond` after resume. The stock cron daemon remains frozen during suspend and resumes with the rest of userspace after an RTC wake. A pilot-only, clearly delimited entry in `/etc/crontab/root` runs the handler once per minute; the handler checks a persisted `next-cycle-due` value and exits immediately when no cycle is due. When due, it records resume evidence and launches exactly one new `low-power-cycle.sh`. It uses the same atomic lock to prevent duplicate wake jobs. This provides a resume trigger without a permanent dashboard daemon or shell sleep process.

### `/etc/upstart/default-kindle-low-power.conf`

A new pilot-only Upstart job, separate from the legacy hook. It starts the low-power cycle after boot only when low-power mode is explicitly enabled and neither disable marker exists. During installation, the legacy Upstart file remains available for rollback. The pilot activation step disables duplicate legacy autostart through a reversible marker/config operation, not deletion.

The Upstart job is responsible only for the first cycle after reboot. Subsequent RTC resumes are handled by the due-time-gated cron wake handler.

### `/mnt/us/default-kindle-low-power-rollback/rollback.sh`

One-command recovery that:

1. Creates `DISABLE_LOW_POWER` first.
2. Stops any low-power process.
3. Removes only the pilot Upstart job and pilot runtime locks.
4. Removes only the delimited pilot line from `/etc/crontab/root` and restores its snapshot.
5. Restores the snapshotted legacy Upstart configuration and script permissions.
6. Clears `preventScreenSaver` state as needed, then starts `/mnt/us/dashboard/start-dashboard.sh --manual`.
7. Never alters unrelated Kindle files.

The rollback script is installed and tested before low-power activation.

## State and logging

Pilot runtime data is isolated under `/mnt/us/dashboard/low-power-state/`:

- `etag`
- `last-modified`
- `cycle.lock/`
- `cycle-sequence`
- `last-http-status`
- `last-image-md5`
- `pilot-interval-seconds`
- `low-power.log`

Each cycle records:

- cycle number and wall-clock/monotonic timestamps
- battery percentage and charging state
- powerd state before refresh, before suspend, and after resume
- Wi-Fi state before and after refresh
- HTTP status and validator use
- whether an image was downloaded and rendered
- process list immediately before suspend
- RTC scheduling result
- suspend request and autonomous resume evidence

## Test sequence

### Phase 1: installation without activation

1. Capture a full rollback snapshot outside `/mnt/us/dashboard`.
2. Install pilot scripts under separate names.
3. Install and validate rollback.
4. Run shell syntax and prerequisite checks.
5. Confirm legacy mode still works and SSH remains reachable.

### Phase 2: first 180-second cycle

1. Record battery, powerd, Wi-Fi, process, and image baseline.
2. Schedule `rtcWakeup=180` through powerd.
3. Verify prerequisite checks immediately before suspend.
4. Enter genuine suspend.
5. Confirm SSH becomes temporarily unreachable.
6. Confirm autonomous wake without pressing the power button.
7. Confirm SSH returns and a refresh result is logged.
8. Confirm the device returns to genuine suspend.

### Phase 3: second consecutive 180-second cycle

Repeat Phase 2 without manual intervention. Both cycles must show autonomous wake, successful refresh/304 handling, and return to suspend. A screen remaining visible is not suspend evidence.

### Phase 4: rollback rehearsal

Run the one-command rollback, verify legacy daemon/kiosk operation, reboot once, and verify legacy autostart. Then reinstall/activate the already validated pilot scripts for the remaining tests.

### Phase 5: reboot persistence

Reboot with low-power mode enabled. Verify the new Upstart job runs one cycle, schedules RTC wake, suspends, wakes autonomously, refreshes, and suspends again.

### Phase 6: network-service experiment

Only after Phases 2–5 pass, run a separate controlled test that stops USBNetwork, Dropbear, and telnetd immediately before suspend. Determine whether those services block suspend and whether they restart reliably after wake. Keep physical recovery available. This experiment is not combined with the initial pilot.

### Phase 7: 60-minute schedule

Do not select 3600 seconds until:

- two consecutive 180-second autonomous cycles pass;
- rollback is rehearsed successfully;
- reboot persistence passes;
- the chosen network-service policy has a reliable recovery path.

## Suspend validation criteria

Genuine suspend requires combined evidence:

- powerd transitions away from `active` and logs readiness/suspend.
- SSH becomes unreachable during the suspended window.
- no permanent refresh loop or normal shell sleep process remains.
- RTC/powerd logs show the wake alarm and resume event.
- the device wakes without human input.
- cycle sequence and monotonic/wall-clock timestamps advance after resume.
- the next refresh completes, or receives HTTP 304 without rendering.
- the device returns to suspend.

## Battery measurement

The pilot records battery level and elapsed time at installation, each cycle, rollback rehearsal, and the end of the measured window. Short tests establish functional behavior only. They cannot support a 30–60 day battery-life claim. A later multi-day observation is required to estimate endurance and identify background-drain sources.

## Failure behavior

- Failure to schedule RTC wake aborts suspend and leaves SSH available.
- Missing/non-executable wake handler aborts suspend.
- Missing/non-executable rollback aborts suspend.
- Either disable marker aborts suspend.
- Network timeout retains the previous e-ink image, logs the error, schedules the next safe attempt only if all suspend prerequisites pass, and leaves no downloader process.
- Invalid downloaded content is discarded atomically.
- Duplicate lock ownership aborts the new cycle rather than starting a second job.
- If autonomous wake fails, physical power-button recovery followed by the rollback command restores legacy operation.

## Out of scope

- No changes to `kitchen-kindle`, `kindle-131`, or any other Kindle.
- No immediate rollout of the network-service shutdown policy.
- No claim of 30–60 day battery life from the short pilot.
- No changes to the existing server rendering pipeline or per-device image endpoint.

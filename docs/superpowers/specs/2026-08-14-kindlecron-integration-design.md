# KindleCron Integration Design

## Objective

Make KindleCron v0.2.0 the sole scheduler for Kindle dashboard refreshes. The dashboard owns one scheduler-independent refresh command; KindleCron owns cadence, RTC wake, deep-sleep handling, job timeouts, and daemon lifecycle.

This change is repository-only until the user reviews the exact diff and explicitly approves deployment to Kindle 4.

## Established root cause

The Aug 14 outage was caused by a strict network-readiness change in `refresh-once.sh`. It required both `cmState == CONNECTED` and an HTTP HEAD probe, then failed the cycle before its normal GET when those checks did not complete during KindleCron's wake window.

Physical evidence after restoring the previous readiness semantics proves the rollback fixed the refresh path:

- KindleCron ran `dashboard` at 14:29:05 and recorded exit 0 at 14:29:08.
- The image server returned HTTP 200 to Kindle 4 at 14:29:06.
- The downloaded and server SHA-256 values matched: `c2d7744317b0eb695b300bab2157c91c22f18bf00c7dbd26ef630d03ca94bb89`.
- `image.png` was atomically replaced.
- `eips -c`, `eips -f`, and `eips -g` each exited 0.
- `/mnt/us/dashboard/image.png` and `/mnt/us/linkss/screensavers/bg_ss00.png` ended with the same SHA-256.

The integration must retain the restored, permissive `wait_for_network || true` behavior. It must not reintroduce the failed `cmState`/HEAD readiness gate.

## Scheduler boundary

`/mnt/us/dashboard/refresh-once.sh` performs exactly one cycle:

1. Load device configuration.
2. Enable Wi-Fi and make the existing bounded best-effort wait.
3. Fetch runtime display configuration.
4. Fetch and validate the device-specific PNG.
5. Preserve the existing image on any download or validation failure.
6. Atomically replace `image.png` after successful validation.
7. Refresh the display with `eips` when image bytes changed.
8. Atomically reconcile `bg_ss00.png` with the current dashboard image.
9. Send best-effort device status.
10. Release the power/screen-saver hold and disable Wi-Fi through cleanup.
11. Exit 0 or 1.

It contains no loop, interval sleep, RTC access, wake-alarm access, suspend scheduling, watchdog behavior, or scheduler registration.

KindleCron exclusively owns scheduling. Production dashboard scripts must not use `rtcWakeup`, `rtcWakeup2`, `/sys/class/rtc`, `/sys/power/state`, `powerButton` suspend requests, or custom suspend/resume state machines.

## KindleCron dependency contract

The generated installer requires an executable `/mnt/us/kron/kron`. It does not download, copy, modify, or replace KindleCron.

Before changing scheduler state, the installer runs `/mnt/us/kron/kron version` and requires output identifying `KindleCron v0.2.0`. Missing, non-executable, failing, or mismatched binaries stop installation with a clear error and non-zero exit. Version verification happens before stopping legacy schedulers or rewriting dashboard scheduler files so dependency failure is fail-closed and non-destructive.

The installer relies on KindleCron's documented exit-code contract rather than parsing human-readable output for scheduler commands. Human-readable output is logged for diagnosis.

## Interval and job registration

The existing allowed dashboard refresh intervals remain `5`, `10`, `15`, `30`, and `60` minutes. The generated installer converts the configured value to KindleCron's fixed-interval syntax:

- `5` -> `every 5m`
- `10` -> `every 10m`
- `15` -> `every 15m`
- `30` -> `every 30m`
- `60` -> `every 1h`

The installer registers or replaces one job:

```sh
/mnt/us/kron/kron add dashboard -timeout 2m "every <interval>" /mnt/us/dashboard/refresh-once.sh
```

The `add` operation is naturally idempotent because KindleCron replaces a job with the same name. The installer confirms success with `kron list` and records the output in the integration log. Any registration failure stops installation with a non-zero exit.

## Legacy scheduler compatibility

The installer first validates KindleCron, writes the dashboard files, then uses the existing PID/cmdline-validated stop logic to terminate only genuine dashboard `watchdog.sh`, `dashboard_loop.sh`, or `refresh.sh` processes. It removes their stale PID files only when existing safety checks permit.

`NATIVE_RTC_SCHEDULER` and `LOW_POWER_MODE` are written as `0` for compatibility with existing configuration readers. They no longer activate production scheduler behavior.

Legacy entry points remain as compatibility shims:

- `refresh.sh` logs that scheduling is managed by KindleCron and performs one foreground `refresh-once.sh` invocation. It does not loop or schedule.
- `dashboard_loop.sh` delegates once to `refresh.sh` and exits.
- `watchdog.sh` logs that it is disabled and exits successfully.
- `start.sh` verifies KindleCron, ensures the daemon is running, and confirms the registered job; it does not start a dashboard loop.
- `stop.sh` stops only legacy dashboard scheduler processes. It does not stop KindleCron because the daemon may own jobs for other applications.

The old `/etc/upstart/dashboard.conf` autostart must be removed or replaced so it cannot resurrect the legacy watchdog/loop. The installer must not restart the Kindle framework.

## Persistent daemon startup

The verified device boot chain is KMC's existing `/var/local/kmc/system_patches/kmc.conf`, which invokes `/mnt/us/emergency.sh` at `framework_ready` when that file exists. The installer does not modify KMC system files.

It verifies that:

- `/var/local/kmc/system_patches/kmc.conf` exists;
- the file references `/mnt/us/emergency.sh`;
- `/mnt/us/kron/kron` has already passed the v0.2.0 dependency check.

It then writes an idempotent `/mnt/us/emergency.sh` that:

- logs boot invocation, dependency/version status, daemon status, startup success, and startup failure to `/mnt/us/kron/boot.log`;
- leaves an existing KindleCron daemon alone;
- starts `/mnt/us/kron/kron daemon` only when no matching daemon is running;
- checks after startup that exactly one matching daemon process exists;
- never starts `watchdog.sh`, `refresh.sh`, or `dashboard_loop.sh`;
- exits non-zero on dependency or daemon-start failure.

The installer invokes the same startup helper once after job registration and verifies daemon/job status. Re-running the installer must not create a second daemon or duplicate job.

## Logging and failure behavior

Scheduler integration actions log to `/mnt/us/dashboard/kindlecron-install.log`. Each log line is timestamped and covers:

- dependency path and accepted version;
- legacy scheduler shutdown results;
- chosen interval and exact job registration result;
- KMC boot-hook verification;
- startup-helper installation;
- daemon already-running or newly-started status;
- final `kron list` output;
- clear fatal errors.

The boot helper logs separately to `/mnt/us/kron/boot.log`, and KindleCron retains its existing `/mnt/us/kron/kron.log` and `/mnt/us/kron/state/dashboard.log` files.

Failures do not remove the current dashboard image. A missing/mismatched KindleCron binary causes no legacy-scheduler shutdown. A later integration failure exits non-zero and leaves diagnostics; it does not fall back to the legacy scheduler, because doing so would silently recreate two competing architectures.

## Installer structure

The scheduler integration is implemented as a focused bundled shell script, `kindle_scripts/install-kindlecron.sh`, embedded by `kindle_installer_script()` beside `refresh-once.sh`. This keeps Kindle-specific process/version/job/startup behavior independently testable instead of expanding the generated Python string further.

The Python installer generator remains responsible for:

- device-specific URLs and credentials;
- writing `device.env` and the existing dashboard scripts;
- embedding the bundled integration script;
- invoking it only after files have been written and made executable;
- propagating its failure instead of swallowing it.

## Tests

Regression tests use real generated shell scripts with temporary fake files/process metadata and a fake `kron` executable where external Kindle services cannot run locally.

Required coverage:

- missing KindleCron fails before legacy shutdown or registration;
- non-v0.2.0 KindleCron fails before legacy shutdown or registration;
- v0.2.0 is accepted;
- every supported refresh interval maps to the expected KindleCron schedule;
- invalid intervals fall back to 60 minutes consistently;
- `dashboard` is registered/replaced with timeout `2m` and `refresh-once.sh` as its only command;
- repeat installation leaves one daemon and one dashboard job;
- legacy watchdog/loop/refresh processes are stopped with existing identity safeguards and are not restarted;
- the KMC boot hook is verified without modifying `kmc.conf`;
- `emergency.sh` is installed and starts KindleCron only when needed;
- startup, daemon, registration, and failure messages are logged;
- `refresh-once.sh` contains no scheduler, RTC, suspend, watchdog, interval-sleep, or looping primitives;
- failed downloads preserve the existing image and overlay;
- successful refresh atomically updates both image and overlay;
- unchanged/304 responses still repair a stale overlay;
- the generated installer contains the expected KindleCron integration and no legacy scheduler autostart.

The relevant Kindle script suite and broader affected settings tests run before completion, followed by `git diff --check`.

## Physical validation gate

No deployment occurs as part of implementation. After tests pass, the user receives:

- the exact repository diff;
- commit list;
- test commands and results;
- a one-variable-at-a-time deployment and rollback plan.

Deployment requires a new explicit approval. Physical validation then covers multiple hourly sleep/wake cycles, image/overlay hash equality, return to deep sleep, reboot persistence, exactly one KindleCron daemon, no legacy scheduler processes, and battery behavior over a meaningful observation period.

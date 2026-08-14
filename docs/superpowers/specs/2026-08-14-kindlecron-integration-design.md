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
/mnt/us/kron/kron add -timeout 2m dashboard "every <interval>" /mnt/us/dashboard/refresh-once.sh
```

This exact argument ordering is physically verified on Kindle 4 and follows KindleCron's documented `add [-timeout DUR] NAME SCHEDULE COMMAND...` grammar. A regression test must assert the complete ordered argument vector. The `add` operation is naturally idempotent because KindleCron replaces a job with the same name. The installer confirms success with `kron list` and records the output in the integration log. Any registration failure stops installation with a non-zero exit.

## Legacy scheduler compatibility

The installer first validates KindleCron, writes the dashboard files, then uses the existing PID/cmdline-validated stop logic to terminate only genuine dashboard `watchdog.sh`, `dashboard_loop.sh`, or `refresh.sh` processes. It removes their stale PID files only when existing safety checks permit.

`NATIVE_RTC_SCHEDULER` and `LOW_POWER_MODE` are written as `0` for compatibility with existing configuration readers. They no longer activate production scheduler behavior.

Legacy entry points remain as compatibility shims:

- `refresh.sh` logs that scheduling is managed by KindleCron and performs one foreground `refresh-once.sh` invocation. It does not loop or schedule.
- `dashboard_loop.sh` delegates once to `refresh.sh` and exits.
- `watchdog.sh` logs that it is disabled and exits successfully.
- `start.sh` is an explicit manual entry point that verifies KindleCron, ensures the daemon is running, and confirms the registered job; it does not install itself into any boot chain or start a dashboard loop.
- `stop.sh` stops only legacy dashboard scheduler processes. It does not stop KindleCron because the daemon may own jobs for other applications.

The old `/etc/upstart/dashboard.conf` autostart must be removed or replaced so it cannot resurrect the legacy watchdog/loop. The installer must not restart the Kindle framework.

## Persistent daemon startup: unresolved design item

The following physical facts are verified:

- `/var/local/kmc/system_patches/kmc.conf` references and executes `/mnt/us/emergency.sh` at `framework_ready` when that file is present.
- Manually executing the temporary `/mnt/us/emergency.sh` helper starts one KindleCron daemon.
- Repeated manual invocation of that helper does not create a second daemon.

The following is not verified: a genuine Kindle reboot executes that helper, produces a new post-boot log entry, starts a new daemon process, and restores autonomous scheduling. Later diagnostics did not show the required fresh `boot.log` evidence after the supposed reboot.

`/mnt/us/emergency.sh` is KMC's emergency/recovery hook. It may contain user recovery logic or belong to another workflow. The Kindle Dashboard installer must not create, replace, append to, chmod, rename, delete, or otherwise take ownership of that path. It must not modify `/var/local/kmc/system_patches/kmc.conf`.

Therefore persistent startup is deliberately outside the current repository implementation scope. The installer reports that automatic KindleCron startup is not yet configured by Kindle Dashboard and must not claim reboot persistence. `start.sh` is an idempotent daemon-start/status command that can be invoked directly without installing a boot hook. The installer invokes it once after successful job registration to make the current installation operational, then verifies one daemon and the registered job. Re-running the installer must not create a second daemon or duplicate job; this current-session startup does not imply reboot persistence.

A production boot mechanism requires a separate evidence-gathering and design cycle. That cycle must first identify a dedicated application-owned KMC/Upstart/KUAL-compatible startup path, or define safe composition semantics that provably preserve any existing recovery hook byte-for-byte and provide an exact rollback. No boot integration may be implemented until ownership, invocation timing, failure isolation, and real-reboot behavior are established and approved.

## Logging and failure behavior

Scheduler integration actions log to `/mnt/us/dashboard/kindlecron-install.log`. Each log line is timestamped and covers:

- dependency path and accepted version;
- legacy scheduler shutdown results;
- chosen interval and exact job registration result;
- explicit notice that persistent boot startup remains unconfigured;
- manual `start.sh` daemon already-running or newly-started status;
- final `kron list` output;
- clear fatal errors.

KindleCron retains its existing `/mnt/us/kron/kron.log` and `/mnt/us/kron/state/dashboard.log` files. The installer must not use an existing `/mnt/us/kron/boot.log` as proof of reboot persistence unless a genuine reboot produces a new timestamp tied to a new daemon process/start time.

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
- the exact `kron add -timeout 2m dashboard "every <interval>" /mnt/us/dashboard/refresh-once.sh` argument order is used;
- neither `/mnt/us/emergency.sh` nor `kmc.conf` is created, overwritten, appended, renamed, chmodded, deleted, or otherwise modified;
- the generated installer explicitly reports that persistent boot startup is unresolved and unconfigured;
- manual daemon startup/status, registration, and failure messages are logged;
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

Deployment requires a new explicit approval. Refresh-path validation then covers multiple hourly sleep/wake cycles, image/overlay hash equality, return to deep sleep, exactly one KindleCron daemon, no legacy scheduler processes, and battery behavior over a meaningful observation period.

Reboot persistence remains a separate unresolved gate. It cannot be marked complete until a dedicated startup design is approved and a genuine reboot proves all of the following:

- a new post-boot startup-log timestamp;
- a new KindleCron daemon PID/process start time relative to the pre-reboot daemon;
- exactly one KindleCron daemon;
- the `dashboard` job remains registered with the configured interval and two-minute timeout;
- no `watchdog.sh`, `refresh.sh`, or `dashboard_loop.sh` processes;
- successful autonomous sleep, RTC wake, refresh, overlay synchronization, and return-to-sleep cycles after that reboot.

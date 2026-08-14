# Default Kindle True Low-Power Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy and validate a genuine suspend-between-refresh pilot on `default-kindle` (`192.168.68.119`) without modifying another Kindle or overwriting the legacy dashboard path.

**Architecture:** Repository code generates a separate set of `low-power-*` scripts and a pilot Upstart definition. On the Kindle, `powerd rtcWakeup` is the only wake source; stock cron only notices that a persisted due time has passed after userspace resumes. Each cycle is one-shot, conditionally fetches the per-device PNG, refreshes e-ink only when content changes, validates recovery prerequisites, schedules the next RTC wake, and enters suspend.

**Tech Stack:** Python 3 `unittest`, POSIX/BusyBox `sh`, Kindle LIPC/powerd, BusyBox `crond`, Upstart, HTTP ETag/Last-Modified, `wget`/curl, `/usr/sbin/eips`, SSH/SCP.

## Global Constraints

- Scope is only `default-kindle` at `192.168.68.119`.
- Do not alter `kitchen-kindle`, `kindle-131`, or any other device.
- Preserve `/mnt/us/dashboard/start-dashboard.sh`, `/mnt/us/dashboard/refresh.sh`, `/mnt/us/dashboard/refresh-once.sh`, and `/etc/upstart/kindle-dashboard.conf` unchanged.
- Install pilot files with separate `low-power-*` names.
- Use `com.lab126.powerd rtcWakeup` as the real wake source.
- Cron may perform due-time checks after resume but must not act as the wake source.
- Do not stop USBNetwork, Dropbear, or telnetd during the first two 180-second cycles.
- Require two consecutive autonomous wake → refresh → suspend cycles before changing to 3600 seconds.
- Rehearse and validate rollback before changing to 3600 seconds.
- Do not infer 30–60 day battery life from the short pilot.
- No production code may be written before its failing test is run and verified.

## File map

### Repository files

- Create `kindle_low_power.py`: constants, shell-template rendering, and pilot bundle generation for one selected Kindle.
- Create `test_kindle_low_power.py`: unit tests for generated scripts, safety gates, conditional HTTP behavior, device scoping, cron due-time semantics, and interval transition.
- Modify `settings_server.py`: expose an explicit server-side bundle-generation/deployment helper for a selected device without changing existing legacy installers or normal push behavior.
- Modify `test_settings_server.py`: regression tests proving only `default-kindle` is selected and legacy installer output remains unchanged.
- Create `docs/default-kindle-low-power-pilot.md`: operator instructions, evidence collection, activation, rollback, reboot validation, and later service optimization.

### Kindle pilot files

- Create `/mnt/us/dashboard/low-power-refresh-once.sh`.
- Create `/mnt/us/dashboard/low-power-cycle.sh`.
- Create `/mnt/us/dashboard/low-power-wake-handler.sh`.
- Create `/mnt/us/dashboard/low-power-manual-start.sh`.
- Create `/mnt/us/dashboard/low-power-state/` and its state/log files.
- Create `/etc/upstart/default-kindle-low-power.conf`.
- Add one clearly delimited pilot line to `/etc/crontab/root`.
- Create `/mnt/us/default-kindle-low-power-rollback/rollback.sh` and a timestamped snapshot outside `/mnt/us/dashboard`.

---

## Phase 1: Repository changes and generated-script contract

### Objective

Implement and test generation of a default-kindle-only pilot bundle while proving that the existing legacy rendering, installer, and push paths remain unchanged.

### Files affected

- Create: `kindle_low_power.py`
- Create: `test_kindle_low_power.py`
- Modify: `settings_server.py`
- Modify: `test_settings_server.py`
- Create: `docs/default-kindle-low-power-pilot.md`

### Interfaces

- Produce `render_low_power_bundle(device, config, server_host, image_port) -> dict[str, str]`.
- Produce `validate_low_power_target(device) -> None`, accepting only device ID `default-kindle` and Kindle device types supported by the pilot.
- Produce `build_low_power_deployment(device, config, server_host, image_port) -> LowPowerDeployment`, containing file paths, contents, modes, cron line, and Upstart content without performing remote writes.
- Preserve existing `kindle_installer_script(...)`, `render_device(...)`, normal device push endpoints, and Special Events push behavior.

### Implementation steps

- [ ] **Step 1: Add a failing device-scope test**

  Add tests asserting that `default-kindle` is accepted and `kitchen-kindle` plus `kindle-131` are rejected by `validate_low_power_target`.

  Run:

  ```bash
  python3 -m unittest test_kindle_low_power.LowPowerTargetTests -v
  ```

  Expected: FAIL because `kindle_low_power` and `validate_low_power_target` do not exist.

- [ ] **Step 2: Implement minimal target validation**

  Create `kindle_low_power.py` with target validation based on the registry device ID, not host guessing.

  Re-run the Phase 1 target tests and require PASS.

- [ ] **Step 3: Add failing generated-file contract tests**

  Assert that the bundle contains only separate pilot paths, contains no write operation targeting the three active legacy scripts, and references exactly:

  ```text
  http://<server>:8765/device/default-kindle/image.png
  ```

  Assert that the cycle script contains `rtcWakeup`, has no `while true`, and has no interval `/bin/sleep` loop.

  Run:

  ```bash
  python3 -m unittest test_kindle_low_power.LowPowerBundleTests -v
  ```

  Expected: FAIL because bundle rendering is not implemented.

- [ ] **Step 4: Implement minimal shell-template rendering**

  Generate the four pilot scripts, Upstart job, cron line, and rollback script. Keep shell content POSIX/BusyBox-compatible and all configurable values shell-quoted.

- [ ] **Step 5: Add failing suspend-prerequisite tests**

  Assert that `low-power-cycle.sh` aborts before suspend unless all checks pass:

  ```text
  rtcWakeup command success
  executable low-power-wake-handler.sh
  executable rollback.sh
  no DISABLE_LOW_POWER marker
  no NOAUTOSTART marker
  valid interval
  exclusive cycle lock
  no downloader child process
  ```

  Run the test class and verify the expected failures before implementation.

- [ ] **Step 6: Implement the prerequisite gate**

  Require each prerequisite to emit a machine-readable log record. Any failed prerequisite exits without requesting suspend and leaves network recovery available.

- [ ] **Step 7: Add failing conditional-request tests**

  Verify generated `low-power-refresh-once.sh`:

  - sends saved ETag and Last-Modified validators;
  - treats 304 as unchanged;
  - does not call eips on 304;
  - validates a changed non-empty PNG before atomic replacement;
  - leaves the old image untouched on failure;
  - cleans temporary files and locks through traps.

- [ ] **Step 8: Implement bounded conditional refresh**

  Use finite network/connect timeouts. Record HTTP status and validator updates under `low-power-state`. Do not create retry loops without a fixed maximum.

- [ ] **Step 9: Add failing cron due-time tests**

  Assert that the cron entry only invokes `low-power-wake-handler.sh`, and that the handler exits unless `next-cycle-due` is reached. Assert that it cannot schedule a wake itself; only `low-power-cycle.sh` may call `rtcWakeup`.

- [ ] **Step 10: Implement due-time-gated wake handling**

  The handler records resume evidence, obtains the shared atomic lock, and starts one cycle. Cron remains only a post-resume detector.

- [ ] **Step 11: Add failing settings-server regression tests**

  Verify that generating a low-power deployment requires an explicit `default-kindle` selection and that existing installer output and normal push endpoints are byte/behavior compatible.

  Run:

  ```bash
  python3 -m unittest test_settings_server.py test_kindle_low_power.py -v
  ```

  Expected: the new integration tests fail before the helper is wired in; existing regression tests continue to pass.

- [ ] **Step 12: Wire in the explicit deployment helper**

  Add a non-default helper path to `settings_server.py`; do not make low-power installation part of normal device creation or push.

- [ ] **Step 13: Run repository verification**

  ```bash
  python3 -m unittest test_kindle_low_power.py test_settings_server.py test_device_renderer.py test_special_events.py -v
  ```

  Expected: all tests PASS with zero failures and errors.

### Validation evidence

- Red/green test output for each behavior.
- Generated bundle manifest showing only `default-kindle` paths.
- Search output proving no generated operation overwrites legacy scripts.
- Full regression test output.
- Git diff reviewed for unrelated device changes.

### Rollback procedure

Before deployment, repository rollback is:

```bash
git revert <phase-1-implementation-commit>
python3 -m unittest test_settings_server.py test_device_renderer.py test_special_events.py -v
```

No Kindle rollback is required because Phase 1 performs no remote writes.

### Failure criteria

- Any generated path targets a legacy active script.
- Any device other than `default-kindle` is accepted.
- Any permanent loop or interval sleep is present.
- Cron contains wake scheduling logic.
- Existing rendering, push, Special Events, or installer tests regress.
- Conditional HTTP failure can replace the current image.

---

## Phase 2: Kindle-side installation without activation

### Objective

Install pilot files, full rollback assets, and syntax-checked integration without activating suspend or changing the running legacy dashboard.

### Files affected

- Create the four `/mnt/us/dashboard/low-power-*.sh` scripts.
- Create `/mnt/us/dashboard/low-power-state/`.
- Create `/mnt/us/default-kindle-low-power-rollback-<timestamp>/`.
- Prepare but do not activate `/etc/upstart/default-kindle-low-power.conf`.
- Prepare but do not activate the delimited cron entry.
- Do not modify active legacy scripts or their Upstart file.

### Implementation steps

- [ ] **Step 1: Capture pre-install evidence**

  Record hashes, modes, timestamps, process list, powerd properties, Wi-Fi state, USBNetwork/Dropbear/telnetd status, battery, legacy log tail, cron file, and Upstart files.

- [ ] **Step 2: Create full rollback snapshot outside the dashboard directory**

  Snapshot `/mnt/us/dashboard`, `/etc/upstart/kindle-dashboard.conf`, `/etc/crontab/root`, and relevant mode/ownership metadata under the timestamped rollback directory.

- [ ] **Step 3: Install rollback first**

  Install `rollback.sh`, make it executable, and run its non-mutating `--check` mode. The check must verify every source needed to restore legacy mode.

- [ ] **Step 4: Transfer pilot files atomically**

  Copy to temporary names, verify hashes, set expected modes, then rename only the new pilot files into place.

- [ ] **Step 5: Validate scripts without running suspend**

  Run BusyBox shell syntax checks where supported and each script’s `--check`/dry-run mode. Confirm the rendered endpoint is `/device/default-kindle/image.png`.

- [ ] **Step 6: Prove legacy operation remains unchanged**

  Compare post-install legacy hashes with the baseline, confirm exactly one legacy refresh daemon, confirm current image hash matches the server image, and verify SSH remains reachable.

### Validation evidence

- Snapshot directory listing and hashes.
- `rollback.sh --check` success output.
- Local-versus-remote pilot file hashes.
- Before/after legacy hashes identical.
- Process list showing the existing legacy loop unchanged.
- No suspend/resume log entries caused by installation.

### Rollback procedure

Remove only unactivated pilot files and the inactive pilot integration files, then confirm legacy hashes and daemon state. The snapshot remains retained.

### Failure criteria

- Any legacy hash, mode, or content changes unexpectedly.
- Rollback prerequisites are incomplete.
- Pilot shell validation fails.
- The wrong device endpoint appears.
- SSH becomes unreliable.
- Any suspend request occurs during installation.

---

## Phase 3: First and second 180-second autonomous pilot cycles

### Objective

Prove two consecutive autonomous wake → refresh → genuine suspend cycles at 180 seconds while keeping USBNetwork, Dropbear, and telnetd running.

### Files affected

- Activate `/etc/upstart/default-kindle-low-power.conf`.
- Add the delimited handler line to `/etc/crontab/root`.
- Create/update state files under `/mnt/us/dashboard/low-power-state/`.
- Create reversible marker/config state that prevents duplicate legacy autostart without deleting its files.

### Implementation steps

- [ ] **Step 1: Record activation baseline**

  Capture battery percentage, charging state, wall time, monotonic time, image hash, server image hash, Wi-Fi state, powerd state, process list, and service status.

- [ ] **Step 2: Activate pilot with interval 180**

  Persist `180` in `pilot-interval-seconds`, enable the pilot Upstart/cron integration, and stop only the legacy refresh process. Do not stop USBNetwork, Dropbear, telnetd, or Wi-Fi.

- [ ] **Step 3: Validate mandatory pre-suspend gate**

  Require log evidence that:

  - `DISABLE_LOW_POWER` and `NOAUTOSTART` were checked;
  - rollback exists and is executable;
  - wake handler exists and is executable;
  - `rtcWakeup=180` returned success;
  - no duplicate lock or downloader remains.

  Abort Phase 3 if any line is missing.

- [ ] **Step 4: Prove first genuine suspend**

  Confirm powerd suspend logs/state and temporary SSH unreachability. Do not use the persistent e-ink image as evidence.

- [ ] **Step 5: Prove first autonomous wake and refresh**

  Without touching the power button, confirm SSH returns, the cycle sequence increments, powerd logs RTC resume, and refresh records either a valid changed-image render or HTTP 304 without eips.

- [ ] **Step 6: Prove return to suspend**

  Confirm SSH becomes unavailable again and the next `rtcWakeup=180` was accepted.

- [ ] **Step 7: Prove the second consecutive cycle**

  Repeat wake, refresh/304, and suspend verification without manual intervention. Require cycle sequence to advance again.

- [ ] **Step 8: Collect post-cycle evidence**

  Capture battery level, elapsed time, powerd/RTC logs, HTTP logs, refresh logs, Wi-Fi state samples, and the last pre-suspend process list.

### Validation evidence

- Two distinct cycle IDs and timestamps.
- Two `rtcWakeup=180` success records.
- Two autonomous resume records from powerd/RTC evidence.
- SSH unreachable during both suspend windows and reachable after both wakes.
- Two refresh outcomes, including image hash/render evidence when changed or 304/no-eips evidence when unchanged.
- Pre-suspend process lists with no permanent refresh loop, shell sleep, wget, curl, or watchdog child.
- USBNetwork, Dropbear, and telnetd remain running for this phase.

### Rollback procedure

If the device remains reachable, run:

```sh
/mnt/us/default-kindle-low-power-rollback/rollback.sh
```

If autonomous wake fails, physically wake once, SSH in, and run the same command. Verify legacy kiosk mode and one legacy `refresh.sh` process.

### Failure criteria

- RTC scheduling cannot be proved.
- Device does not enter genuine suspend.
- Either wake requires human intervention.
- SSH does not return within the bounded recovery window.
- Refresh fails or corrupts the image.
- Device does not return to suspend.
- A permanent dashboard loop or downloader remains.
- Any network service is stopped during the initial pilot.
- Fewer than two consecutive autonomous cycles pass.

---

## Phase 4: Rollback rehearsal

### Objective

Prove that one command restores the untouched legacy dashboard before any 60-minute schedule is allowed.

### Files affected

- Execute `/mnt/us/default-kindle-low-power-rollback/rollback.sh`.
- Remove/disable only pilot Upstart and cron integration.
- Restore snapshot metadata where required.
- Leave pilot files available for reactivation unless the rollback design explicitly archives them.

### Implementation steps

- [ ] **Step 1: Run rollback while SSH is available**

  Record command output and exit status.

- [ ] **Step 2: Verify legacy state**

  Confirm original legacy hashes, executable modes, one legacy refresh process, GUI stopped, kiosk image current, `preventScreenSaver=1`, and pilot cycle absent.

- [ ] **Step 3: Reboot in legacy mode**

  Confirm `/etc/upstart/kindle-dashboard.conf` starts the legacy dashboard after reboot and SSH key recovery remains available.

- [ ] **Step 4: Reinstall/reactivate validated pilot integration**

  Reuse the already hash-verified pilot bundle, set interval back to 180, and run one additional autonomous cycle before proceeding.

### Validation evidence

- Rollback exit status zero.
- Legacy hashes match Phase 2 baseline.
- Pilot cron line/job inactive after rollback.
- Reboot produces exactly one legacy refresh process and current image.
- Reactivated pilot completes one autonomous 180-second cycle.

### Rollback procedure

This phase is itself the rollback. If rollback validation fails, keep `DISABLE_LOW_POWER` present, do not suspend, restore directly from the timestamped snapshot, and start legacy mode manually.

### Failure criteria

- Rollback needs more than the documented single command.
- Legacy hashes or startup behavior differ from baseline.
- Pilot process/job remains active.
- Reboot does not restore legacy mode.
- Reactivation cannot reproduce a successful 180-second cycle.

---

## Phase 5: Low-power reboot persistence test

### Objective

Prove that reboot initiates one low-power cycle and that subsequent wake cycles remain autonomous.

### Files affected

- `/etc/upstart/default-kindle-low-power.conf`
- `/etc/crontab/root` pilot handler line
- `/mnt/us/dashboard/low-power-state/*`

### Implementation steps

- [ ] **Step 1: Confirm pilot enabled at 180 seconds**

  Verify markers, interval, rollback, handler, and integration hashes.

- [ ] **Step 2: Reboot once**

  Record shutdown and boot timestamps. Do not manually launch a low-power script after boot.

- [ ] **Step 3: Verify first boot cycle**

  Confirm Upstart launched exactly one cycle, refresh completed, RTC wake was scheduled, and the device suspended.

- [ ] **Step 4: Verify post-reboot autonomous wake**

  Confirm autonomous wake, SSH return, cron due-time handler invocation, next refresh/304 result, and return to suspend.

### Validation evidence

- New boot ID/uptime.
- One Upstart-started cycle after boot.
- No duplicate handler/cycle.
- RTC wake and resume log evidence.
- Current image or valid 304 behavior.
- Return to genuine suspend.

### Rollback procedure

Physically wake only if required, then run the single rollback command and verify legacy reboot behavior.

### Failure criteria

- Low-power cycle needs manual launch after reboot.
- Legacy and pilot jobs both start.
- Duplicate cycles occur.
- Autonomous post-reboot wake fails.
- Device stays active after refresh.

---

## Phase 6: Production transition to a 60-minute interval

### Objective

Change only the validated pilot interval from 180 to 3600 seconds after all safety gates pass.

### Files affected

- Update `/mnt/us/dashboard/low-power-state/pilot-interval-seconds` from `180` to `3600`.
- Append transition evidence to `low-power.log`.
- Do not modify script content, legacy files, cron cadence, or wake mechanism.

### Entry gates

- Two consecutive 180-second autonomous cycles passed.
- Rollback rehearsal and legacy reboot test passed.
- Pilot reactivation passed.
- Low-power reboot persistence passed.
- No unresolved duplicate-process, image, network, or RTC issue remains.

### Implementation steps

- [ ] **Step 1: Record final short-pilot evidence and battery delta**

  State explicitly that the delta is functional evidence, not a long-term endurance estimate.

- [ ] **Step 2: Atomically set interval to 3600**

  Validate numeric bounds and record the change.

- [ ] **Step 3: Verify one 60-minute cycle**

  Confirm `rtcWakeup=3600`, genuine suspend, autonomous wake, refresh/304, and return to suspend.

- [ ] **Step 4: Begin multi-day measurement**

  Record battery at consistent times, number of wakes, changed versus 304 responses, Wi-Fi connection duration, and unexpected resumes.

### Validation evidence

- Entry-gate checklist with references to prior logs.
- Atomic state-file value `3600`.
- `rtcWakeup=3600` success record.
- One complete autonomous hourly cycle.
- Multi-day measurement log initialized.

### Rollback procedure

Set `DISABLE_LOW_POWER`, run the single rollback command, and verify legacy mode. If only the interval is problematic and suspend remains safe, atomically return the pilot state to `180` for diagnosis before another production attempt.

### Failure criteria

- Any entry gate is incomplete.
- RTC schedules a value other than 3600.
- Device fails to wake or return to suspend.
- Refresh or conditional request behavior regresses.
- A short pilot is presented as proof of 30–60 day battery life.

---

## Phase 7: Later network-service optimization

### Objective

After the core 60-minute architecture is stable, determine whether stopping USBNetwork, Dropbear, telnetd, or Wi-Fi before suspend materially improves suspend behavior or battery consumption without making recovery unreliable.

### Files affected

- Potentially modify `kindle_low_power.py` templates and `test_kindle_low_power.py` in a new, separately reviewed change.
- Potentially update `/mnt/us/dashboard/low-power-cycle.sh` through a new pilot bundle version.
- Do not modify the already validated version in place without snapshot and rollback.

### Implementation steps

- [ ] **Step 1: Measure baseline with services retained**

  Collect multi-day battery, suspend duration, wake count, Wi-Fi-on duration, and unexpected wake evidence.

- [ ] **Step 2: Add failing service-policy tests**

  Require service shutdown only after RTC scheduling and prerequisite validation. Require deterministic service restoration after resume and a marker that bypasses shutdown for recovery.

- [ ] **Step 3: Test Wi-Fi-only shutdown first**

  Keep USBNetwork and Dropbear available, disable Wi-Fi immediately before suspend if safe, and verify autonomous resume plus bounded reconnection.

- [ ] **Step 4: Test Dropbear/telnetd/USBNetwork separately**

  Change one service policy at a time. Use a short interval, physical recovery readiness, and rollback after each experiment.

- [ ] **Step 5: Select the minimum safe service set**

  Adopt only changes with measurable benefit and reliable automatic restoration.

### Validation evidence

- Before/after measured battery and suspend evidence over comparable windows.
- Service state before suspend and after resume.
- Autonomous wake and network restoration logs.
- SSH recovery test after every service-policy change.
- No unexplained background process remains.

### Rollback procedure

Use `DISABLE_LOW_POWER` and the single rollback command. Restore the last validated pilot bundle and retain all experiment logs for comparison.

### Failure criteria

- A service change is combined with another variable.
- SSH recovery does not return automatically.
- Service shutdown happens before RTC/prerequisite validation.
- Battery benefit is not measurable.
- Suspend or refresh reliability declines.

---

## Phase 8: Final verification and deployment report

### Objective

Produce an evidence-backed report without overstating battery-life results.

### Files affected

- Create `docs/default-kindle-low-power-deployment-report.md` after implementation.
- Do not modify Kindle runtime during report generation.

### Implementation steps

- [ ] **Step 1: Run the full repository test suite required by the changed files**

  ```bash
  python3 -m unittest test_kindle_low_power.py test_settings_server.py test_device_renderer.py test_special_events.py -v
  ```

- [ ] **Step 2: Audit device scope**

  Compare SSH/deployment logs and prove no command targeted another Kindle IP or device ID.

- [ ] **Step 3: Assemble validation evidence**

  Include HTTP logs, low-power logs, powerd/RTC evidence, process lists, Wi-Fi/service states, image hashes, two short cycles, rollback rehearsal, reboot test, and hourly cycle.

- [ ] **Step 4: Report battery results accurately**

  Separate measured percentage/time data from projections. State that 30–60 day endurance requires a multi-day observation and remains unproven until that evidence exists.

### Validation evidence

- Full test command exits zero with no failures/errors.
- Device scope audit names only `default-kindle` and `192.168.68.119`.
- Report contains exact rollback command and retained snapshot path.
- Every success claim cites a timestamped log or command result.

### Rollback procedure

The report does not modify runtime. If the deployed state is not acceptable, invoke the already rehearsed single-command legacy rollback.

### Failure criteria

- Missing evidence for any required validation.
- Any other Kindle was modified.
- Runtime success is inferred from the screen alone.
- Battery-life claims exceed measured evidence.
- Rollback instructions are incomplete or untested.

## Commit strategy for implementation

Use small, reviewable commits after each independently passing deliverable:

1. `test: define default Kindle low-power safety contract`
2. `feat: generate default Kindle low-power pilot bundle`
3. `test: protect legacy installer and device scope`
4. `docs: add default Kindle low-power pilot operations`
5. Deployment evidence and report commits only after the corresponding live validation succeeds.

Do not combine the planning commit with implementation commits.

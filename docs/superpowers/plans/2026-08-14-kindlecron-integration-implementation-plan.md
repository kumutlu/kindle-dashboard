# KindleCron Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make externally installed KindleCron v0.2.0 the sole Kindle dashboard scheduler while keeping `refresh-once.sh` as one safe, scheduler-independent refresh cycle.

**Architecture:** Add one POSIX shell integration command that validates KindleCron, disables legacy dashboard schedulers, registers the single `dashboard` job, and starts/verifies the daemon for the current session. Keep device-specific generation in `settings_server.py`; replace old scheduler entry points with non-looping compatibility shims. Do not install or claim a persistent boot mechanism.

**Tech Stack:** Python 3.11 `unittest`, POSIX/BusyBox `sh`, KindleCron v0.2.0 CLI, Kindle LIPC/eips, generated shell installer.

## Global Constraints

- Require executable `/mnt/us/kron/kron` reporting `KindleCron v0.2.0`; fail before legacy shutdown when missing or mismatched.
- Never download, copy, replace, or modify KindleCron.
- Register with this exact argument order: `/mnt/us/kron/kron add -timeout 2m dashboard "every <interval>" /mnt/us/dashboard/refresh-once.sh`.
- Map `5`, `10`, `15`, `30`, and `60` minutes to `every 5m`, `every 10m`, `every 15m`, `every 30m`, and `every 1h`; invalid values fall back to `60`.
- KindleCron is the only scheduler. No production custom RTC, wake-alarm, suspend, watchdog, or looping scheduler path remains.
- Preserve `wait_for_network || true`; do not add `cmState` or HTTP HEAD readiness gates.
- Preserve atomic image replacement and `bg_ss00.png` reconciliation.
- Write `LOW_POWER_MODE="0"` and `NATIVE_RTC_SCHEDULER="0"` for compatibility only.
- Never modify `/mnt/us/emergency.sh` or `/var/local/kmc/system_patches/kmc.conf` in any way.
- Persistent reboot startup is unresolved and out of scope. Current-session startup must not claim persistence.
- Do not restart the Kindle framework or deploy to a physical Kindle.

## File Map

- Create `kindle_scripts/install-kindlecron.sh`: validation, interval mapping, legacy shutdown, job registration, current-session daemon status/start, and logging.
- Modify `kindle_scripts/refresh.sh`: one-cycle compatibility shim.
- Modify `device_registry.py`: retire the native scheduler capability while accepting old stored data.
- Modify `settings_server.py`: embed/invoke the integration, generate disabled shims, force compatibility flags to zero, and remove legacy autostart generation.
- Modify `test_kindle_scripts.py`: test-first integration, safety, generator, and regression coverage.
- Modify `docs/low-power-kindle-mode.md`: describe the approved architecture and unresolved reboot persistence.

---

### Task 1: Retire the custom scheduler

**Files:**
- Modify: `test_kindle_scripts.py`
- Modify: `kindle_scripts/refresh.sh`
- Modify: `device_registry.py`
- Modify: `settings_server.py`

**Interfaces:**
- Consumes: executable `$DASHBOARD_DIR/refresh-once.sh`.
- Produces: `refresh.sh` that invokes it once and returns its status; generated legacy flags fixed at zero.

- [ ] **Step 1: Write failing scheduler-boundary tests**

Replace obsolete native-RTC tests with these requirements:

```python
def generated_installer(self):
    import settings_server

    class FakeDevice:
        id = "kindle-131"
        type = "kindle_pw1"
        name = "Test Kindle"
        resolution = (758, 1024)
        enabled = True

    return settings_server.kindle_installer_script(
        FakeDevice(), {"status_token": "fake-token"},
        "192.168.68.167", 8765, 8767
    )

def test_refresh_sh_is_single_cycle_compatibility_shim(self):
    script = REFRESH_SH.read_text(encoding="utf-8")
    for token in (
        "while true", "RTC_SYS_DIR", "/sys/class/rtc", "wakealarm",
        "rtcWakeup", "powerButton", "NATIVE_RTC_SCHEDULER",
        "REFRESH_INTERVAL_MINUTES",
    ):
        self.assertNotIn(token, script)
    self.assertIn('exec "$REFRESH_ONCE_SH"', script)

def test_refresh_once_has_no_scheduler_or_suspend_primitives(self):
    script = REFRESH_ONCE_SH.read_text(encoding="utf-8")
    for token in (
        "while true", "RTC_SYS_DIR", "/sys/class/rtc", "/sys/power/state",
        "wakealarm", "rtcWakeup", "powerButton", "watchdog.sh",
        "REFRESH_INTERVAL_MINUTES",
    ):
        self.assertNotIn(token, script)

def test_generated_device_env_disables_legacy_schedulers(self):
    installer = self.generated_installer()
    self.assertIn('LOW_POWER_MODE="0"', installer)
    self.assertIn('NATIVE_RTC_SCHEDULER="0"', installer)
```

Add a runtime test where fake `refresh-once.sh` appends one line and exits `7`; assert `refresh.sh` invokes it exactly once, logs `KindleCron owns scheduling`, and exits `7`.

- [ ] **Step 2: Verify RED**

```sh
/tmp/kindlecron-integration-venv/bin/python -m unittest \
  test_kindle_scripts.KindleScriptsTests.test_refresh_sh_is_single_cycle_compatibility_shim \
  test_kindle_scripts.KindleScriptsTests.test_refresh_sh_runs_refresh_once_exactly_once_and_propagates_status \
  test_kindle_scripts.KindleScriptsTests.test_generated_device_env_disables_legacy_schedulers -v
```

Expected: failures identify the current loop/RTC state machine and device-derived native flag.

- [ ] **Step 3: Implement the minimal `refresh.sh` shim**

```sh
#!/bin/sh
set -eu
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
REFRESH_ONCE_SH="${REFRESH_ONCE_SH:-$DASHBOARD_DIR/refresh-once.sh}"
LOG_FILE="${LOG_FILE:-$DASHBOARD_DIR/dashboard.log}"
MESSAGE="$(date '+%Y-%m-%d %H:%M:%S') legacy refresh.sh invoked; KindleCron owns scheduling"
echo "$MESSAGE"
echo "$MESSAGE" >> "$LOG_FILE"
[ -x "$REFRESH_ONCE_SH" ] || { echo "ERROR: missing executable $REFRESH_ONCE_SH" >&2; exit 1; }
exec "$REFRESH_ONCE_SH"
```

Do not change `refresh-once.sh` unless a failing requirement-specific test proves a change is needed.

- [ ] **Step 4: Retire the registry capability compatibly**

Remove `native_rtc_scheduler` from `DeviceRecord`, serialization, and public output. Before unknown-field validation, copy and discard the retired input field so existing JSON loads:

```python
value = dict(value)
value.pop("native_rtc_scheduler", None)
```

Replace installer preservation/derivation logic with literal lines:

```python
'NATIVE_RTC_SCHEDULER="0"',
'LOW_POWER_MODE="0"',
```

- [ ] **Step 5: Verify GREEN and commit**

```sh
sh -n kindle_scripts/refresh.sh
/tmp/kindlecron-integration-venv/bin/python -m unittest test_kindle_scripts.KindleScriptsTests -v
git add kindle_scripts/refresh.sh device_registry.py settings_server.py test_kindle_scripts.py
git commit -m "refactor: retire custom Kindle scheduler"
```

---

### Task 2: Add the fail-closed KindleCron integration

**Files:**
- Create: `kindle_scripts/install-kindlecron.sh`
- Modify: `test_kindle_scripts.py`

**Interfaces:**
- Consumes: `DASHBOARD_DIR`, `device.env`, `stop.sh`, and optional `KRON_BIN`/`PROC_DIR`/Upstart test overrides.
- Produces: `install-kindlecron.sh install`, `install-kindlecron.sh start-daemon`, `$DASHBOARD_DIR/kindlecron-install.log`, and one replaced `dashboard` job.

- [ ] **Step 1: Add a hermetic `KindleCronInstallTests` harness**

Create a temporary dashboard and fake Kron executable. The fake records each original argument in angle brackets, returns configurable status for `add`, `list`, and `daemon`, and simulates one persisted `dashboard` record. Provide helpers `run_installer(action)`, `write_fake_kron(version)`, `write_device_env(minutes)`, and `write_stop_script(marker)`.

- [ ] **Step 2: Write failing validation/order tests**

```python
def test_missing_kron_fails_before_legacy_stop(self):
    result = self.run_installer("install")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("required KindleCron binary is missing", result.stderr)
    self.assertFalse(self.stop_marker.exists())

def test_wrong_version_fails_before_legacy_stop(self):
    self.write_fake_kron("KindleCron v0.1.0")
    result = self.run_installer("install")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("expected KindleCron v0.2.0", result.stderr)
    self.assertFalse(self.stop_marker.exists())
```

For each supported interval and invalid `999`, assert the complete call string. This exact assertion must fail if `-timeout` follows `dashboard`:

```python
self.assertIn(
    f"<add><-timeout><2m><dashboard><{schedule}>"
    f"<{self.dashboard / 'refresh-once.sh'}>",
    self.calls.read_text(encoding="utf-8"),
)
```

- [ ] **Step 3: Write failing protected-path, logging, and idempotency tests**

Create sentinel emergency/KMC files with known bytes and modes; run installation with test path overrides; assert `stat()` and bytes are identical afterward. Assert the log contains `persistent startup is not configured`. Run installation twice and assert one daemon start, two idempotent `add` calls, and one stored dashboard job.

- [ ] **Step 4: Verify RED**

```sh
/tmp/kindlecron-integration-venv/bin/python -m unittest test_kindle_scripts.KindleCronInstallTests -v
```

Expected: the class fails because `install-kindlecron.sh` does not exist.

- [ ] **Step 5: Implement the validation and mapping foundation**

```sh
#!/bin/sh
set -eu
DASHBOARD_DIR="${DASHBOARD_DIR:-/mnt/us/dashboard}"
KRON_BIN="${KRON_BIN:-/mnt/us/kron/kron}"
PROC_DIR="${PROC_DIR:-/proc}"
LOG_FILE="${LOG_FILE:-$DASHBOARD_DIR/kindlecron-install.log}"
EXPECTED_VERSION="KindleCron v0.2.0"

log_msg() { LINE="$(date '+%Y-%m-%d %H:%M:%S') $1"; echo "$LINE"; echo "$LINE" >> "$LOG_FILE"; }
fail() { log_msg "ERROR: $1" >&2; exit 1; }
verify_kron() {
    [ -x "$KRON_BIN" ] || fail "required KindleCron binary is missing or not executable: $KRON_BIN"
    VERSION_OUTPUT=$($KRON_BIN version 2>&1) || fail "KindleCron version command failed: $KRON_BIN"
    case "$VERSION_OUTPUT" in
        *"$EXPECTED_VERSION"*) log_msg "verified $EXPECTED_VERSION at $KRON_BIN" ;;
        *) fail "expected $EXPECTED_VERSION, got: $VERSION_OUTPUT" ;;
    esac
}
schedule_for_minutes() {
    case "$1" in
        5) echo "every 5m" ;; 10) echo "every 10m" ;;
        15) echo "every 15m" ;; 30) echo "every 30m" ;;
        60) echo "every 1h" ;; *) echo "every 1h" ;;
    esac
}
```

- [ ] **Step 6: Implement the exact install order**

Use concrete helpers with these contracts and bodies:

```sh
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
    UPSTART_CONF="${UPSTART_CONF:-/etc/upstart/dashboard.conf}"
    MNTROOT_BIN="${MNTROOT_BIN:-mntroot}"
    [ -f "$UPSTART_CONF" ] || return 0
    grep -q '/mnt/us/dashboard' "$UPSTART_CONF" || \
        fail "refusing to modify non-dashboard Upstart file: $UPSTART_CONF"
    "$MNTROOT_BIN" rw || fail "could not make rootfs writable"
    if mv -f "$UPSTART_CONF" "$DASHBOARD_DIR/dashboard.conf.legacy-disabled"; then
        "$MNTROOT_BIN" ro || fail "could not restore rootfs read-only"
        log_msg "disabled legacy dashboard Upstart config: $UPSTART_CONF"
    else
        "$MNTROOT_BIN" ro || true
        fail "could not disable legacy dashboard Upstart config: $UPSTART_CONF"
    fi
}

daemon_count() {
    if [ -n "${KRON_DAEMON_STATE_FILE:-}" ]; then
        cat "$KRON_DAEMON_STATE_FILE" 2>/dev/null || echo 0
        return
    fi
    ps auxww 2>/dev/null | grep -F "$KRON_BIN daemon" | grep -v grep | wc -l | tr -d ' '
}

start_daemon_once() {
    COUNT=$(daemon_count)
    case "$COUNT" in
        0)
            "$KRON_BIN" daemon >> "${KRON_DAEMON_LOG:-/mnt/us/kron/kron.log}" 2>&1 &
            sleep 2
            [ -n "${KRON_DAEMON_STATE_FILE:-}" ] && printf '1\n' > "$KRON_DAEMON_STATE_FILE"
            [ "$(daemon_count)" = "1" ] || fail "KindleCron daemon did not start exactly once"
            log_msg "KindleCron daemon started"
            ;;
        1) log_msg "KindleCron daemon already running" ;;
        *) fail "multiple KindleCron daemons detected: $COUNT" ;;
    esac
}
```

Add an `EXIT HUP INT TERM` cleanup trap around the rootfs transition so an interruption between `mntroot rw` and `mntroot ro` always attempts to restore read-only state. The helper test must interrupt immediately after the mocked `rw` call and assert a later mocked `ro` call.

Then use this exact operation order:

```sh
verify_kron
load_interval_from_device_env
"$DASHBOARD_DIR/stop.sh"
disable_owned_legacy_upstart_config
"$KRON_BIN" add -timeout 2m dashboard "$SCHEDULE" "$DASHBOARD_DIR/refresh-once.sh"
start_daemon_once
"$KRON_BIN" list
log_msg "persistent startup is not configured; reboot persistence remains unresolved"
```

`disable_owned_legacy_upstart_config` may act on `/etc/upstart/dashboard.conf` only when it contains `/mnt/us/dashboard`. Move it to `$DASHBOARD_DIR/dashboard.conf.legacy-disabled` while rootfs is temporarily writable and restore rootfs read-only in a trap. If the file is not dashboard-owned, fail without modifying it. Tests override `UPSTART_CONF` and `MNTROOT_BIN`.

`start_daemon_once` counts exact `$KRON_BIN daemon` commands: zero starts one background daemon and verifies one; one logs already running; more than one fails. Use a test-only state-file override rather than host `/proc` timing.

The production script must not mention `emergency.sh`, `kmc.conf`, `framework_ready`, or create any boot configuration.

- [ ] **Step 7: Verify GREEN and commit**

```sh
sh -n kindle_scripts/install-kindlecron.sh
/tmp/kindlecron-integration-venv/bin/python -m unittest test_kindle_scripts.KindleCronInstallTests -v
git add kindle_scripts/install-kindlecron.sh test_kindle_scripts.py
git commit -m "feat: add verified KindleCron integration"
```

---

### Task 3: Generate and invoke the integration safely

**Files:**
- Modify: `test_kindle_scripts.py`
- Modify: `settings_server.py`

**Interfaces:**
- Consumes: Task 2's integration script.
- Produces: generated installer with disabled legacy shims, exact non-swallowed integration invocation, and no boot-hook ownership.

- [ ] **Step 1: Write failing generator tests**

```python
def test_generated_installer_embeds_and_runs_kindlecron_integration(self):
    script = self.generated_installer()
    self.assertIn('> "$DASHBOARD_DIR/install-kindlecron.sh"', script)
    self.assertIn('"$DASHBOARD_DIR/install-kindlecron.sh" install', script)
    self.assertNotIn('"$DASHBOARD_DIR/install-kindlecron.sh" install || true', script)

def test_generated_installer_has_no_legacy_scheduler_autostart(self):
    script = self.generated_installer()
    self.assertNotIn("cat <<'UPSTART' > /etc/upstart/dashboard.conf", script)
    self.assertNotIn("start on started lab126", script)
    self.assertNotIn('"$DASHBOARD_DIR/watchdog.sh" >/dev/null 2>&1 &', script)

def test_generated_installer_does_not_own_kmc_emergency_hook(self):
    script = self.generated_installer()
    self.assertNotIn("/mnt/us/emergency.sh", script)
    self.assertNotIn("/var/local/kmc", script)
    self.assertNotIn("framework_ready", script)
```

Extract generated `dashboard_loop.sh`, `watchdog.sh`, and `start.sh`; assert respectively one delegation and exit, disabled log and exit, and `install-kindlecron.sh start-daemon` with no boot changes.

- [ ] **Step 2: Verify RED**

```sh
/tmp/kindlecron-integration-venv/bin/python -m unittest \
  test_kindle_scripts.KindleScriptsTests.test_generated_installer_embeds_and_runs_kindlecron_integration \
  test_kindle_scripts.KindleScriptsTests.test_generated_installer_has_no_legacy_scheduler_autostart \
  test_kindle_scripts.KindleScriptsTests.test_generated_installer_does_not_own_kmc_emergency_hook -v
```

Expected: no embedded integration and current legacy Upstart/watchdog content remains.

- [ ] **Step 3: Refactor generated scripts minimally**

In `kindle_installer_script()`:

- embed `install-kindlecron.sh`, `refresh-once.sh`, and `refresh.sh`;
- make `dashboard_loop.sh` execute `refresh.sh` once and exit;
- make `watchdog.sh` log disabled and exit 0;
- make `start.sh` execute `install-kindlecron.sh start-daemon`;
- retain PID/cmdline-safe `stop.sh` to stop old deployed processes;
- remove generated `/etc/upstart/dashboard.conf` and watchdog startup;
- chmod the integration script;
- finish with a non-swallowed call:

```python
'"$DASHBOARD_DIR/install-kindlecron.sh" install',
'echo "Configured Kindle dashboard device: $DEVICE_ID"',
```

- [ ] **Step 4: Execute the generated installer hermetically**

With sandbox overrides, assert missing/mismatched Kron fails before stop, valid installation writes scripts and exact job, flags are zero, second execution is idempotent, and emergency/KMC sentinels remain byte- and mode-identical.

- [ ] **Step 5: Verify GREEN and commit**

```sh
sh -n kindle_scripts/refresh.sh
sh -n kindle_scripts/refresh-once.sh
sh -n kindle_scripts/install-kindlecron.sh
/tmp/kindlecron-integration-venv/bin/python -m unittest test_kindle_scripts.py -v
git add settings_server.py test_kindle_scripts.py
git commit -m "feat: generate KindleCron-only Kindle installer"
```

---

### Task 4: Lock in physical-root-cause regressions and document operations

**Files:**
- Modify: `test_kindle_scripts.py`
- Modify: `docs/low-power-kindle-mode.md`

**Interfaces:**
- Consumes: final scripts and generator.
- Produces: explicit network/safety regression guards and accurate operations documentation.

- [ ] **Step 1: Add the proven network semantics guard**

```python
def test_refresh_once_preserves_proven_best_effort_network_wait(self):
    script = REFRESH_ONCE_SH.read_text(encoding="utf-8")
    self.assertIn("wait_for_network || true", script)
    self.assertNotIn("cmState", script)
    self.assertNotIn("curl -I", script)
    self.assertNotIn("curl --head", script)
    self.assertNotIn('fail "network/server not ready', script)
```

This characterization test should pass immediately and must stay green. Retain existing behavioral tests for failed-download preservation, atomic replacement, 304/unchanged overlay repair, final image/overlay equality, and screen-saver release ordering.

- [ ] **Step 2: Rewrite `docs/low-power-kindle-mode.md`**

Document the external v0.2.0 dependency, exact job syntax/mapping, one-cycle refresh boundary, disabled legacy shims, current-session `start.sh`, unconfigured reboot persistence, protected emergency/KMC paths, permissive readiness root cause, overlay guarantees, and deployment approval gate. Remove claims that `refresh.sh` or watchdog is the desired long-running scheduler.

- [ ] **Step 3: Run affected tests and commit**

```sh
/tmp/kindlecron-integration-venv/bin/python -m unittest \
  test_kindle_scripts.py test_settings_server.py test_device_registry.py -v
git add test_kindle_scripts.py docs/low-power-kindle-mode.md
git commit -m "docs: document KindleCron scheduler architecture"
```

---

### Task 5: Final verification and review package

**Files:**
- Verify only; make no production edit unless a failing test traces to an in-scope defect.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: exact evidence/diff package; no deployment.

- [ ] **Step 1: Run shell syntax checks**

```sh
for script in kindle_scripts/*.sh; do sh -n "$script"; done
```

- [ ] **Step 2: Run all relevant tests**

```sh
/tmp/kindlecron-integration-venv/bin/python -m unittest \
  test_kindle_scripts.py test_settings_server.py test_device_registry.py \
  test_device_image_server.py -v
```

Expected: zero failures and errors. Report pre-existing warnings separately.

- [ ] **Step 3: Scan production paths for forbidden primitives**

```sh
rg -n 'rtcWakeup|rtcWakeup2|/sys/class/rtc|/sys/power/state|wakealarm|powerButton|while true' \
  kindle_scripts/refresh.sh kindle_scripts/refresh-once.sh \
  kindle_scripts/install-kindlecron.sh settings_server.py
rg -n '/mnt/us/emergency.sh|/var/local/kmc|framework_ready' \
  kindle_scripts settings_server.py
```

Expected: no matches in production paths.

- [ ] **Step 4: Verify diff hygiene and commit scope**

```sh
git diff --check eaa3eef..HEAD
git status --short
git log --oneline --decorate eaa3eef..HEAD
git diff --stat eaa3eef..HEAD
git diff --find-renames eaa3eef..HEAD
```

Expected: clean worktree, no whitespace errors, and only approved files changed.

- [ ] **Step 5: Prepare but do not execute physical validation**

The handoff lists exact read-only prechecks, backup/rollback targets, proposed deployment diff, expected output, and stop conditions. Separate approved scheduler/refresh validation (version, one daemon/job, no legacy process, several sleep/wake/update/overlay cycles) from unresolved reboot-persistence research (genuine reboot, fresh startup timestamp, new daemon PID/start time, retained job, no legacy process, autonomous post-reboot cycles).

Do not copy files, run the installer, stop production processes, alter Upstart/KMC, reboot, or otherwise change Kindle 4 without a new explicit approval after the exact diff is shown.

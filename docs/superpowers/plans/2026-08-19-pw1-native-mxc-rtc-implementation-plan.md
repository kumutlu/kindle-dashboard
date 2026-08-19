# PW1 Native MXC RTC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace KindleCron on Kindle Paperwhite 1 with a reboot-persistent native `mxc_rtc` suspend/wake scheduler that performs exactly one dashboard refresh per configured interval and survives reboot without falling back to the legacy `refresh.sh` loop.

**Architecture:** A two-phase handoff starts the native scheduler in inert standby, proves its PID/starttime identity, stops all legacy cadence owners, then commits activation with an identity-bound token and waits for an ACK. The active scheduler runs `refresh-once.sh`, arms `/sys/devices/platform/mxc_rtc.0/wakeup_enable`, and enters `mem`; an owned Upstart job invokes `/mnt/us/dashboard/start.sh` after reboot so the native scheduler is restored instead of the legacy loop.

**Tech Stack:** POSIX `/bin/sh`, Lab126 Linux 2.6.31, `/proc`, `mxc_rtc`, Upstart, Python `unittest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-12-default-kindle-true-low-power-design.md`

## Global Constraints

- PW1 kernel is `2.6.31-rt11-lab126`; do not depend on KindleCron/modern Go runtime.
- Do not use LIPC `rtcWakeup`, `rtcWakeup2`, `readyToSuspend`, or `wakeupFromSuspend` for cadence.
- Native wake primitive is `/sys/devices/platform/mxc_rtc.0/wakeup_enable`.
- Native sleep primitive is `echo mem > /sys/power/state`.
- Never activate the new cadence before legacy ownership has been safely removed.
- PID reuse protection must bind process identity to `/proc/<pid>/stat` starttime and cmdline.
- Reboot persistence must be owned by a dashboard-specific Upstart job and must not launch the old infinite `refresh.sh` loop.

---

### Task 1: Define the PW1 scheduler contract

**Files:**
- Create: `test_pw1_mxc_rtc.py`
- Create: `.github/workflows/pw1-mxc-rtc-tests.yml`

- [x] **Step 1: Write failing tests for missing native scheduler, safe standby, legacy refresh-loop ownership, transactional ordering, and reboot persistence.**
- [ ] **Step 2: Verify the contract is red before production scripts exist.**

### Task 2: Implement native scheduler and legacy shutdown

**Files:**
- Create: `kindle_scripts/mxc-rtc-scheduler.sh`
- Create: `kindle_scripts/stop_legacy.sh`

- [ ] **Step 1: Implement capability checks, atomic PID file, PID/starttime-bound activation, ACK, interval mapping, one-shot refresh, RTC arm verification, and `mem` suspend.**
- [ ] **Step 2: Implement safe shutdown for `refresh.sh`, `watchdog.sh`, and `dashboard_loop.sh` with starttime/cmdline validation.**
- [ ] **Step 3: Run focused tests.**

### Task 3: Replace KindleCron install path with transactional native handoff

**Files:**
- Modify: `kindle_scripts/install-kindlecron.sh`

- [ ] **Step 1: Start native scheduler in standby and prove identity.**
- [ ] **Step 2: Stop and verify legacy cadence owners.**
- [ ] **Step 3: Atomically activate and require matching ACK.**
- [ ] **Step 4: Roll back to legacy startup if activation fails.**
- [ ] **Step 5: Install an owned Upstart config that runs `/mnt/us/dashboard/start.sh` on boot.**

### Task 4: Integrate bundle generation and settings

**Files:**
- Modify: `settings_server.py`
- Modify: `test_settings_server.py`

- [ ] **Step 1: Bundle the native scheduler and legacy-stop scripts in the installer payload.**
- [ ] **Step 2: Generate `start.sh`/`stop.sh` for the native backend and persist scheduler selection.**
- [ ] **Step 3: Run server tests and full Kindle script tests.**

### Task 5: Verify and stage safely

**Files:**
- No production code changes unless verification exposes a defect.

- [ ] **Step 1: Run `sh -n kindle_scripts/*.sh`.**
- [ ] **Step 2: Run focused PW1 tests and existing Kindle script tests.**
- [ ] **Step 3: Run `git diff --check`.**
- [ ] **Step 4: Review PR diff and checks before any physical-device deployment.**

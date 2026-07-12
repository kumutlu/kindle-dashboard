# Default Kindle True Low-Power Pilot

This runbook applies only to `default-kindle` (`192.168.68.119`). It does not replace or overwrite the active legacy dashboard scripts.

## Safety markers

Either marker prevents a new low-power cycle:

```text
/mnt/us/dashboard/DISABLE_LOW_POWER
/mnt/us/dashboard/NOAUTOSTART
```

The first two 180-second cycles must leave USBNetwork, Dropbear, and telnetd running.

## Pilot files

```text
/mnt/us/dashboard/low-power-refresh-once.sh
/mnt/us/dashboard/low-power-cycle.sh
/mnt/us/dashboard/low-power-wake-handler.sh
/mnt/us/dashboard/low-power-manual-start.sh
/mnt/us/dashboard/low-power-state/
/etc/upstart/default-kindle-low-power.conf
/mnt/us/default-kindle-low-power-rollback/rollback.sh
```

The wake source is `com.lab126.powerd rtcWakeup`. Cron only checks `next-cycle-due` after the kernel and powerd have resumed userspace.

## Pre-activation checks

Before activation, preserve hashes and copies of:

```text
/mnt/us/dashboard/start-dashboard.sh
/mnt/us/dashboard/refresh.sh
/mnt/us/dashboard/refresh-once.sh
/etc/upstart/kindle-dashboard.conf
/etc/crontab/root
```

Run:

```sh
/mnt/us/default-kindle-low-power-rollback/rollback.sh --check
```

Do not activate unless it exits zero and all pilot scripts are executable.

## Manual 180-second start

After the pilot Upstart and cron integration are installed and validated:

```sh
/mnt/us/dashboard/low-power-manual-start.sh 180
```

Immediately inspect `/mnt/us/dashboard/low-power-state/low-power.log`. Suspend is forbidden unless the log confirms the disable markers were checked, both wake and rollback handlers are executable, and `rtcWakeup=180` succeeded.

## Evidence

Collect for each cycle:

- battery level and charging state;
- powerd state and suspend/resume logs;
- Wi-Fi state;
- HTTP status and ETag/Last-Modified state;
- image MD5 when changed;
- process list immediately before suspend;
- SSH unreachability during suspend and return after autonomous wake;
- cycle sequence and timestamps.

The visible e-ink image alone is not suspend evidence.

## Rollback

When SSH is available:

```sh
/mnt/us/default-kindle-low-power-rollback/rollback.sh
```

If RTC wake fails, physically wake the Kindle once and run the same command. Verify one legacy `refresh.sh` process, current image content, stopped `lab126_gui`, and reboot autostart.

## Transition gates

Do not set 3600 seconds until:

1. two consecutive autonomous 180-second cycles pass;
2. rollback and legacy reboot are rehearsed;
3. the validated pilot is reactivated successfully;
4. low-power reboot persistence passes.

Short pilot results do not demonstrate 30–60 day battery life. Record a later multi-day battery window before estimating endurance.

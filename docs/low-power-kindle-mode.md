# Low-power Kindle dashboard mode

The Kindle dashboard uses the externally installed KindleCron v0.2.0 binary as
its only scheduler. The repository does not download, install, replace, or own
KindleCron. Installation fails closed unless the executable at
`/mnt/us/kron/kron` reports the expected version.

## Scheduling architecture

The generated dashboard installer replaces the `dashboard` job with this exact
command shape:

```text
/mnt/us/kron/kron add -timeout 2m dashboard "every <interval>" /mnt/us/dashboard/refresh-once.sh
```

The configured minute interval maps as follows:

| Setting | KindleCron schedule |
| --- | --- |
| `5` | `every 5m` |
| `10` | `every 10m` |
| `15` | `every 15m` |
| `30` | `every 30m` |
| `60` or an invalid value | `every 1h` |

`refresh-once.sh` performs exactly one refresh cycle and returns. It contains no
RTC programming, suspend scheduling, watchdog behavior, or scheduler loop.
`refresh.sh` remains only as a one-cycle compatibility shim.

The old `dashboard_loop.sh` delegates once to `refresh.sh`, and `watchdog.sh`
logs that it is disabled and exits. `stop.sh` remains available to retire
already-running legacy processes safely. Compatibility values
`LOW_POWER_MODE="0"` and `NATIVE_RTC_SCHEDULER="0"` are still written to
`device.env`, but they do not enable either legacy scheduler.

`start.sh` verifies KindleCron and starts its daemon for the current session
only. Reboot persistence is not configured or claimed. It remains unresolved
until a genuine reboot demonstrates a fresh daemon start, retained job, no
legacy scheduler process, and autonomous post-reboot refresh cycles.

## Refresh behavior

Each cycle loads device configuration, optionally enables Wi-Fi, conditionally
downloads `/device/<device-id>/image.png`, atomically replaces the local image
when changed, updates e-ink only when needed, reconciles `bg_ss00.png`, and then
restores the screen-saver and Wi-Fi state.

Network readiness intentionally remains permissive: `wait_for_network || true`
allows the proven download path to make the final decision. Do not add a
`cmState` gate or an HTTP HEAD readiness probe; those checks caused false
negatives on the physical Kindle. Failed or invalid downloads preserve the
existing image and screensaver overlay. A `304` or unchanged image still repairs
a stale overlay, and final verification ensures the dashboard image and
`bg_ss00.png` agree.

## Device settings

- `refresh_interval_minutes`: `5`, `10`, `15`, `30`, or `60`; defaults to `60`.
- `wifi_power_save`: turns Wi-Fi off again after a cycle when enabled.
- `update_only_if_changed`: uses cached HTTP validators and skips unnecessary
  display refreshes when enabled.

## Safety and operations

The integration may disable only a legacy `/etc/upstart/dashboard.conf` that it
can identify as dashboard-owned. It must never read, write, replace, chmod, or
otherwise claim `/mnt/us/emergency.sh` or
`/var/local/kmc/system_patches/kmc.conf`.

Repository tests and generated-installer sandbox checks must pass before any
physical work. Deployment, process shutdown, root filesystem changes, reboot,
and production validation require a separate explicit approval after the exact
diff and rollback targets are presented.

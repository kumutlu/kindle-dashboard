# Kindle-03 removal report

## Scope

This maintenance removes only the unused `kindle-03` registry record for the
former device at `192.168.68.127`. It does not alter `default-kindle`,
`kitchen-kindle`, `kindle-131`, or any low-power file.

## Read-only audit

Active references found before removal:

- `devices.json`: enabled registry record, exposed by `/api/devices`.
- `devices/kindle-03/config.json`: exclusive device configuration.
- `devices/kindle-03/image.png`: exclusive generated image.
- `devices/kindle-03/render_state.json`: exclusive render metadata.
- `devices/kindle-03/.render.lock`: exclusive empty render lock.
- `test_run_dashboard.py`: obsolete literal in a negative scheduler assertion.

Historical references retained as operational history:

- `access.log`: three browser preview GETs from `192.168.68.125` on 11 July.
- `/var/log/syslog.1`: historical Tailscale traffic involving
  `192.168.68.127` and one historical render log line.

No `kindle-03` dependency was found in the canonical production scheduler,
user crontab, systemd units, running processes, scripts, documentation,
monitoring configuration, or device-status records. SSH port 22 at
`192.168.68.127` was unreachable during the audit.

## Removal

- Removed the `kindle-03` object from the live, untracked `devices.json`.
- Removed the exclusive `devices/kindle-03/` runtime directory after backup.
- Removed the obsolete test literal.
- Preserved historical logs unchanged.

## Backup

Complete rollback data is stored at:

`/home/user/backups/kindle-03-removal-20260713-065118`

It contains the pre-removal registry, a compressed copy of the exclusive
runtime directory, repository bundle, scheduler/service/cron snapshots,
checksums, and extracted historical log references.

## Rollback

```sh
cd /home/user/kindle4-weather-display
cp /home/user/backups/kindle-03-removal-20260713-065118/devices.json devices.json
tar -C devices -xzf \
  /home/user/backups/kindle-03-removal-20260713-065118/kindle-03-runtime.tar.gz
```

## Validation

- Live registry and `/api/devices` contain exactly `default-kindle`,
  `kitchen-kindle`, and `kindle-131`.
- The old image and configuration endpoints both return HTTP 404.
- The canonical timer remains active; its last production result is
  `success` with exit status 0. The recorded cycle completed legacy,
  `default-kindle`, and `kindle-131` without a failed target.
- Explicit server render checks succeeded for all three remaining devices:
  `default-kindle` and `kitchen-kindle` produced 758x1024 grayscale PNGs;
  `kindle-131` produced a 600x800 grayscale PNG.
- No active registry, scheduler, cron, systemd, process, script, monitoring,
  or runtime-directory reference remains.
- Full test suite: 253 tests passed.

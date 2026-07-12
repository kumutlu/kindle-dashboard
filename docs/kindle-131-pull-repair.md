# Kindle 131 pull repair

## Scope and root cause

This repair is limited to `kindle-131` (`192.168.68.131`). The device had
valid executable pull scripts and the correct image endpoint, but no Upstart
job and no running refresh/watchdog process. The canonical production renderer
also omitted `kindle-131`, leaving its server-side image stale.

## Server integration

`scheduled_render.py` now renders these targets exactly once, in order, during
each existing `kindle-dashboard-generate.timer` cycle:

1. legacy image
2. `default-kindle`
3. `kindle-131`

No second timer, cron job, or SCP push was added. The first canonical timer
proof was recorded at `2026-07-12 22:35:52 +0100`; it completed at `22:35:55`
and changed the stale SHA-256 from
`921880bfd7d6c54f95a1bcbef90bd83ee45836facd263f308d1540ad0a24c430`
to `6b51b4ea1449af43d971a9bef9ca46ee8c84969b7f63edaee7115842d218f89f`.

## Kindle autostart

`/etc/upstart/dashboard.conf` directly supervises the existing
`/mnt/us/dashboard/watchdog.sh` with Upstart respawn enabled. The watchdog owns
one existing continuous `refresh.sh` loop. No pull script, low-power marker,
cron entry, or low-power activation file was changed.

The direct watchdog ownership is required on this Kindle firmware: a one-shot
Upstart job that calls `start.sh` exits immediately and Upstart terminates its
detached children.

## Backups

- Server: `/home/user/backups/kindle-131-pull-repair-20260712-223018`
- Kindle: `/mnt/us/kindle-131-pull-repair-20260712-223018`

## Rollback

Restore the server scheduler:

```sh
cp /home/user/backups/kindle-131-pull-repair-20260712-223018/server/scheduled_render.py \
  /home/user/kindle4-weather-display/scheduled_render.py
```

Remove the new Kindle autostart hook and stop its supervised processes:

```sh
ssh -i ~/.ssh/kindle_dashboard_ed25519 \
  -o UserKnownHostsFile=~/.ssh/kindle_dashboard_known_hosts \
  root@192.168.68.131 '
    /sbin/stop dashboard 2>/dev/null || true
    /usr/sbin/mntroot rw
    rm -f /etc/upstart/dashboard.conf
    /usr/sbin/mntroot ro
    /mnt/us/dashboard/stop.sh 2>/dev/null || true
  '
```

## Validation

- Initial automatic pull after service activation: `2026-07-12 22:45:08
  +0100`, HTTP 200.
- Server and Kindle MD5 after that pull:
  `1f75289f923b812fa7439ca2e6ae8a6d`.
- Reboot proof: at 63 seconds uptime, Upstart was running with one watchdog and
  exactly one `refresh.sh` loop.
- First natural interval pull: `2026-07-12 23:46:39 +0100`, HTTP 200.
- Second natural interval pull: `2026-07-13 00:46:41 +0100`, HTTP 200.
- The two requests were 60 minutes and 2 seconds apart, with no intervening
  duplicate `kindle-131` pull.
- At 7,240 seconds post-reboot, Upstart remained `start/running` with one
  watchdog and exactly one `refresh.sh` process.
- Final server and Kindle MD5:
  `936f2b62faa2d8e0f4398f800d1dee3d`.
- Final server SHA-256:
  `3d2e39ddeb419ddfdc85ad8fc4d17f5ac3349aa2c13821e47b563a54ba72f86d`.
- No cron or systemd configuration contains a separate `kindle-131` render;
  the existing production timer is the sole render owner.

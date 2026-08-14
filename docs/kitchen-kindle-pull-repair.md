# Kitchen Kindle pull repair

## Scope

This deployment repairs only `kitchen-kindle` (`192.168.68.122`). The
`default-kindle`, Kindle 4, legacy `.127` device, and low-power pilot files are
outside its scope.

## Root cause

The active Kindle scripts used the settings-server port (`8767`) as their PNG
source. The image server is on port `8765`, so the pull loop received HTTP 404
responses. The hourly server-side push hid the fault by copying a fresh image
to the Kindle. Process inspection also showed that the apparent second
`refresh.sh` was the BusyBox sleep child of the single Upstart-owned loop, not
an independently scheduled loop.

## Deployment

The following Kindle files were backed up outside the dashboard directory and
then changed in place:

- `/mnt/us/dashboard/device.env`
- `/mnt/us/dashboard/refresh.sh`
- `/mnt/us/dashboard/refresh-once.sh`

All image URLs now use:

`http://192.168.68.167:8765/device/kitchen-kindle/image.png`

`refresh.sh` invokes `/bin/sleep` explicitly so process listings distinguish
the one refresh loop from its waiting child. The unchanged Upstart owner is
`/etc/upstart/dashboard.conf`.

The server-side hourly push remains enabled as a temporary fallback. It must
not be removed until two consecutive natural Kindle pulls and reboot
persistence are proven.

## Backups and rollback

Server snapshot:

`/home/user/backups/kitchen-kindle-pull-repair-20260712-191552`

Kindle snapshot:

`/mnt/us/kitchen-kindle-pull-repair-20260712-191552`

Rollback from the mini PC:

```sh
ssh -i ~/.ssh/kindle_dashboard_ed25519 \
  -o UserKnownHostsFile=~/.ssh/kindle_dashboard_known_hosts \
  root@192.168.68.122 '
    /sbin/stop dashboard 2>/dev/null || true
    cp /mnt/us/kitchen-kindle-pull-repair-20260712-191552/refresh.sh /mnt/us/dashboard/refresh.sh
    cp /mnt/us/kitchen-kindle-pull-repair-20260712-191552/refresh-once.sh /mnt/us/dashboard/refresh-once.sh
    cp /mnt/us/kitchen-kindle-pull-repair-20260712-191552/device.env /mnt/us/dashboard/device.env
    cp /mnt/us/kitchen-kindle-pull-repair-20260712-191552/dashboard.conf /etc/upstart/dashboard.conf
    /sbin/start dashboard
  '
```

## Validation record

- Initial automatic GET: `2026-07-12 19:17:49 +0100`, HTTP 200 on port 8765.
- Server and Kindle MD5 after that pull:
  `d87ef7487d5fde796a5d6f668bce2a84`.
- Reboot completed; at 106 seconds uptime Upstart was running and exactly one
  `/bin/sh /mnt/us/dashboard/refresh.sh` loop was visible.
- First natural hourly pull: `2026-07-12 20:19:51 +0100`, HTTP 200.
- Second natural hourly pull: `2026-07-12 21:19:52 +0100`, HTTP 200.
- Final server and Kindle MD5 after the retained 22:00 fallback push:
  `d98bccd3185408863e868ea3778109ca`.
- Final server SHA-256:
  `a311ae75a5601e8e7d04d459525daccec752cfc9380853a46d8119c82bf480f6`.
- At 10,216 seconds post-reboot, Upstart remained `start/running` and exactly
  one `refresh.sh` process was visible.
- The repaired runtime files contain no port-8767 image URL and the rebooted
  autostart log contains zero HTTP 404 entries.
- The server crontab is byte-for-byte unchanged from its pre-deployment
  backup; the hourly server push is retained temporarily as fallback.

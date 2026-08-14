# Default Kindle Low-Power Deployment Report

## Phase 2 — Installation without activation

Date: 2026-07-12

Target: `default-kindle` (`192.168.68.119`) only.

### Snapshot

Full rollback material is stored outside the live dashboard directory:

```text
/mnt/us/default-kindle-low-power-rollback/snapshot/dashboard/
/mnt/us/default-kindle-low-power-rollback/snapshot/kindle-dashboard.conf
/mnt/us/default-kindle-low-power-rollback/snapshot/crontab-root
/mnt/us/default-kindle-low-power-rollback/snapshot/legacy-md5.txt
```

### Installed but inactive pilot files

```text
/mnt/us/dashboard/low-power-refresh-once.sh
/mnt/us/dashboard/low-power-cycle.sh
/mnt/us/dashboard/low-power-wake-handler.sh
/mnt/us/dashboard/low-power-manual-start.sh
/mnt/us/default-kindle-low-power-rollback/rollback.sh
/mnt/us/default-kindle-low-power-rollback/default-kindle-low-power.conf.candidate
```

The candidate Upstart file was not copied into `/etc/upstart`. No cron line was added. No suspend command was run.

### Legacy integrity evidence

Before and after installation, hashes remained:

```text
39218e24b93e14bbbfb2dc02bcf1d261  /mnt/us/dashboard/start-dashboard.sh
abb5dc46632ba1fd9c5385d5806009cf  /mnt/us/dashboard/refresh.sh
926d6f4b88dc2d0a84dfd312ca60c383  /mnt/us/dashboard/refresh-once.sh
6b662cc4708b6e447db4098030c0011b  /etc/upstart/kindle-dashboard.conf
890d7542cb133c5bfdca1111cfb11ad0  /etc/crontab/root
```

`rollback.sh --check` exited zero. All pilot shell files passed `/bin/sh -n`. Exactly one legacy `refresh.sh` process remained, no low-power process existed, `powerd` remained `active`, `preventScreenSaver` remained `1`, and Wi-Fi remained `CONNECTED`.

### Rollback command

Because low-power mode is not active, remove the inactive pilot files if required. The full operational rollback command is already installed and validated:

```sh
/mnt/us/default-kindle-low-power-rollback/rollback.sh
```

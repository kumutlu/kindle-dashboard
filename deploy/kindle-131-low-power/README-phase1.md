# Kindle 131 low-power pilot: Phase 1 assets

Scope: `kindle-131` (`192.168.68.131`) only.

This directory records the recovery controls installed before kiosk or
low-power activation. The pilot remains inactive unless the device contains
`/mnt/us/dashboard/LOW_POWER_ACTIVE`.

Installed recovery snapshot:

`/mnt/us/kindle-131-low-power-rollback-20260716-115926`

One-command rollback:

```sh
/mnt/us/kindle-131-low-power-rollback-20260716-115926/rollback.sh
```

The failsafe boot job only checks the consecutive-failure counter when the
activation marker exists. Three recorded failures execute the rollback.

Phase 1 validation:

- rollback snapshot contains the original dashboard directory and Upstart job;
- rollback and failsafe scripts pass `sh -n`;
- `rollback.sh --check` succeeds;
- no activation or disable marker exists;
- the legacy dashboard remains the active architecture.

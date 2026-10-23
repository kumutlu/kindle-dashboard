# PW1 mxc_rtc rollout safety gate

No physical-device deployment is permitted until the PR diff is reviewed, focused and full CI pass, and the device checks below are explicitly authorized. This change has not been installed on a physical Kindle.

## Protocol and recovery contract

- Exactly one controller and one native scheduler are admitted by separate mkdir locks in `/var/run`. A second caller fails without touching state. Confirm `/var/run` is writable and cleared by reboot on PW1. An abandoned controller lock fails closed: before removing it, verify no installer/start controller is running. `stop.sh` reclaims the scheduler lock only after scanning `/proc` for all native owners.
- Standby performs no refresh, RTC write or suspend. Activation ACK binds to PID/starttime, but still cannot start cadence.
- Installer checks that the legacy startup path exists before any handoff. Every trappable failure after transaction start performs common recovery, including failures from persistence helpers and signals. Reinstall records displacement of an existing native owner as requiring recovery.
- The owned Upstart job is written to a temporary file, renamed, synced and followed by a successful rootfs read-only remount. The versioned `ENABLE_MXC_RTC_AUTOSTART` receipt is then renamed and synced. Missing/old empty receipts require reinstall; they do not authorize native commit.
- Commit requires the receipt, a successful sync, and successful RTC arm/readback before publishing the identity-bound committed ACK. ACK means these software checks passed. It does **not** prove power-loss durability of the storage hardware, successful suspend/wake, or that the scheduler will never fail afterward. ACK/PID files are transient process messages, not reboot credentials.
- Native always arms and verifies RTC before `mem`. Failures stop cadence, leave a visible `mxc-rtc.failed` diagnostic and avoid further suspend. Upstart runs `start.sh --supervise`; after boot this monitors native death and attempts verified legacy recovery. The installer also starts a detached monitor and requires an identity-bound monitor readiness ACK before reporting success, so the initial session is covered too. Confirm `nohup` and monitor lifetime on the device.
- Recovery attempts native shutdown even if the receipt cannot be unlinked or synced. It must prove no native process remains, stop/verify any partial legacy owners, invoke the proven legacy startup and observe a legacy process. Failure is reported as `ROLLBACK FAILED`; it is never reported as successful recovery. Failed receipt removal/sync also means persistence rollback is not guaranteed across reboot.
- An explicit external `stop.sh` writes an identity-bound stop request. The boot supervisor respects it without restarting legacy. Internal handoff stops do not create that request. `NOAUTOSTART` also prevents automatic recovery.
- The native job waits up to 60 seconds for the dashboard startup path after `started lab126`. This is a bounded mitigation, not proof of boot event/mount order. Validate the real event and filesystem timing.

## Required physical acceptance evidence

1. Read-only preflight: firmware/kernel, executable scripts, rootfs remount support, Upstart event names/config parsing, `/var/run` volatility and the proven legacy startup/rollback snapshot.
2. Rehearse rollback while awake, including failed native standby and partial legacy shutdown. Confirm one cadence owner and retain SSH access.
3. Reboot with missing/stale PID, activation and commit files. Confirm the owned native job starts after `/mnt/us` is ready; no old boot job starts a competing legacy loop.
4. Perform controlled power-loss/reboot tests around Upstart publication, receipt publication and commit ACK. Capture boot and scheduler logs. POSIX rename plus successful `sync`/read-only remount is the software assumption, not a substitute for these tests.
5. Prove at least two successive autonomous RTC suspend/wake/refresh cycles, then exercise RTC readback/suspend failure and boot-supervisor recovery.
6. Determine and test the driver's supported alarm-cancellation semantics. Scripts intentionally do not guess that writing `0` clears an existing `mxc_rtc` alarm. On rollback an earlier alarm may remain until it fires; verify it cannot trigger another native cadence or conflict with legacy/power management before deployment.

SIGKILL and power loss cannot run shell traps. Locks and persisted receipt handling must be verified across a real reboot. Keep the PR draft until the device acceptance evidence is recorded.

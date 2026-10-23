# PW1 native mxc_rtc notes

This branch replaces the incompatible KindleCron runtime path on Paperwhite 1 with the device's proven native MXC RTC wake source.

Validated device primitives from the physical PW1:

- Kernel: `2.6.31-rt11-lab126`
- RTC wake control: `/sys/devices/platform/mxc_rtc.0/wakeup_enable`
- Suspend state: `mem` via `/sys/power/state`
- A controlled 120-second experiment confirmed autonomous RTC wake from `mem` without physical intervention.

The implementation must preserve exactly one cadence owner, use PID/starttime/cmdline identity checks during handoff, and install reboot persistence so a reboot cannot silently return the device to the legacy infinite `refresh.sh` loop.

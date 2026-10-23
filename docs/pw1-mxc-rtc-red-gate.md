# PW1 mxc_rtc red gate

Before implementation, `test_pw1_mxc_rtc.py` intentionally references two production scripts that do not yet exist on this branch:

- `kindle_scripts/mxc-rtc-scheduler.sh`
- `kindle_scripts/stop_legacy.sh`

The focused contract is therefore expected to fail until the native scheduler implementation is added. This file exists only to make the test-first checkpoint explicit in branch history.

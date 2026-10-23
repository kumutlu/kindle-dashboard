# PW1 handoff hardening

**Goal:** Make failed handoff recover visibly and prevent a commit ACK before persistence and RTC readiness have been checked.
**Approach:** Retain the POSIX shell protocol and isolated KindleCron path. Add shared recovery helpers, exercise real scripts against temporary filesystem/device substitutes, and keep physical deployment gated.

1. Reproduce persistence-install failures, unsafe rollback and premature commit ACK with executable failure-injection tests.
2. Centralize error/signal cleanup, require verified native shutdown before legacy restart, and report failed recovery.
3. Publish Upstart and its enable receipt atomically with sync barriers; wait for dashboard availability on boot. Require that receipt before committing, and verify RTC arming before a flushed commit ACK.
4. Serialize handoffs and scheduler instances; fail closed on abandoned locks until verified cleanup. Keep uncertain RTC cancellation semantics behind the physical-device gate.
5. Run focused and full unittest suites, shell syntax and whitespace checks; review final diff and update PR #3 without merging or deploying.

Physical power-loss durability, mount/event ordering, stale hardware alarm cancellation and real suspend/wake remain device acceptance tests. Filesystem substitutes cannot prove these.

## Execution record

- Shared recovery, receipt/RTC ACK gating and control/scheduler locks implemented.
- First CI run (77f387f) passed all existing focused checks plus the first nine failure-injection cases.
- Independent review found displaced-native reinstall recovery and receipt-unlink early-return defects; both received regression tests and fixes. Deliberate stop semantics are now explicit and tested.
- Ruling: retain unknown RTC cancellation and physical durability as device gates, rather than guessing driver semantics. Cost: no physical rollout until that evidence exists.
- Ruling: abandoned controller locks fail closed; do not reclaim by PID guesses across boots. Cost: verified operator cleanup may be needed after SIGKILL.
- Initial install now also starts a detached recovery monitor and requires its identity-bound readiness ACK before success. Physical monitor lifetime remains part of device acceptance.

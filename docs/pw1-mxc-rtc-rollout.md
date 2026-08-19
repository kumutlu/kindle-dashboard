# PW1 mxc_rtc rollout safety gate

No physical-device deployment is permitted from this branch until all focused CI checks pass and the PR diff has been reviewed. The physical staging sequence must begin with a read-only preflight and keep the proven legacy scheduler available for rollback until the native scheduler has emitted a matching activation ACK.

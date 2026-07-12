import unittest
from pathlib import Path
from types import SimpleNamespace

from kindle_low_power import (
    LowPowerTargetError,
    build_low_power_deployment,
    render_low_power_bundle,
    validate_low_power_target,
)


def device(device_id="default-kindle", device_type="kindle_pw1"):
    return SimpleNamespace(
        id=device_id,
        name=device_id,
        type=device_type,
        enabled=True,
        resolution=(758, 1024),
        connection={
            "host": "192.168.68.119",
            "user": "root",
            "ssh_profile": "kindle_dashboard",
            "port": 22,
        },
    )


class LowPowerTargetTests(unittest.TestCase):
    def test_only_default_kindle_is_accepted(self):
        validate_low_power_target(device())
        for device_id in ("kitchen-kindle", "kindle-131"):
            with self.subTest(device_id=device_id):
                with self.assertRaises(LowPowerTargetError):
                    validate_low_power_target(device(device_id))

    def test_non_kindle_and_disabled_targets_are_rejected(self):
        with self.assertRaises(LowPowerTargetError):
            validate_low_power_target(device(device_type="generic_png"))
        disabled = device()
        disabled.enabled = False
        with self.assertRaises(LowPowerTargetError):
            validate_low_power_target(disabled)


class LowPowerBundleTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "refresh_interval_minutes": 60,
            "update_only_if_changed": True,
            "wifi_power_save": True,
        }
        self.bundle = render_low_power_bundle(
            device(), self.config, "192.168.68.167", 8765
        )

    def test_bundle_uses_only_separate_low_power_paths(self):
        self.assertEqual(
            set(self.bundle),
            {
                "/mnt/us/dashboard/low-power-refresh-once.sh",
                "/mnt/us/dashboard/low-power-cycle.sh",
                "/mnt/us/dashboard/low-power-wake-handler.sh",
                "/mnt/us/dashboard/low-power-manual-start.sh",
                "/etc/upstart/default-kindle-low-power.conf",
                "/mnt/us/default-kindle-low-power-rollback/rollback.sh",
            },
        )
        combined = "\n".join(self.bundle.values())
        self.assertIn(
            "http://192.168.68.167:8765/device/default-kindle/image.png",
            combined,
        )
        for legacy in (
            'cat > "/mnt/us/dashboard/start-dashboard.sh"',
            'cat > "/mnt/us/dashboard/refresh.sh"',
            'cat > "/mnt/us/dashboard/refresh-once.sh"',
        ):
            self.assertNotIn(legacy, combined)

    def test_cycle_uses_powerd_rtc_without_loop_or_interval_sleep(self):
        cycle = self.bundle["/mnt/us/dashboard/low-power-cycle.sh"]
        self.assertIn("com.lab126.powerd rtcWakeup", cycle)
        self.assertNotIn("while true", cycle)
        self.assertNotIn('/bin/sleep "$INTERVAL_SECONDS"', cycle)
        self.assertNotIn("wakealarm", cycle)

    def test_suspend_gate_checks_every_recovery_prerequisite(self):
        cycle = self.bundle["/mnt/us/dashboard/low-power-cycle.sh"]
        for required in (
            "DISABLE_LOW_POWER",
            "NOAUTOSTART",
            "low-power-wake-handler.sh",
            "/mnt/us/default-kindle-low-power-rollback/rollback.sh",
            "rtcWakeup",
            "cycle.lock",
            "pgrep wget",
            "pgrep curl",
        ):
            with self.subTest(required=required):
                self.assertIn(required, cycle)

    def test_refresh_uses_http_validators_and_skips_eips_on_304(self):
        refresh = self.bundle[
            "/mnt/us/dashboard/low-power-refresh-once.sh"
        ]
        for required in (
            "If-None-Match",
            "If-Modified-Since",
            "HTTP_STATUS=304",
            "image unchanged",
            "/usr/sbin/eips -g",
            "trap cleanup EXIT HUP INT TERM",
            "file \"$TMP_IMAGE\"",
            "mv -f \"$TMP_IMAGE\" \"$IMAGE\"",
        ):
            with self.subTest(required=required):
                self.assertIn(required, refresh)
        unchanged_block = refresh.split("HTTP_STATUS=304", 1)[1].split(
            "fi", 1
        )[0]
        self.assertNotIn("eips", unchanged_block)

    def test_cron_handler_only_runs_due_cycle(self):
        handler = self.bundle[
            "/mnt/us/dashboard/low-power-wake-handler.sh"
        ]
        self.assertIn("next-cycle-due", handler)
        self.assertIn('if [ "$NOW" -lt "$DUE" ]', handler)
        self.assertNotIn("rtcWakeup", handler)

        deployment = build_low_power_deployment(
            device(), self.config, "192.168.68.167", 8765
        )
        self.assertEqual(
            deployment.cron_line,
            "* * * * * /mnt/us/dashboard/low-power-wake-handler.sh",
        )

    def test_upstart_starts_only_first_boot_cycle(self):
        upstart = self.bundle[
            "/etc/upstart/default-kindle-low-power.conf"
        ]
        self.assertIn("low-power-cycle.sh --boot", upstart)
        self.assertNotIn("refresh.sh", upstart)

    def test_deployment_manifest_has_modes_and_default_interval(self):
        deployment = build_low_power_deployment(
            device(), self.config, "192.168.68.167", 8765
        )
        self.assertEqual(deployment.device_id, "default-kindle")
        self.assertEqual(deployment.interval_seconds, 3600)
        self.assertTrue(
            all(mode == 0o755 for mode in deployment.file_modes.values())
        )


if __name__ == "__main__":
    unittest.main()

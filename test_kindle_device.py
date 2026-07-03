#!/usr/bin/env python3
import subprocess
import unittest
from unittest import mock

import kindle_device


class KindleDeviceTests(unittest.TestCase):
    def setUp(self):
        self.device = kindle_device.KindleDevice()

    def completed(self, stdout=""):
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    def test_whitelisted_action_uses_fixed_ssh_argument_list(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run:
            message = self.device.run_action("refresh")

        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(
            args[0][-1],
            "/mnt/us/dashboard/refresh-once.sh",
        )
        self.assertNotIn("shell", kwargs)
        self.assertEqual(message, "Dashboard refreshed")

    def test_unsupported_action_is_rejected_before_subprocess(self):
        with mock.patch("kindle_device.subprocess.run") as run:
            with self.assertRaises(ValueError):
                self.device.run_action("anything")
        run.assert_not_called()

    def test_brightness_validation_accepts_only_integer_zero_to_24(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            side_effect=[self.completed(), self.completed("12\n")],
        ) as run:
            self.assertEqual(self.device.set_light(12), 12)
        self.assertEqual(
            run.call_args_list[0].args[0][-1],
            "lipc-set-prop com.lab126.powerd flIntensity 12",
        )

        for value in (-1, 25, "8", 8.0, True, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.device.set_light(value)

    def test_light_read_parses_only_valid_integer(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed("8\n"),
        ):
            self.assertEqual(self.device.get_light(), 8)
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed("unexpected\n"),
        ):
            with self.assertRaises(kindle_device.DeviceError):
                self.device.get_light()

    def test_push_generates_then_runs_one_shot_refresh(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            side_effect=[self.completed(), self.completed()],
        ) as run:
            message = self.device.push()

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [str(kindle_device.RUN_DASHBOARD)],
        )
        self.assertEqual(
            run.call_args_list[1].args[0][-1],
            "/mnt/us/dashboard/refresh-once.sh",
        )
        self.assertEqual(message, "Dashboard generated and pushed")

    def test_timeout_returns_safe_device_error(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ssh"], 8),
        ):
            with self.assertRaisesRegex(
                kindle_device.DeviceError,
                "timed out",
            ):
                self.device.run_action("home")

    def test_restart_requires_literal_confirmation(self):
        with mock.patch("kindle_device.subprocess.run") as run:
            for value in (None, "", "restart", True):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        self.device.restart(value)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

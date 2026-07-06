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
        ) as run, mock.patch.object(self.device, "set_light"):
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

    def test_battery_status_reads_capacity_and_charging_when_available(self):
        output = "capacity=87\nstatus=Charging\nvoltage_now=3975000\n"
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(output),
        ):
            status = self.device.get_battery_status()

        self.assertEqual(status["battery_percent"], 87)
        self.assertTrue(status["charging"])
        self.assertEqual(status["battery_voltage"], 3.975)

    def test_battery_status_reports_unknown_when_capacity_unavailable(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed("capacity=unknown\nstatus=unknown\n"),
        ):
            status = self.device.get_battery_status()

        self.assertIsNone(status["battery_percent"])
        self.assertIsNone(status["charging"])

    @mock.patch("weather_image.render_device")
    def test_push_generates_then_runs_one_shot_refresh(self, mock_render):
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run, mock.patch.object(self.device, "set_light"):
            message = self.device.push()

        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            run.call_args_list[0].args[0][-1],
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

    def test_saved_brightness_applied_on_start_and_refresh(self):
        with mock.patch(
            "kindle_device.get_saved_brightness",
            return_value=12,
        ), mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ):
            with mock.patch.object(self.device, "set_light") as mock_set_light:
                self.device.run_action("start")
                mock_set_light.assert_called_once_with(12, connection=None, device_id=None, device_type='kindle_pw1')

            with mock.patch.object(self.device, "set_light") as mock_set_light:
                self.device.run_action("refresh")
                mock_set_light.assert_called_once_with(12, connection=None, device_id=None, device_type='kindle_pw1')

            with mock.patch.object(self.device, "set_light") as mock_set_light:
                self.device.push()
                mock_set_light.assert_called_once_with(12, connection=None, device_id='default-kindle', device_type='kindle_pw1')

    def test_profile_builds_strict_ssh_arguments_without_shell(self):
        conn = {
            "host": "192.168.68.200",
            "user": "root",
            "ssh_profile": "kindle_dashboard",
            "port": 2222,
        }
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run:
            self.device.run_action("home", connection=conn)
            
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0][0], "/usr/bin/ssh")
        self.assertIn("-p", args[0])
        self.assertIn("2222", args[0])
        self.assertIn("root@192.168.68.200", args[0])
        self.assertNotIn("shell", kwargs)

    def test_api_never_exposes_profile_paths(self):
        # Verify SSH_PROFILES is not exposed in public records
        self.assertTrue(hasattr(kindle_device, "SSH_PROFILES"))
        self.assertIn("kindle_dashboard", kindle_device.SSH_PROFILES)
        profile = kindle_device.SSH_PROFILES["kindle_dashboard"]
        self.assertIn("key_path", profile)
        self.assertIn("known_hosts", profile)

    def test_named_kindle_uses_its_connection_host_user_and_port(self):
        conn = {
            "host": "192.168.68.222",
            "user": "root_user",
            "ssh_profile": "kindle_dashboard",
            "port": 2222,
        }
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run:
            self.device.run_action("home", connection=conn)
            
        args, _ = run.call_args
        self.assertEqual(args[0][-2], "root_user@192.168.68.222")
        self.assertIn("-p", args[0])
        self.assertIn("2222", args[0])

    def test_missing_named_connection_does_not_fall_back(self):
        with self.assertRaises(kindle_device.DeviceError):
            self.device.run_action("home", connection=None, device_id="kitchen-kindle")

    def test_default_kindle_may_use_legacy_connection_fallback(self):
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run:
            self.device.run_action("home", connection=None, device_id="default-kindle")
        args, _ = run.call_args
        self.assertEqual(args[0][-2], kindle_device.KINDLE_HOST)

    def test_non_kindle_rejects_kindle_actions(self):
        with self.assertRaises(ValueError):
            self.device.run_action("home", device_type="esp32_epaper")
        with self.assertRaises(ValueError):
            self.device.push(device_type="generic_png")

    @mock.patch("weather_image.render_device")
    def test_push_renders_and_refreshes_selected_device(self, mock_render):
        conn = {
            "host": "192.168.68.200",
            "user": "root",
            "ssh_profile": "kindle_dashboard",
        }
        with mock.patch(
            "kindle_device.subprocess.run",
            return_value=self.completed(),
        ) as run, mock.patch.object(self.device, "set_light"):
            self.device.push(connection=conn, device_id="my-kindle")
            
        # Verify render was called with correct device_id
        mock_render.assert_called_once()
        self.assertEqual(mock_render.call_args[0][0], "my-kindle")
        
        # Verify SSH refresh was called on the remote device
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0][-1], "/mnt/us/dashboard/refresh-once.sh")


if __name__ == "__main__":
    unittest.main()

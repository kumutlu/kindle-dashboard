import unittest
from pathlib import Path


class ScheduledRenderContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = Path("run_dashboard.sh").read_text(encoding="utf-8")

    def test_wrapper_delegates_once_to_tracked_scheduler(self):
        self.assertEqual(self.script.count("python3 scheduled_render.py"), 1)
        self.assertIn("exec python3 scheduled_render.py", self.script)

    def test_scheduler_does_not_render_other_devices(self):
        for device_id in ("kitchen-kindle", "kindle-131"):
            with self.subTest(device_id=device_id):
                self.assertNotIn(f"--device {device_id}", self.script)

    def test_wrapper_does_not_run_untracked_render_commands(self):
        self.assertNotIn("python3 weather_image.py", self.script)


if __name__ == "__main__":
    unittest.main()

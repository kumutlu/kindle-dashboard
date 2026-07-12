import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import scheduled_render


class ScheduledRenderTests(unittest.TestCase):
    @mock.patch("scheduled_render.subprocess.run")
    def test_production_targets_render_once_in_order(self, run):
        run.return_value.returncode = 0

        result = scheduled_render.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [scheduled_render.sys.executable, "weather_image.py"],
                [
                    scheduled_render.sys.executable,
                    "weather_image.py",
                    "--device",
                    "default-kindle",
                ],
                [
                    scheduled_render.sys.executable,
                    "weather_image.py",
                    "--device",
                    "kindle-131",
                ],
            ],
        )

    @mock.patch("scheduled_render.subprocess.run")
    def test_both_renders_run_and_failure_is_reported(self, run):
        run.side_effect = [
            mock.Mock(returncode=3),
            mock.Mock(returncode=0),
            mock.Mock(returncode=0),
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = scheduled_render.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_count, 3)
        self.assertIn("render_failed target=legacy rc=3", stderr.getvalue())
        self.assertIn(
            "render_complete target=default-kindle", stdout.getvalue()
        )
        self.assertIn("render_complete target=kindle-131", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

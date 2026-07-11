#!/usr/bin/env python3
import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import special_events


TEST_PNG_DATA_URL = (
    "data:image/png;base64,"
    + base64.b64encode(
        (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00"
            b"\x3a\x7e\x9b\x55\x00\x00\x00\x0bIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01"
            b"\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    ).decode("ascii")
)


class SpecialEventsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_load_and_render_event_image(self):
        event = special_events.create_event(
            self.root,
            {
                "title": "Test Event",
                "start_date": "2026-07-10",
                "end_date": "2026-07-12",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle"],
                "enabled": True,
            },
            ["default-kindle"],
            now=datetime(2026, 7, 10, 12, 0, 0),
        )
        special_events.save_events(self.root, [event])
        loaded = special_events.load_events(self.root, ["default-kindle"])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].title, "Test Event")
        self.assertTrue((self.root / event.image_path).exists())

        output = self.root / "rendered.png"
        special_events.render_event_image(
            self.root / event.image_path,
            output,
            (600, 800),
            kt4_safe=True,
        )
        with Image.open(output) as image:
            self.assertEqual(image.size, (600, 800))
            self.assertEqual(image.mode, "L")

    def test_active_event_for_device_respects_target_and_date_range(self):
        event = special_events.create_event(
            self.root,
            {
                "title": "Test Event",
                "start_date": "2026-07-10",
                "end_date": "2026-07-12",
                "image_data": TEST_PNG_DATA_URL,
                "devices": ["default-kindle"],
                "enabled": True,
            },
            ["default-kindle", "kitchen-kindle"],
        )
        special_events.save_events(self.root, [event])
        device = SimpleNamespace(id="default-kindle")

        active = special_events.active_event_for_device(
            self.root,
            device,
            "Europe/London",
            ["default-kindle", "kitchen-kindle"],
            now=datetime(2026, 7, 11, 12, 0, 0),
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.id, event.id)

        expired = special_events.active_event_for_device(
            self.root,
            device,
            "Europe/London",
            ["default-kindle", "kitchen-kindle"],
            now=datetime(2026, 7, 13, 12, 0, 0),
        )
        self.assertIsNone(expired)

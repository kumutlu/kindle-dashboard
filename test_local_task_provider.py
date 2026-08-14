#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from providers.local_task_provider import LocalTaskProvider, TaskNotFoundError


class LocalTaskProviderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.provider = LocalTaskProvider(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_missing_store_is_empty_without_creating_a_file(self):
        self.assertEqual(self.provider.list_tasks("default-kindle"), [])
        self.assertFalse(
            (self.root / "devices/default-kindle/tasks.json").exists()
        )

    def test_create_edit_delete_and_device_isolation(self):
        milk = self.provider.create_task("default-kindle", "  Buy milk  ")
        parcel = self.provider.create_task("default-kindle", "Pick up parcel")
        kitchen = self.provider.create_task("kitchen-kindle", "Wipe counter")

        self.assertEqual(milk.title, "Buy milk")
        self.assertEqual(milk.sort_order, 0)
        self.assertEqual(parcel.sort_order, 1)
        self.assertNotEqual(milk.id, parcel.id)
        self.assertTrue(milk.created_at.endswith("Z"))
        self.assertEqual(
            [task.title for task in self.provider.list_tasks("kitchen-kindle")],
            [kitchen.title],
        )

        updated = self.provider.update_task(
            "default-kindle", milk.id, title="Buy oat milk"
        )
        self.assertEqual(updated.title, "Buy oat milk")
        deleted = self.provider.delete_task("default-kindle", parcel.id)
        self.assertEqual(deleted.id, parcel.id)
        self.assertEqual(
            [task.title for task in self.provider.list_tasks("default-kindle")],
            ["Buy oat milk"],
        )

    def test_completion_moves_task_to_end_of_destination_group(self):
        first = self.provider.create_task("default-kindle", "First")
        second = self.provider.create_task("default-kindle", "Second")
        done = self.provider.create_task("default-kindle", "Already done")
        self.provider.update_task(
            "default-kindle", done.id, completed=True
        )

        moved = self.provider.update_task(
            "default-kindle", first.id, completed=True
        )
        tasks = self.provider.list_tasks("default-kindle")
        self.assertEqual(
            [(task.title, task.completed, task.sort_order) for task in tasks],
            [
                ("Second", False, 0),
                ("Already done", True, 0),
                ("First", True, 1),
            ],
        )
        self.assertEqual(moved.sort_order, 1)

        restored = self.provider.update_task(
            "default-kindle", first.id, completed=False
        )
        self.assertEqual(restored.sort_order, 1)
        self.assertEqual(
            [task.title for task in self.provider.list_tasks("default-kindle")],
            ["Second", "First", "Already done"],
        )

    def test_reorder_requires_exact_ids_from_one_status_group(self):
        first = self.provider.create_task("default-kindle", "First")
        second = self.provider.create_task("default-kindle", "Second")
        done = self.provider.create_task("default-kindle", "Done")
        self.provider.update_task("default-kindle", done.id, completed=True)

        reordered = self.provider.reorder_tasks(
            "default-kindle", False, [second.id, first.id]
        )
        self.assertEqual(
            [task.title for task in reordered],
            ["Second", "First", "Done"],
        )

        for invalid in ([first.id], [first.id, first.id], [first.id, done.id]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.provider.reorder_tasks(
                        "default-kindle", False, invalid
                    )

    def test_validation_and_missing_task_do_not_change_storage(self):
        task = self.provider.create_task("default-kindle", "Safe")
        path = self.root / "devices/default-kindle/tasks.json"
        before = path.read_bytes()

        for title in ("", " ", "x" * 201, 42):
            with self.subTest(title=title):
                with self.assertRaises(ValueError):
                    self.provider.create_task("default-kindle", title)
                self.assertEqual(path.read_bytes(), before)

        with self.assertRaises(TaskNotFoundError):
            self.provider.update_task(
                "default-kindle", "00000000-0000-0000-0000-000000000000",
                title="Missing",
            )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.provider.list_tasks("default-kindle")[0].id, task.id)

    def test_invalid_device_id_and_corrupt_store_are_rejected(self):
        for device_id in ("../escape", "UPPERCASE", ""):
            with self.subTest(device_id=device_id):
                with self.assertRaises(ValueError):
                    self.provider.list_tasks(device_id)

        path = self.root / "devices/default-kindle/tasks.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"version": 1, "tasks": [{"bad": true}]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.provider.list_tasks("default-kindle")

    def test_persisted_schema_contains_exact_task_fields(self):
        self.provider.create_task("default-kindle", "Schema")
        data = json.loads(
            (self.root / "devices/default-kindle/tasks.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            set(data["tasks"][0]),
            {"id", "title", "completed", "sort_order", "created_at"},
        )


if __name__ == "__main__":
    unittest.main()

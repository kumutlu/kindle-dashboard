#!/usr/bin/env python3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from providers.task_provider import Task, TaskProvider
from themes.theme import ThemeRenderContext
from themes.todo.theme import TodoTheme


class FakeTaskProvider(TaskProvider):
    def __init__(self, tasks):
        self.tasks = tasks
        self.device_ids = []

    def list_tasks(self, device_id):
        self.device_ids.append(device_id)
        return list(self.tasks)


def make_task(index, title=None, completed=False, sort_order=None):
    return Task(
        id=f"00000000-0000-0000-0000-{index:012d}",
        title=title or f"Task {index}",
        completed=completed,
        sort_order=index if sort_order is None else sort_order,
        created_at=f"2026-07-20T09:{index:02d}:00Z",
    )


class TodoThemeTests(unittest.TestCase):
    def now(self, timezone):
        return datetime(2026, 7, 20, 9, 30, tzinfo=ZoneInfo(timezone))

    def test_visible_tasks_are_incomplete_first_and_limited_to_eight(self):
        tasks = [
            make_task(9, completed=True, sort_order=0),
            make_task(3, completed=False, sort_order=3),
            make_task(1, completed=False, sort_order=1),
            make_task(8, completed=True, sort_order=1),
        ] + [make_task(index + 20) for index in range(8)]
        theme = TodoTheme(FakeTaskProvider(tasks), now_factory=self.now)

        visible = theme.visible_tasks("kitchen-kindle")

        self.assertEqual(len(visible), 8)
        self.assertTrue(all(not task.completed for task in visible))
        self.assertEqual(visible[0].title, "Task 1")

    def test_render_uses_provider_and_returns_binary_image_for_both_kindles(self):
        provider = FakeTaskProvider([
            make_task(1, "Buy milk"),
            make_task(2, "Call GP", completed=True),
        ])
        theme = TodoTheme(provider, now_factory=self.now)

        for resolution in ((758, 1024), (600, 800)):
            with self.subTest(resolution=resolution):
                context = ThemeRenderContext(
                    device_id="kitchen-kindle",
                    resolution=resolution,
                    timezone="Europe/London",
                )
                image = theme.render({"theme": "todo"}, context)
                self.assertEqual(image.mode, "1")
                self.assertEqual(image.size, resolution)
                self.assertEqual(
                    {value for _, value in image.convert("L").getcolors()},
                    {0, 255},
                )
        self.assertEqual(provider.device_ids, ["kitchen-kindle"] * 2)

    def test_empty_and_long_title_render_without_error(self):
        for tasks in ([], [make_task(1, "A very long task title " * 30)]):
            with self.subTest(empty=not tasks):
                theme = TodoTheme(FakeTaskProvider(tasks), now_factory=self.now)
                image = theme.render(
                    {"theme": "todo"},
                    ThemeRenderContext(
                        device_id="default-kindle",
                        resolution=(758, 1024),
                        timezone="Europe/London",
                    ),
                )
                self.assertEqual(image.size, (758, 1024))

    def test_counts_include_tasks_hidden_by_eight_row_limit(self):
        tasks = [make_task(index) for index in range(10)] + [
            make_task(20 + index, completed=True) for index in range(3)
        ]
        theme = TodoTheme(FakeTaskProvider(tasks), now_factory=self.now)
        self.assertEqual(theme.counts(tasks), (10, 3))


if __name__ == "__main__":
    unittest.main()

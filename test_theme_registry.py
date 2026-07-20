#!/usr/bin/env python3
import unittest

from PIL import Image

from themes.registry import ThemeRegistry
from themes.theme import Theme, ThemeRenderContext


class FakeTheme(Theme):
    def __init__(self):
        self.calls = []

    def render(self, config, context):
        self.calls.append((config, context))
        return Image.new("1", context.resolution, 1)


class ThemeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.context = ThemeRenderContext(
            device_id="default-kindle",
            resolution=(758, 1024),
            timezone="Europe/London",
        )

    def test_register_lookup_and_render(self):
        registry = ThemeRegistry()
        theme = FakeTheme()
        registry.register("todo", theme)

        image = registry.render("todo", {"theme": "todo"}, self.context)

        self.assertIs(registry.get("todo"), theme)
        self.assertEqual(registry.theme_ids(), ("todo",))
        self.assertEqual(image.size, (758, 1024))
        self.assertEqual(len(theme.calls), 1)

    def test_duplicate_unknown_and_invalid_results_are_rejected(self):
        registry = ThemeRegistry()
        registry.register("todo", FakeTheme())
        with self.assertRaises(ValueError):
            registry.register("todo", FakeTheme())
        with self.assertRaises(ValueError):
            registry.render("missing", {}, self.context)

        class BadTheme(Theme):
            def render(self, config, context):
                return "not an image"

        registry.register("bad", BadTheme())
        with self.assertRaises(TypeError):
            registry.render("bad", {}, self.context)


if __name__ == "__main__":
    unittest.main()

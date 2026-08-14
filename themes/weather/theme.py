"""Adapter that places existing weather renderers behind the Theme contract."""

from themes.theme import Theme


class WeatherTheme(Theme):
    def __init__(self, render_callback):
        self.render_callback = render_callback

    def render(self, config, context):
        return self.render_callback(config, context)

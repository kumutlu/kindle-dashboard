"""Theme registration and renderer dispatch."""

from PIL import Image

from .theme import Theme


class ThemeRegistry:
    def __init__(self):
        self._themes = {}

    def register(self, theme_id, theme):
        if not isinstance(theme_id, str) or not theme_id:
            raise ValueError("theme id is required")
        if not isinstance(theme, Theme):
            raise TypeError("theme must implement Theme")
        if theme_id in self._themes:
            raise ValueError(f"theme is already registered: {theme_id}")
        self._themes[theme_id] = theme

    def get(self, theme_id):
        try:
            return self._themes[theme_id]
        except KeyError as exc:
            raise ValueError("theme renderer is not available") from exc

    def theme_ids(self):
        return tuple(self._themes)

    def render(self, theme_id, config, context):
        image = self.get(theme_id).render(config, context)
        if not isinstance(image, Image.Image):
            raise TypeError("theme renderer must return a Pillow image")
        if image.size != tuple(context.resolution):
            raise ValueError("generated image does not match device resolution")
        return image

"""Common interface for all dashboard themes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeRenderContext:
    device_id: str
    resolution: tuple
    timezone: str
    status_bar_safe_area_px: int = 0


class Theme(ABC):
    @abstractmethod
    def render(self, config, context):
        """Return a Pillow image for the supplied device context."""
        raise NotImplementedError

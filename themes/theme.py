"""Common interface for all dashboard themes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeRenderContext:
    device_id: str
    resolution: tuple
    timezone: str


class Theme(ABC):
    @abstractmethod
    def render(self, config, context):
        """Return a Pillow image for the supplied device context."""
        raise NotImplementedError

"""Provider-neutral task values and read interface."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    completed: bool
    sort_order: int
    created_at: str

    def to_dict(self):
        return asdict(self)


class TaskProvider(ABC):
    @abstractmethod
    def list_tasks(self, device_id):
        """Return normalized tasks for a device."""
        raise NotImplementedError

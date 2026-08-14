"""Atomic per-device JSON implementation of the task provider."""

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .task_provider import Task, TaskProvider


DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TASK_FIELDS = {"id", "title", "completed", "sort_order", "created_at"}
MAX_TITLE_LENGTH = 200
_UNSET = object()


class TaskNotFoundError(KeyError):
    """A requested local task does not exist."""


class LocalTaskProvider(TaskProvider):
    def __init__(self, project_root):
        self.project_root = Path(project_root).resolve()
        self.devices_root = self.project_root / "devices"

    def _paths(self, device_id):
        if not isinstance(device_id, str) or not DEVICE_ID_RE.fullmatch(device_id):
            raise ValueError("invalid device id")
        device_dir = (self.devices_root / device_id).resolve()
        try:
            device_dir.relative_to(self.devices_root.resolve())
        except ValueError as exc:
            raise ValueError("invalid device id") from exc
        return device_dir / "tasks.json", device_dir / ".tasks.lock"

    @contextmanager
    def _locked(self, device_id, create=False):
        path, lock_path = self._paths(device_id)
        if not create and not path.parent.exists():
            yield path
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield path
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _validated_title(title):
        if not isinstance(title, str):
            raise ValueError("task title must be text")
        title = title.strip()
        if not title or len(title) > MAX_TITLE_LENGTH:
            raise ValueError("task title must contain 1-200 characters")
        return title

    @staticmethod
    def _validated_task(value):
        if not isinstance(value, dict) or set(value) != TASK_FIELDS:
            raise ValueError("task fields do not match the supported schema")
        try:
            uuid.UUID(value["id"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("task id is invalid") from exc
        title = LocalTaskProvider._validated_title(value["title"])
        completed = value["completed"]
        sort_order = value["sort_order"]
        created_at = value["created_at"]
        if not isinstance(completed, bool):
            raise ValueError("task completed must be true or false")
        if isinstance(sort_order, bool) or not isinstance(sort_order, int) or sort_order < 0:
            raise ValueError("task sort_order must be a non-negative integer")
        if not isinstance(created_at, str) or not created_at.endswith("Z"):
            raise ValueError("task created_at is invalid")
        try:
            datetime.fromisoformat(created_at[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("task created_at is invalid") from exc
        return Task(
            id=value["id"],
            title=title,
            completed=completed,
            sort_order=sort_order,
            created_at=created_at,
        )

    def _read(self, path):
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("task data is invalid") from exc
        if not isinstance(value, dict) or set(value) != {"version", "tasks"}:
            raise ValueError("task data fields do not match the supported schema")
        if value["version"] != 1 or not isinstance(value["tasks"], list):
            raise ValueError("task data version is unsupported")
        tasks = [self._validated_task(task) for task in value["tasks"]]
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("task ids must be unique")
        return tasks

    @staticmethod
    def _sorted(tasks):
        return sorted(
            tasks,
            key=lambda task: (
                task.completed,
                task.sort_order,
                task.created_at,
                task.id,
            ),
        )

    @staticmethod
    def _normalize(tasks):
        normalized = []
        for completed in (False, True):
            group = sorted(
                (task for task in tasks if task.completed is completed),
                key=lambda task: (task.sort_order, task.created_at, task.id),
            )
            normalized.extend(
                Task(
                    id=task.id,
                    title=task.title,
                    completed=task.completed,
                    sort_order=index,
                    created_at=task.created_at,
                )
                for index, task in enumerate(group)
            )
        return normalized

    @staticmethod
    def _write(path, tasks):
        payload = {
            "version": 1,
            "tasks": [task.to_dict() for task in tasks],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o644)
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def list_tasks(self, device_id):
        with self._locked(device_id) as path:
            return self._sorted(self._read(path))

    def create_task(self, device_id, title):
        title = self._validated_title(title)
        with self._locked(device_id, create=True) as path:
            tasks = self._normalize(self._read(path))
            task = Task(
                id=str(uuid.uuid4()),
                title=title,
                completed=False,
                sort_order=sum(not item.completed for item in tasks),
                created_at=(
                    datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
            )
            tasks.append(task)
            tasks = self._normalize(tasks)
            self._write(path, tasks)
            return next(item for item in tasks if item.id == task.id)

    def update_task(self, device_id, task_id, title=_UNSET, completed=_UNSET):
        if title is _UNSET and completed is _UNSET:
            raise ValueError("task update requires title or completed")
        if title is not _UNSET:
            title = self._validated_title(title)
        if completed is not _UNSET and not isinstance(completed, bool):
            raise ValueError("task completed must be true or false")
        with self._locked(device_id, create=True) as path:
            tasks = self._normalize(self._read(path))
            current = next((task for task in tasks if task.id == task_id), None)
            if current is None:
                raise TaskNotFoundError(task_id)
            destination_completed = (
                current.completed if completed is _UNSET else completed
            )
            destination_order = current.sort_order
            if destination_completed != current.completed:
                destination_order = sum(
                    task.completed is destination_completed
                    for task in tasks
                    if task.id != current.id
                )
            replacement = Task(
                id=current.id,
                title=current.title if title is _UNSET else title,
                completed=destination_completed,
                sort_order=destination_order,
                created_at=current.created_at,
            )
            tasks = [replacement if task.id == current.id else task for task in tasks]
            tasks = self._normalize(tasks)
            self._write(path, tasks)
            return next(task for task in tasks if task.id == task_id)

    def delete_task(self, device_id, task_id):
        with self._locked(device_id, create=True) as path:
            tasks = self._normalize(self._read(path))
            current = next((task for task in tasks if task.id == task_id), None)
            if current is None:
                raise TaskNotFoundError(task_id)
            tasks = self._normalize([task for task in tasks if task.id != task_id])
            self._write(path, tasks)
            return current

    def reorder_tasks(self, device_id, completed, task_ids):
        if not isinstance(completed, bool):
            raise ValueError("completed must be true or false")
        if not isinstance(task_ids, list) or not all(
            isinstance(task_id, str) for task_id in task_ids
        ):
            raise ValueError("task_ids must be a list of task ids")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_ids must be unique")
        with self._locked(device_id, create=True) as path:
            tasks = self._normalize(self._read(path))
            group = [task for task in tasks if task.completed is completed]
            if set(task_ids) != {task.id for task in group}:
                raise ValueError("task_ids must contain exactly one status group")
            order = {task_id: index for index, task_id in enumerate(task_ids)}
            reordered = [
                Task(
                    id=task.id,
                    title=task.title,
                    completed=task.completed,
                    sort_order=order[task.id] if task.completed is completed else task.sort_order,
                    created_at=task.created_at,
                )
                for task in tasks
            ]
            reordered = self._normalize(reordered)
            self._write(path, reordered)
            return self._sorted(reordered)

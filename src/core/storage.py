from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Any, Sequence
from uuid import uuid4

from .models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class JsonCompositionRepository:
    """Persist actions and tasks as atomically replaced JSON documents."""

    def __init__(
        self,
        *,
        actions_file: Path | None = None,
        tasks_directory: Path | None = None,
    ) -> None:
        self._actions_file = (
            actions_file
            if actions_file is not None
            else _PROJECT_ROOT / "data" / "actions_library.json"
        )
        self._tasks_directory = (
            tasks_directory
            if tasks_directory is not None
            else _PROJECT_ROOT / "data" / "tasks"
        )
        self._lock = RLock()

    @property
    def tasks_directory(self) -> Path:
        return self._tasks_directory

    def load_actions(self) -> list[ActionDefinition]:
        with self._lock:
            if not self._actions_file.is_file():
                return []
            raw_actions = self._read_json(self._actions_file)
            if not isinstance(raw_actions, list):
                raise ValueError("actions library must contain a JSON array")

            needs_save = False
            for raw_action in raw_actions:
                if not isinstance(raw_action, dict):
                    raise ValueError(
                        "each actions library item must be a JSON object"
                    )
                if not raw_action.get("id"):
                    raw_action["id"] = str(uuid4())
                    needs_save = True
            if needs_save:
                self._write_json_atomic(
                    self._actions_file,
                    raw_actions,
                )
            return [
                ActionDefinition.from_dict(raw_action)
                for raw_action in raw_actions
            ]

    def save_actions(
        self,
        actions: Sequence[ActionDefinition],
    ) -> None:
        payload = [action.to_dict() for action in actions]
        with self._lock:
            self._write_json_atomic(self._actions_file, payload)

    def list_task_names(self) -> tuple[str, ...]:
        with self._lock:
            if not self._tasks_directory.is_dir():
                return ()
            return tuple(
                sorted(
                    path.name
                    for path in self._tasks_directory.glob("*.task")
                    if path.is_file()
                )
            )

    def load_task(
        self,
        task_name: str,
    ) -> list[SequenceEntry] | None:
        path = self._task_path(task_name)
        with self._lock:
            if not path.is_file():
                return None
            raw_entries = self._read_json(path)
            if not isinstance(raw_entries, list):
                raise ValueError(
                    f"task {path.name!r} must contain a JSON array"
                )
            entries: list[SequenceEntry] = []
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    raise ValueError(
                        f"task {path.name!r} contains a non-object entry"
                    )
                if raw_entry.get("kind") == "loop":
                    entries.append(LoopBlock.from_dict(raw_entry))
                else:
                    entries.append(SequenceItem.from_dict(raw_entry))
            return entries

    def save_task(
        self,
        task_name: str,
        entries: Sequence[SequenceEntry],
    ) -> str:
        path = self._task_path(task_name)
        payload = [entry.to_dict() for entry in entries]
        with self._lock:
            self._write_json_atomic(path, payload)
        return path.name

    def delete_task(self, task_name: str) -> bool:
        path = self._task_path(task_name)
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            return True

    def rename_task(
        self,
        task_name: str,
        new_task_name: str,
    ) -> tuple[str, str]:
        old_path = self._task_path(task_name)
        new_path = self._task_path(new_task_name)
        with self._lock:
            if not old_path.is_file():
                raise FileNotFoundError(old_path.name)
            if (
                new_path.is_file()
                and old_path.resolve() != new_path.resolve()
            ):
                raise FileExistsError(new_path.name)
            old_name = old_path.name
            old_path.replace(new_path)
            return old_name, new_path.name

    def _task_path(self, task_name: str) -> Path:
        normalized = Path(str(task_name)).name.strip()
        if not normalized or normalized in {".", ".."}:
            raise ValueError("task name must not be empty")
        path = self._tasks_directory / normalized
        if path.suffix != ".task":
            path = path.with_suffix(".task")
        return path

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            try:
                temporary_file = os.fdopen(
                    descriptor,
                    "w",
                    encoding="utf-8",
                    newline="\n",
                )
            except Exception:
                with suppress(OSError):
                    os.close(descriptor)
                raise
            with temporary_file as file:
                json.dump(
                    payload,
                    file,
                    indent=2,
                    ensure_ascii=False,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

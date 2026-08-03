from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import TypeAlias
from uuid import uuid4

from ..core.models import (
    ActionDefinition,
    SequenceItem,
    SequenceItemStatus,
)
from .composition import CompositionService


@dataclass(frozen=True, slots=True)
class ComposedTask:
    task_name: str


@dataclass(frozen=True, slots=True)
class ComposedAction:
    action: ActionDefinition


ComposerEntry: TypeAlias = ComposedTask | ComposedAction


class TaskComposerService:
    """Own the editable task/action composition draft.

    The persisted action and task catalogs remain owned by ``CompositionService``.
    This service owns only the ordered, transient GUI draft and expands it into
    fresh sequence items at the application boundary.
    """

    def __init__(self, composition: CompositionService) -> None:
        self._composition = composition
        self._entries: list[ComposerEntry] = []
        self._lock = RLock()

    def entries(self) -> tuple[ComposerEntry, ...]:
        with self._lock:
            return tuple(_clone_entry(entry) for entry in self._entries)

    def add_task(self, task_name: str, *, index: int | None = None) -> None:
        normalized = task_name.strip()
        if not normalized:
            raise ValueError("task name must not be empty")
        self._composition.flattened_task(normalized)
        self._insert(ComposedTask(normalized), index)

    def add_action(
        self,
        action: ActionDefinition,
        *,
        index: int | None = None,
    ) -> None:
        self._insert(ComposedAction(_clone_action(action)), index)

    def remove(self, index: int) -> ComposerEntry:
        with self._lock:
            _require_existing_index(index, len(self._entries))
            return _clone_entry(self._entries.pop(index))

    def move(self, from_index: int, to_index: int) -> None:
        with self._lock:
            _require_existing_index(from_index, len(self._entries))
            _require_existing_index(to_index, len(self._entries))
            entry = self._entries.pop(from_index)
            self._entries.insert(to_index, entry)

    def repeat(self, start_index: int, end_index: int, count: int) -> None:
        if isinstance(count, bool) or not isinstance(count, int) or count < 2:
            raise ValueError("repeat count must be an integer of at least 2")
        with self._lock:
            _require_existing_index(start_index, len(self._entries))
            _require_existing_index(end_index, len(self._entries))
            if start_index > end_index:
                raise ValueError("repeat range must be ordered")
            block = [
                _clone_entry(entry)
                for entry in self._entries[start_index : end_index + 1]
            ]
            insert_at = end_index + 1
            additions = [
                _clone_entry(entry)
                for _ in range(count - 1)
                for entry in block
            ]
            self._entries[insert_at:insert_at] = additions

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def step_count(self, entry: ComposerEntry) -> int:
        if isinstance(entry, ComposedAction):
            return 1
        try:
            return len(self._composition.flattened_task(entry.task_name))
        except FileNotFoundError:
            return 0

    def build_sequence(self) -> tuple[SequenceItem, ...]:
        with self._lock:
            entries = tuple(_clone_entry(entry) for entry in self._entries)
        sequence: list[SequenceItem] = []
        for entry in entries:
            if isinstance(entry, ComposedAction):
                sequence.append(_sequence_item(entry.action))
                continue
            try:
                task_items = self._composition.flattened_task(entry.task_name)
            except FileNotFoundError:
                continue
            sequence.extend(_sequence_item(item.definition) for item in task_items)
        return tuple(sequence)

    def _insert(self, entry: ComposerEntry, index: int | None) -> None:
        with self._lock:
            insert_index = len(self._entries) if index is None else index
            _require_insert_index(insert_index, len(self._entries))
            self._entries.insert(insert_index, _clone_entry(entry))


def _sequence_item(action: ActionDefinition) -> SequenceItem:
    return SequenceItem(
        uuid=str(uuid4()),
        definition=_clone_action(action),
        status=SequenceItemStatus.PENDING,
    )


def _clone_action(action: ActionDefinition) -> ActionDefinition:
    return ActionDefinition.from_dict(deepcopy(action.to_dict()))


def _clone_entry(entry: ComposerEntry) -> ComposerEntry:
    if isinstance(entry, ComposedAction):
        return ComposedAction(_clone_action(entry.action))
    return ComposedTask(entry.task_name)


def _require_existing_index(index: int, length: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index < length:
        raise IndexError(f"index {index} is outside composer length {length}")


def _require_insert_index(index: int, length: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index <= length:
        raise IndexError(f"index {index} is outside insertion range 0..{length}")

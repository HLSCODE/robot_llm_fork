from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import uuid4

from ..core.models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)


logger = logging.getLogger(__name__)


class CompositionChangeType(str, Enum):
    ACTIONS = "actions"
    TASKS = "tasks"
    SEQUENCE = "sequence"


@dataclass(frozen=True, slots=True)
class CompositionEvent:
    change_type: CompositionChangeType
    revision: int
    change_revision: int
    origin: str


@dataclass(frozen=True, slots=True)
class TaskSummary:
    name: str
    step_count: int


class CompositionRevisionConflict(RuntimeError):
    """Raised when a stale editor attempts to replace shared state."""


class CompositionRepository(Protocol):
    @property
    def tasks_directory(self) -> Path: ...

    def load_actions(self) -> list[ActionDefinition]: ...

    def save_actions(
        self,
        actions: Sequence[ActionDefinition],
    ) -> None: ...

    def list_task_names(self) -> tuple[str, ...]: ...

    def load_task(
        self,
        task_name: str,
    ) -> list[SequenceEntry] | None: ...

    def save_task(
        self,
        task_name: str,
        entries: Sequence[SequenceEntry],
    ) -> str: ...

    def delete_task(self, task_name: str) -> bool: ...

    def rename_task(
        self,
        task_name: str,
        new_task_name: str,
    ) -> tuple[str, str]: ...


CompositionListener = Callable[[CompositionEvent], None]
Unsubscribe = Callable[[], None]


class CompositionService:
    """Own actions, tasks, and the shared in-memory composition sequence."""

    def __init__(self, repository: CompositionRepository) -> None:
        self._repository = repository
        self._lock = RLock()
        self._actions = [
            _clone_action(action)
            for action in repository.load_actions()
        ]
        self._sequence: list[SequenceEntry] = []
        self._revision = 0
        self._change_revisions = {
            change_type: 0
            for change_type in CompositionChangeType
        }
        self._listeners: dict[str, CompositionListener] = {}

    @property
    def tasks_directory(self) -> Path:
        return self._repository.tasks_directory

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def sequence_revision(self) -> int:
        with self._lock:
            return self._change_revisions[
                CompositionChangeType.SEQUENCE
            ]

    def subscribe(
        self,
        listener: CompositionListener,
    ) -> Unsubscribe:
        token = uuid4().hex
        with self._lock:
            self._listeners[token] = listener

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(token, None)

        return unsubscribe

    def list_actions(self) -> tuple[ActionDefinition, ...]:
        with self._lock:
            return tuple(
                _clone_action(action)
                for action in self._actions
            )

    def get_action(self, action_id: str) -> ActionDefinition:
        with self._lock:
            action = self._find_action_unlocked(action_id)
            return _clone_action(action)

    def create_action(
        self,
        action: ActionDefinition,
        *,
        origin: str,
    ) -> ActionDefinition:
        stored = _validated_action(action)
        with self._lock:
            if any(
                existing.id == stored.id
                for existing in self._actions
            ):
                raise ValueError(
                    f"action id already exists: {stored.id}"
                )
            updated_actions = [
                *self._actions,
                stored,
            ]
            self._repository.save_actions(updated_actions)
            self._actions = updated_actions
            event = self._next_event_unlocked(
                CompositionChangeType.ACTIONS,
                origin,
            )
        self._notify(event)
        return _clone_action(stored)

    def update_action(
        self,
        action_id: str,
        action: ActionDefinition,
        *,
        origin: str,
    ) -> ActionDefinition:
        replacement = _clone_action(action)
        replacement.id = _required_text(action_id, "action id")
        replacement = _validated_action(replacement)
        with self._lock:
            target_index = self._find_action_index_unlocked(action_id)
            updated_actions = list(self._actions)
            updated_actions[target_index] = replacement
            self._repository.save_actions(updated_actions)
            self._actions = updated_actions
            event = self._next_event_unlocked(
                CompositionChangeType.ACTIONS,
                origin,
            )
        self._notify(event)
        return _clone_action(replacement)

    def delete_action(
        self,
        action_id: str,
        *,
        origin: str,
    ) -> ActionDefinition:
        with self._lock:
            target_index = self._find_action_index_unlocked(action_id)
            removed = self._actions[target_index]
            updated_actions = [
                action
                for index, action in enumerate(self._actions)
                if index != target_index
            ]
            self._repository.save_actions(updated_actions)
            self._actions = updated_actions
            event = self._next_event_unlocked(
                CompositionChangeType.ACTIONS,
                origin,
            )
        self._notify(event)
        return _clone_action(removed)

    def sequence_entries(self) -> tuple[SequenceEntry, ...]:
        with self._lock:
            return tuple(_clone_entry(entry) for entry in self._sequence)

    def flattened_sequence(self) -> tuple[SequenceItem, ...]:
        with self._lock:
            return tuple(_flatten_entries(self._sequence))

    def replace_sequence(
        self,
        entries: Sequence[SequenceEntry],
        *,
        origin: str,
        expected_revision: int | None = None,
    ) -> tuple[SequenceEntry, ...]:
        replacement = [_pending_entry(entry) for entry in entries]
        with self._lock:
            current_revision = self._change_revisions[
                CompositionChangeType.SEQUENCE
            ]
            if (
                expected_revision is not None
                and expected_revision != current_revision
            ):
                raise CompositionRevisionConflict(
                    "sequence changed since it was displayed: "
                    f"expected revision {expected_revision}, "
                    f"current revision {current_revision}"
                )
            self._sequence = replacement
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return self.sequence_entries()

    def append_sequence(
        self,
        entries: Sequence[SequenceEntry],
        *,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        additions = [_pending_entry(entry) for entry in entries]
        if not additions:
            raise ValueError("at least one sequence entry is required")
        with self._lock:
            self._sequence.extend(additions)
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return self.sequence_entries()

    def append_action_ids(
        self,
        action_ids: Sequence[str],
        *,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        if not action_ids:
            raise ValueError("at least one action id is required")
        with self._lock:
            definitions = [
                _clone_action(self._find_action_unlocked(action_id))
                for action_id in action_ids
            ]
            additions = [
                SequenceItem.from_definition(definition)
                for definition in definitions
            ]
            self._sequence.extend(additions)
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return self.sequence_entries()

    def remove_sequence_entry(
        self,
        index: int,
        *,
        origin: str,
    ) -> SequenceEntry:
        with self._lock:
            _require_existing_index(index, len(self._sequence))
            removed = self._sequence.pop(index)
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return _clone_entry(removed)

    def move_sequence_entry(
        self,
        from_index: int,
        to_index: int,
        *,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        with self._lock:
            _require_existing_index(from_index, len(self._sequence))
            _require_existing_index(to_index, len(self._sequence))
            entry = self._sequence.pop(from_index)
            self._sequence.insert(to_index, entry)
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return self.sequence_entries()

    def clear_sequence(self, *, origin: str) -> None:
        with self._lock:
            if not self._sequence:
                return
            self._sequence.clear()
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)

    def list_tasks(self) -> tuple[TaskSummary, ...]:
        with self._lock:
            summaries = []
            for task_name in self._repository.list_task_names():
                entries = self._repository.load_task(task_name)
                if entries is None:
                    continue
                summaries.append(
                    TaskSummary(
                        name=task_name,
                        step_count=len(_flatten_entries(entries)),
                    )
                )
            return tuple(summaries)

    def load_task(
        self,
        task_name: str,
    ) -> tuple[SequenceEntry, ...]:
        with self._lock:
            entries = self._required_task_unlocked(task_name)
            return tuple(_pending_entry(entry) for entry in entries)

    def load_task_into_sequence(
        self,
        task_name: str,
        *,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        with self._lock:
            entries = self._required_task_unlocked(task_name)
            if not entries:
                raise ValueError("task is empty")
            self._sequence = [
                _pending_entry(entry)
                for entry in entries
            ]
            event = self._next_event_unlocked(
                CompositionChangeType.SEQUENCE,
                origin,
            )
        self._notify(event)
        return self.sequence_entries()

    def flattened_task(
        self,
        task_name: str,
    ) -> tuple[SequenceItem, ...]:
        with self._lock:
            return tuple(
                _flatten_entries(
                    self._required_task_unlocked(task_name)
                )
            )

    def save_task(
        self,
        task_name: str,
        entries: Sequence[SequenceEntry],
        *,
        origin: str,
    ) -> str:
        stored_entries = [_pending_entry(entry) for entry in entries]
        if not stored_entries:
            raise ValueError("cannot save an empty task")
        with self._lock:
            stored_name = self._repository.save_task(
                task_name,
                stored_entries,
            )
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return stored_name

    def save_current_task(
        self,
        task_name: str,
        *,
        origin: str,
    ) -> str:
        with self._lock:
            if not self._sequence:
                raise ValueError("cannot save an empty sequence")
            stored_name = self._repository.save_task(
                task_name,
                self._sequence,
            )
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return stored_name

    def delete_task(
        self,
        task_name: str,
        *,
        origin: str,
    ) -> str:
        with self._lock:
            if not self._repository.delete_task(task_name):
                raise FileNotFoundError(task_name)
            normalized_name = Path(task_name).with_suffix(".task").name
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return normalized_name

    def rename_task(
        self,
        task_name: str,
        new_task_name: str,
        *,
        origin: str,
    ) -> tuple[str, str]:
        with self._lock:
            names = self._repository.rename_task(
                task_name,
                new_task_name,
            )
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return names

    def insert_task_entries(
        self,
        task_name: str,
        entries: Sequence[SequenceEntry],
        *,
        index: int | None,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        additions = [_pending_entry(entry) for entry in entries]
        if not additions:
            raise ValueError("at least one task entry is required")
        with self._lock:
            task_entries = list(
                self._required_task_unlocked(task_name)
            )
            insert_index = (
                len(task_entries)
                if index is None
                else _require_insert_index(index, len(task_entries))
            )
            task_entries[insert_index:insert_index] = additions
            self._repository.save_task(task_name, task_entries)
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return tuple(_clone_entry(entry) for entry in task_entries)

    def remove_task_entry(
        self,
        task_name: str,
        index: int,
        *,
        origin: str,
    ) -> tuple[SequenceEntry, tuple[SequenceEntry, ...]]:
        with self._lock:
            task_entries = list(
                self._required_task_unlocked(task_name)
            )
            _require_existing_index(index, len(task_entries))
            removed = task_entries.pop(index)
            self._repository.save_task(task_name, task_entries)
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return (
            _clone_entry(removed),
            tuple(_clone_entry(entry) for entry in task_entries),
        )

    def move_task_entry(
        self,
        task_name: str,
        from_index: int,
        to_index: int,
        *,
        origin: str,
    ) -> tuple[SequenceEntry, ...]:
        with self._lock:
            task_entries = list(
                self._required_task_unlocked(task_name)
            )
            _require_existing_index(from_index, len(task_entries))
            _require_existing_index(to_index, len(task_entries))
            entry = task_entries.pop(from_index)
            task_entries.insert(to_index, entry)
            self._repository.save_task(task_name, task_entries)
            event = self._next_event_unlocked(
                CompositionChangeType.TASKS,
                origin,
            )
        self._notify(event)
        return tuple(_clone_entry(entry) for entry in task_entries)

    def _find_action_unlocked(
        self,
        action_id: str,
    ) -> ActionDefinition:
        return self._actions[
            self._find_action_index_unlocked(action_id)
        ]

    def _find_action_index_unlocked(self, action_id: str) -> int:
        required_id = _required_text(action_id, "action id")
        for index, action in enumerate(self._actions):
            if action.id == required_id:
                return index
        raise KeyError(required_id)

    def _required_task_unlocked(
        self,
        task_name: str,
    ) -> list[SequenceEntry]:
        entries = self._repository.load_task(task_name)
        if entries is None:
            raise FileNotFoundError(task_name)
        return entries

    def _next_event_unlocked(
        self,
        change_type: CompositionChangeType,
        origin: str,
    ) -> CompositionEvent:
        self._revision += 1
        self._change_revisions[change_type] += 1
        return CompositionEvent(
            change_type=change_type,
            revision=self._revision,
            change_revision=self._change_revisions[change_type],
            origin=origin.strip() or "unknown",
        )

    def _notify(self, event: CompositionEvent) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception(
                    "Composition listener failed: change=%s revision=%s",
                    event.change_type.value,
                    event.revision,
                )


def _validated_action(action: ActionDefinition) -> ActionDefinition:
    cloned = _clone_action(action)
    cloned.id = _required_text(cloned.id, "action id")
    cloned.name = _required_text(cloned.name, "action name")
    if not isinstance(cloned.parameters, dict):
        raise TypeError("action parameters must be a dictionary")
    return cloned


def _required_text(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _clone_action(action: ActionDefinition) -> ActionDefinition:
    return ActionDefinition.from_dict(deepcopy(action.to_dict()))


def _clone_entry(entry: SequenceEntry) -> SequenceEntry:
    if isinstance(entry, LoopBlock):
        return LoopBlock.from_dict(deepcopy(entry.to_dict()))
    if isinstance(entry, SequenceItem):
        return SequenceItem.from_dict(deepcopy(entry.to_dict()))
    raise TypeError(
        f"unsupported sequence entry: {type(entry).__name__}"
    )


def _pending_entry(entry: SequenceEntry) -> SequenceEntry:
    cloned = _clone_entry(entry)
    if isinstance(cloned, LoopBlock):
        cloned.current_iteration = 0
        for child in cloned.items:
            child.status = SequenceItemStatus.PENDING
    else:
        cloned.status = SequenceItemStatus.PENDING
    return cloned


def _flatten_entries(
    entries: Sequence[SequenceEntry],
) -> list[SequenceItem]:
    flattened: list[SequenceItem] = []
    for entry in entries:
        if isinstance(entry, LoopBlock):
            for _ in range(entry.repeat_count):
                flattened.extend(
                    _pending_entry(child)
                    for child in entry.items
                )
            continue
        flattened.append(_pending_entry(entry))
    return flattened


def _require_existing_index(index: int, length: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index < length:
        raise IndexError(
            f"index {index} is outside sequence length {length}"
        )
    return index


def _require_insert_index(index: int, length: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index <= length:
        raise IndexError(
            f"index {index} is outside insertion range 0..{length}"
        )
    return index

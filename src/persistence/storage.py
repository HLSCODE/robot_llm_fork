from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Sequence

from .json_documents import (
    CollectionDocumentSpec,
    JsonDocumentSchemaError,
    load_collection_document,
    migrate_collection_document,
    read_json_document,
    write_collection_document,
    write_json_atomic,
)
from ..domain.models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
)
from ..domain.action_schema import validate_action_parameters
from ..domain.workflow import WorkflowDocument, WorkflowDocumentError


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTION_LIBRARY_DOCUMENT = CollectionDocumentSpec(
    schema="robot_llm.actions",
    collection_key="actions",
    legacy_kind="list",
    current_version=2,
    schema_reference="../schemas/action-library.schema.json",
)
ACTION_LIBRARY_FILE_NAME = "library.json"
TASK_DOCUMENT = CollectionDocumentSpec(
    schema="robot_llm.task",
    collection_key="entries",
    legacy_kind="list",
)
WORKFLOW_FILE_SUFFIX = ".workflow"
WORKFLOW_DRAFT_FILE_NAME = ".workflow-draft"


class JsonCompositionRepository:
    """Persist actions and tasks as atomically replaced JSON documents."""

    def __init__(
        self,
        *,
        actions_directory: Path | None = None,
        tasks_directory: Path | None = None,
    ) -> None:
        self._actions_directory = (
            actions_directory
            if actions_directory is not None
            else _PROJECT_ROOT / "data" / "actions"
        )
        self._tasks_directory = (
            tasks_directory if tasks_directory is not None else _PROJECT_ROOT / "data" / "tasks"
        )
        self._lock = RLock()

    @property
    def tasks_directory(self) -> Path:
        return self._tasks_directory

    def load_actions(self) -> list[ActionDefinition]:
        with self._lock:
            actions_file = self._actions_path()
            if not actions_file.is_file():
                return []
            document = load_collection_document(
                actions_file,
                ACTION_LIBRARY_DOCUMENT,
            )
            if document.requires_migration:
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} must use the versioned action-library schema"
                )
            return self._parse_actions(actions_file, document.collection)

    def save_actions(
        self,
        actions: Sequence[ActionDefinition],
    ) -> None:
        payload = [action.to_dict() for action in actions]
        with self._lock:
            write_collection_document(
                self._actions_path(),
                ACTION_LIBRARY_DOCUMENT,
                payload,
            )

    def list_task_names(self) -> tuple[str, ...]:
        with self._lock:
            if not self._tasks_directory.is_dir():
                return ()
            return tuple(
                sorted(path.name for path in self._tasks_directory.glob("*.task") if path.is_file())
            )

    def list_workflow_names(self) -> tuple[str, ...]:
        with self._lock:
            if not self._tasks_directory.is_dir():
                return ()
            return tuple(
                sorted(
                    path.name
                    for path in self._tasks_directory.glob(
                        f"*{WORKFLOW_FILE_SUFFIX}"
                    )
                    if path.is_file()
                )
            )

    def load_workflow(
        self,
        workflow_name: str,
    ) -> WorkflowDocument | None:
        path = self._workflow_path(workflow_name)
        with self._lock:
            if not path.is_file():
                return None
            try:
                return WorkflowDocument.from_dict(read_json_document(path))
            except WorkflowDocumentError as exc:
                raise JsonDocumentSchemaError(
                    f"{path.name} is not a valid workflow document"
                ) from exc

    def save_workflow(
        self,
        workflow_name: str,
        document: WorkflowDocument,
    ) -> str:
        path = self._workflow_path(workflow_name)
        with self._lock:
            write_json_atomic(path, document.to_dict())
        return path.name

    def delete_workflow(self, workflow_name: str) -> bool:
        path = self._workflow_path(workflow_name)
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            return True

    def load_workflow_draft(self) -> WorkflowDocument | None:
        path = self._workflow_draft_path()
        with self._lock:
            if not path.is_file():
                return None
            try:
                return WorkflowDocument.from_dict(read_json_document(path))
            except WorkflowDocumentError as exc:
                raise JsonDocumentSchemaError(
                    f"{path.name} is not a valid workflow draft"
                ) from exc

    def save_workflow_draft(self, document: WorkflowDocument) -> None:
        with self._lock:
            write_json_atomic(
                self._workflow_draft_path(),
                document.to_dict(),
            )

    def delete_workflow_draft(self) -> bool:
        path = self._workflow_draft_path()
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            return True

    def load_task(
        self,
        task_name: str,
    ) -> list[SequenceEntry] | None:
        path = self._task_path(task_name)
        with self._lock:
            if not path.is_file():
                return None
            document = load_collection_document(path, TASK_DOCUMENT)
            return self._parse_task_entries(path, document.collection)

    def migrate_legacy_tasks(self) -> tuple[str, ...]:
        """Explicitly migrate every legacy task after validating the full file."""
        migrated: list[str] = []
        with self._lock:
            for task_name in self.list_task_names():
                path = self._task_path(task_name)
                document = load_collection_document(path, TASK_DOCUMENT)
                entries = self._parse_task_entries(path, document.collection)
                if not document.requires_migration:
                    continue
                migrate_collection_document(
                    path,
                    TASK_DOCUMENT,
                    [entry.to_dict() for entry in entries],
                )
                migrated.append(path.name)
        return tuple(migrated)

    def save_task(
        self,
        task_name: str,
        entries: Sequence[SequenceEntry],
    ) -> str:
        path = self._task_path(task_name)
        payload = [entry.to_dict() for entry in entries]
        with self._lock:
            write_collection_document(path, TASK_DOCUMENT, payload)
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
            if new_path.is_file() and old_path.resolve() != new_path.resolve():
                raise FileExistsError(new_path.name)
            old_name = old_path.name
            old_path.replace(new_path)
            return old_name, new_path.name

    def _task_path(self, task_name: str) -> Path:
        requested_name = self._plain_name(task_name, "task")
        path = self._tasks_directory / requested_name
        if path.suffix != ".task":
            path = path.with_suffix(".task")
        return path

    def _workflow_path(self, workflow_name: str) -> Path:
        requested_name = self._plain_name(workflow_name, "workflow")
        path = self._tasks_directory / requested_name
        if path.suffix != WORKFLOW_FILE_SUFFIX:
            path = path.with_suffix(WORKFLOW_FILE_SUFFIX)
        return path

    def _workflow_draft_path(self) -> Path:
        return self._tasks_directory / WORKFLOW_DRAFT_FILE_NAME

    def _parse_actions(
        self,
        actions_file: Path,
        raw_actions: Sequence[object],
    ) -> list[ActionDefinition]:
        actions: list[ActionDefinition] = []
        action_ids: set[str] = set()
        action_names: set[str] = set()
        for index, raw_action in enumerate(raw_actions):
            if not isinstance(raw_action, dict):
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} action at index {index} must be a JSON object"
                )
            if not raw_action.get("id"):
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} action at index {index} "
                    "must declare a stable id"
                )
            try:
                action = ActionDefinition.from_dict(raw_action)
            except (KeyError, TypeError, ValueError) as exc:
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} action at index {index} is invalid"
                ) from exc
            if action.id in action_ids:
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} contains duplicate action id {action.id!r}"
                )
            if action.name in action_names:
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} contains duplicate action name {action.name!r}"
                )
            validation = validate_action_parameters(
                action.type,
                action.parameters,
            )
            if not validation.is_valid:
                raise JsonDocumentSchemaError(
                    f"{actions_file.name} action {action.name!r} has invalid parameters: "
                    f"{validation.message}"
                )
            action_ids.add(action.id)
            action_names.add(action.name)
            actions.append(action)
        return actions

    def _actions_path(self) -> Path:
        return self._actions_directory / ACTION_LIBRARY_FILE_NAME

    @staticmethod
    def _parse_task_entries(
        path: Path,
        raw_entries: Sequence[object],
    ) -> list[SequenceEntry]:
        entries: list[SequenceEntry] = []
        for index, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                raise JsonDocumentSchemaError(
                    f"{path.name} entry at index {index} must be a JSON object"
                )
            try:
                entry = (
                    LoopBlock.from_dict(raw_entry)
                    if raw_entry.get("kind") == "loop"
                    else SequenceItem.from_dict(raw_entry)
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise JsonDocumentSchemaError(
                    f"{path.name} entry at index {index} is invalid"
                ) from exc
            entries.append(entry)
        return entries

    @staticmethod
    def _plain_name(value: str, label: str) -> str:
        requested_name = str(value).strip()
        if (
            not requested_name
            or requested_name in {".", ".."}
            or Path(requested_name).name != requested_name
            or "/" in requested_name
            or "\\" in requested_name
        ):
            raise ValueError(
                f"{label} name must be a non-empty plain file name"
            )
        return requested_name

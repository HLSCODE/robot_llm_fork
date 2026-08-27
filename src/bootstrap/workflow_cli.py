"""Explicit one-way migration from legacy task/workflow files to workflow v5."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from ..domain.models import (
    ActionType,
    LoopBlock,
    ParallelBlock,
    SequenceEntry,
    SequenceItem,
    SubworkflowBlock,
    sequence_entry_from_dict,
)
from ..domain.robot_profile import UNSCOPED_ROBOT_PROFILE, normalize_robot_profile_id
from ..domain.workflow import CanvasPosition, WorkflowDocument
from ..geometry.pose_compensation import parse_pose
from ..persistence.json_documents import read_json_document, write_json_atomic
from ..persistence.storage import WORKFLOW_FILE_SUFFIX


@dataclass(frozen=True, slots=True)
class MigrationItem:
    source: Path
    target: Path
    document: WorkflowDocument


@dataclass(frozen=True, slots=True)
class WorkflowMigrationResult:
    """Result of one idempotent legacy-workflow migration pass."""

    migrated_files: tuple[Path, ...]
    backup_directory: Path

    @property
    def migrated_count(self) -> int:
        return len(self.migrated_files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--robot-profile",
        default=UNSCOPED_ROBOT_PROFILE,
        help="Robot Profile written to migrated workflow documents.",
    )
    parser.add_argument(
        "--normalize-active",
        action="store_true",
        help="Rewrite textual poses in active v5 workflows as numeric arrays.",
    )
    args = parser.parse_args(argv)
    root = args.data_root.resolve()
    source_directory = root / "tasks"
    workflows_directory = root / "workflows"
    drafts_directory = root / "drafts"
    if args.normalize_active:
        changed = normalize_active_workflows(
            workflows_directory,
            root / "migration-backups" / "workflow-integrity-v1",
        )
        print(f"normalized {changed} active workflow documents")
        return 0

    robot_profile_id = normalize_robot_profile_id(args.robot_profile)
    items = _plan(
        source_directory,
        workflows_directory,
        drafts_directory,
        robot_profile_id=robot_profile_id,
    )
    print(f"legacy documents: {len(items)}")
    for item in items:
        print(f"  {item.source.name} -> {item.target.relative_to(root)}")
    if not args.apply:
        print("dry run; pass --apply to migrate")
        return 0
    result = migrate_legacy_workflows(
        root,
        workflows_directory=workflows_directory,
        drafts_directory=drafts_directory,
        robot_profile_id=robot_profile_id,
    )
    print(
        f"migrated {result.migrated_count} documents; "
        f"originals archived in {result.backup_directory}"
    )
    return 0


def migrate_legacy_workflows(
    data_root: Path,
    *,
    workflows_directory: Path | None = None,
    drafts_directory: Path | None = None,
    robot_profile_id: str = UNSCOPED_ROBOT_PROFILE,
) -> WorkflowMigrationResult:
    """Migrate legacy files below ``data_root/tasks`` exactly once.

    Active workflow and draft directories may be overridden independently by
    configuration. Existing canonical workflows are never overwritten.
    """
    root = data_root.resolve()
    normalized_profile_id = normalize_robot_profile_id(robot_profile_id)
    source_directory = root / "tasks"
    active_workflows_directory = (
        workflows_directory.resolve()
        if workflows_directory is not None
        else root / "workflows"
    )
    active_drafts_directory = (
        drafts_directory.resolve()
        if drafts_directory is not None
        else root / "drafts"
    )
    backup_directory = root / "migration-backups" / "workflow-v5"
    items = _plan(
        source_directory,
        active_workflows_directory,
        active_drafts_directory,
        robot_profile_id=normalized_profile_id,
    )
    if items:
        _apply(
            items,
            source_directory,
            active_workflows_directory,
            backup_directory,
        )
    return WorkflowMigrationResult(
        migrated_files=tuple(item.target for item in items),
        backup_directory=backup_directory,
    )


def _plan(
    source_directory: Path,
    workflows_directory: Path,
    drafts_directory: Path,
    *,
    robot_profile_id: str = UNSCOPED_ROBOT_PROFILE,
) -> tuple[MigrationItem, ...]:
    if not source_directory.is_dir():
        return ()
    items: list[MigrationItem] = []
    targets: set[Path] = set()
    for source in sorted(source_directory.iterdir()):
        if not source.is_file():
            continue
        if source.name.endswith(".task"):
            name = source.name.removesuffix(".task")
            document = _task_document(source, name, robot_profile_id)
            target = workflows_directory / f"{name}{WORKFLOW_FILE_SUFFIX}"
        elif source.name.endswith(".workflow"):
            name = source.name.removesuffix(".workflow")
            document = _workflow_v1_document(source, robot_profile_id)
            target = workflows_directory / f"{name}{WORKFLOW_FILE_SUFFIX}"
        elif source.name == ".workflow-draft":
            document = _workflow_v1_document(source, robot_profile_id)
            target = drafts_directory / "current.draft.workflow.json"
        else:
            continue
        if target in targets:
            raise ValueError(f"multiple legacy documents map to {target.name}")
        if target.exists():
            raise FileExistsError(target)
        targets.add(target)
        items.append(MigrationItem(source, target, document))
    return tuple(items)


def _task_document(
    path: Path,
    name: str,
    robot_profile_id: str,
) -> WorkflowDocument:
    raw = read_json_document(path)
    raw_entries: object
    if isinstance(raw, list):
        raw_entries = raw
    elif isinstance(raw, Mapping):
        raw_entries = raw.get("entries")
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        raise ValueError(f"{path.name}: task entries must be an array")
    entries = _entries(path, raw_entries, robot_profile_id)
    return WorkflowDocument.from_entries(
        workflow_id=name,
        name=name,
        revision=1,
        entries=entries,
        robot_profile_id=robot_profile_id,
    )


def _workflow_v1_document(
    path: Path,
    robot_profile_id: str,
) -> WorkflowDocument:
    raw = read_json_document(path)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path.name}: workflow must be an object")
    if raw.get("schema") != "robot_llm.workflow" or raw.get("schema_version") != 1:
        raise ValueError(f"{path.name}: expected robot_llm.workflow schema v1")
    raw_nodes = raw.get("nodes")
    raw_order = raw.get("order")
    if not isinstance(raw_nodes, list) or not isinstance(raw_order, list):
        raise ValueError(f"{path.name}: nodes and order must be arrays")
    nodes: dict[str, tuple[SequenceEntry, CanvasPosition]] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise ValueError(f"{path.name}: node must be an object")
        node_id = str(raw_node.get("node_id", "")).strip()
        raw_entry = raw_node.get("entry")
        raw_position = raw_node.get("position")
        if not node_id or not isinstance(raw_entry, Mapping):
            raise ValueError(f"{path.name}: invalid node")
        if node_id in nodes:
            raise ValueError(f"{path.name}: duplicate node id {node_id}")
        entry = _entry(path, raw_entry)
        position = (
            CanvasPosition.from_dict(raw_position)
            if isinstance(raw_position, Mapping)
            else CanvasPosition(0.0, 0.0)
        )
        nodes[node_id] = (entry, position)
    order = [str(value) for value in raw_order]
    if len(order) != len(nodes) or set(order) != set(nodes):
        raise ValueError(f"{path.name}: order must reference every node exactly once")
    entries = _normalize_entries(
        [nodes[node_id][0] for node_id in order],
        workflow_id=str(raw.get("workflow_id", "")).strip() or path.stem,
        robot_profile_id=robot_profile_id,
    )
    positions = {entry.uuid: nodes[node_id][1] for node_id, entry in zip(order, entries)}
    return WorkflowDocument.from_entries(
        workflow_id=str(raw.get("workflow_id", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        revision=int(raw.get("revision", 0)),
        entries=entries,
        positions=positions,
        robot_profile_id=robot_profile_id,
    )


def _entries(
    path: Path,
    raw_entries: list[object],
    robot_profile_id: str,
) -> list[SequenceEntry]:
    return _normalize_entries(
        [_entry(path, raw_entry) for raw_entry in raw_entries],
        workflow_id=path.stem,
        robot_profile_id=robot_profile_id,
    )


def _entry(path: Path, raw_entry: object) -> SequenceEntry:
    if not isinstance(raw_entry, Mapping):
        raise ValueError(f"{path.name}: entry must be an object")
    try:
        entry_data = dict(raw_entry)
        return _normalize_entry(sequence_entry_from_dict(entry_data))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path.name}: invalid task entry") from exc


def normalize_active_workflows(
    workflows_directory: Path,
    backup_directory: Path | None = None,
) -> int:
    """Rewrite valid v5 workflows to the canonical pose representation."""
    if not workflows_directory.is_dir():
        return 0

    changes: list[tuple[Path, WorkflowDocument]] = []
    for path in sorted(workflows_directory.glob(f"*{WORKFLOW_FILE_SUFFIX}")):
        document = WorkflowDocument.from_dict(read_json_document(path))
        entries = tuple(
            _normalize_entries(
                list(document.to_entries()),
                workflow_id=document.workflow_id,
                robot_profile_id=document.robot_profile_id,
            )
        )
        normalized = WorkflowDocument.from_entries(
            workflow_id=document.workflow_id,
            name=document.name,
            revision=document.revision,
            entries=entries,
            positions=document.position_map(),
            robot_profile_id=document.robot_profile_id,
        )
        if normalized.to_dict() != document.to_dict():
            changes.append((path, normalized))

    if backup_directory is not None and changes:
        collisions = [
            backup_directory / path.name
            for path, _document in changes
            if (backup_directory / path.name).exists()
        ]
        if collisions:
            raise FileExistsError(collisions[0])
        backup_directory.mkdir(parents=True, exist_ok=True)
        for path, _document in changes:
            shutil.copy2(path, backup_directory / path.name)

    for path, normalized in changes:
        write_json_atomic(path, normalized.to_dict())
    return len(changes)


def _normalize_entry(entry: SequenceEntry) -> SequenceEntry:
    if isinstance(entry, SequenceItem):
        if entry.definition.type is ActionType.MOVE and "点位" in entry.definition.parameters:
            entry.definition.parameters["点位"] = _parse_legacy_pose(
                entry.definition.parameters["点位"]
            )
        return entry
    if isinstance(entry, LoopBlock | SubworkflowBlock):
        entry.items = [_normalize_entry(item) for item in entry.items]
        return entry
    if isinstance(entry, ParallelBlock):
        for branch in entry.branches:
            branch.items = [_normalize_entry(item) for item in branch.items]
        return entry
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _parse_legacy_pose(value: object) -> list[float]:
    if isinstance(value, str):
        # Some legacy files captured the terminal bracketed-paste marker as text.
        value = value.strip().removeprefix("[200~")
    return parse_pose(value)


def _normalize_entries(
    entries: list[SequenceEntry],
    *,
    workflow_id: str,
    robot_profile_id: str,
) -> list[SequenceEntry]:
    normalized = [_normalize_entry(entry) for entry in entries]
    used_uuids: set[str] = set()

    def normalize_identity(entry: SequenceEntry, path: tuple[int, ...]) -> None:
        entry_uuid = entry.uuid.strip()
        if not entry_uuid or entry_uuid in used_uuids:
            entry_uuid = str(
                uuid5(
                    NAMESPACE_URL,
                    f"robot-llm/workflow/{workflow_id}/entry/{path}",
                )
            )
            entry.uuid = entry_uuid
        used_uuids.add(entry_uuid)

        if isinstance(entry, SequenceItem):
            entry.definition.robot_profile_id = robot_profile_id
            if not entry.definition.id.strip():
                entry.definition.id = f"legacy-{entry_uuid}"
            if not entry.definition.name.strip():
                entry.definition.name = f"未命名动作-{entry_uuid[:8]}"
            return
        if isinstance(entry, LoopBlock | SubworkflowBlock):
            for index, child in enumerate(entry.items):
                normalize_identity(child, (*path, index))
            return
        if isinstance(entry, ParallelBlock):
            for branch_index, branch in enumerate(entry.branches):
                for item_index, child in enumerate(branch.items):
                    normalize_identity(child, (*path, branch_index, item_index))
            return
        raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")

    for index, entry in enumerate(normalized):
        normalize_identity(entry, (index,))
    return normalized


def _apply(
    items: tuple[MigrationItem, ...],
    source_directory: Path,
    workflows_directory: Path,
    backup_directory: Path,
) -> None:
    workflows_directory.parent.mkdir(parents=True, exist_ok=True)
    sources = tuple(
        source
        for source in sorted(source_directory.iterdir())
        if source.is_file()
    )
    collisions = [
        backup_directory / source.name
        for source in sources
        if (backup_directory / source.name).exists()
    ]
    if collisions:
        raise FileExistsError(collisions[0])

    with TemporaryDirectory(prefix="workflow-v5-", dir=workflows_directory.parent) as raw_stage:
        stage = Path(raw_stage)
        staged: list[tuple[Path, MigrationItem]] = []
        for index, item in enumerate(items):
            staged_path = stage / f"{index}.json"
            write_json_atomic(staged_path, item.document.to_dict())
            restored = WorkflowDocument.from_dict(read_json_document(staged_path))
            if restored != item.document:
                raise ValueError(f"staged workflow verification failed: {item.source.name}")
            staged.append((staged_path, item))
        for staged_path, item in staged:
            item.target.parent.mkdir(parents=True, exist_ok=True)
            staged_path.replace(item.target)

    backup_directory.mkdir(parents=True, exist_ok=True)
    for source in sources:
        destination = backup_directory / source.name
        shutil.move(str(source), destination)


if __name__ == "__main__":
    raise SystemExit(main())

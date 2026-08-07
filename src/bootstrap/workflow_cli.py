"""Explicit one-way migration from legacy task/workflow files to workflow v3."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Mapping, Sequence

from ..domain.models import SequenceEntry, sequence_entry_from_dict
from ..domain.workflow import CanvasPosition, WorkflowDocument
from ..persistence.json_documents import read_json_document, write_json_atomic
from ..persistence.storage import WORKFLOW_FILE_SUFFIX


@dataclass(frozen=True, slots=True)
class MigrationItem:
    source: Path
    target: Path
    document: WorkflowDocument


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = args.data_root.resolve()
    source_directory = root / "tasks"
    workflows_directory = root / "workflows"
    drafts_directory = root / "drafts"
    backup_directory = root / "migration-backups" / "workflow-v1"
    items = _plan(source_directory, workflows_directory, drafts_directory)
    print(f"legacy documents: {len(items)}")
    for item in items:
        print(f"  {item.source.name} -> {item.target.relative_to(root)}")
    if not args.apply:
        print("dry run; pass --apply to migrate")
        return 0
    _apply(items, source_directory, workflows_directory, backup_directory)
    print(f"migrated {len(items)} documents; originals archived in {backup_directory}")
    return 0


def _plan(
    source_directory: Path,
    workflows_directory: Path,
    drafts_directory: Path,
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
            document = _task_document(source, name)
            target = workflows_directory / f"{name}{WORKFLOW_FILE_SUFFIX}"
        elif source.name.endswith(".workflow"):
            name = source.name.removesuffix(".workflow")
            document = _workflow_v1_document(source)
            target = workflows_directory / f"{name}{WORKFLOW_FILE_SUFFIX}"
        elif source.name == ".workflow-draft":
            document = _workflow_v1_document(source)
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


def _task_document(path: Path, name: str) -> WorkflowDocument:
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
    entries = _entries(path, raw_entries)
    return WorkflowDocument.from_entries(
        workflow_id=name,
        name=name,
        revision=1,
        entries=entries,
    )


def _workflow_v1_document(path: Path) -> WorkflowDocument:
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
    entries = [nodes[node_id][0] for node_id in order]
    positions = {entry.uuid: nodes[node_id][1] for node_id, entry in zip(order, entries)}
    return WorkflowDocument.from_entries(
        workflow_id=str(raw.get("workflow_id", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        revision=int(raw.get("revision", 0)),
        entries=entries,
        positions=positions,
    )


def _entries(path: Path, raw_entries: list[object]) -> list[SequenceEntry]:
    return [_entry(path, raw_entry) for raw_entry in raw_entries]


def _entry(path: Path, raw_entry: object) -> SequenceEntry:
    if not isinstance(raw_entry, Mapping):
        raise ValueError(f"{path.name}: entry must be an object")
    try:
        entry_data = dict(raw_entry)
        return sequence_entry_from_dict(entry_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path.name}: invalid task entry") from exc


def _apply(
    items: tuple[MigrationItem, ...],
    source_directory: Path,
    workflows_directory: Path,
    backup_directory: Path,
) -> None:
    with TemporaryDirectory(prefix="workflow-v3-", dir=workflows_directory.parent) as raw_stage:
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
    for source in sorted(source_directory.iterdir()):
        if not source.is_file():
            continue
        destination = backup_directory / source.name
        if destination.exists():
            raise FileExistsError(destination)
        shutil.move(str(source), destination)


if __name__ == "__main__":
    raise SystemExit(main())

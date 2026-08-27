"""Versioned, runtime-independent workflow document model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from collections.abc import Callable
from typing import Any, Mapping, Sequence, TypeAlias
from uuid import uuid4

from .models import (
    ActionDefinition,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    ParallelFailurePolicy,
    ParallelJoinPolicy,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)


WORKFLOW_DOCUMENT_SCHEMA = "robot_llm.workflow"
WORKFLOW_DOCUMENT_VERSION = 5
WORKFLOW_SCHEMA_REFERENCE = "../../../schemas/workflow.schema.json"


class WorkflowDocumentError(ValueError):
    """Base error for malformed workflow documents."""


class UnsupportedWorkflowDocumentVersion(WorkflowDocumentError):
    """Raised when a document uses an unsupported schema version."""


@dataclass(frozen=True, slots=True)
class CanvasPosition:
    """Optional device-independent presentation coordinates."""

    x: float
    y: float

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"canvas {name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"canvas {name} must be finite")

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CanvasPosition:
        if not isinstance(data, Mapping):
            raise WorkflowDocumentError("canvas position must be an object")
        try:
            return cls(x=data["x"], y=data["y"])
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"canvas position is missing {exc.args[0]!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkflowActionNode:
    """A stable workflow node containing an action snapshot."""

    node_id: str
    item_uuid: str
    definition: ActionDefinition

    def __post_init__(self) -> None:
        _require_text(self.node_id, "action node_id")
        _require_text(self.item_uuid, "action item_uuid")
        if not isinstance(self.definition, ActionDefinition):
            raise TypeError("action definition must be an ActionDefinition")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "action",
            "node_id": self.node_id,
            "item_uuid": self.item_uuid,
            "definition": deepcopy(self.definition.to_dict()),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowActionNode:
        try:
            raw_definition = data["definition"]
            if not isinstance(raw_definition, Mapping):
                raise WorkflowDocumentError("action definition must be an object")
            return cls(
                node_id=data["node_id"],
                item_uuid=data["item_uuid"],
                definition=ActionDefinition.from_dict(dict(raw_definition)),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"action node is missing {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowDocumentError):
                raise
            raise WorkflowDocumentError("action node is invalid") from exc


@dataclass(frozen=True, slots=True)
class WorkflowLoopNode:
    """A structured loop whose body is an ordered action sequence."""

    node_id: str
    loop_uuid: str
    repeat_count: int
    body: WorkflowSequence

    def __post_init__(self) -> None:
        _require_text(self.node_id, "loop node_id")
        _require_text(self.loop_uuid, "loop_uuid")
        if isinstance(self.repeat_count, bool) or not isinstance(self.repeat_count, int):
            raise TypeError("loop repeat_count must be an integer")
        if not isinstance(self.body, WorkflowSequence):
            raise TypeError("loop body must be a workflow sequence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "loop",
            "node_id": self.node_id,
            "loop_uuid": self.loop_uuid,
            "repeat_count": self.repeat_count,
            "body": self.body.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowLoopNode:
        try:
            raw_body = data["body"]
            if not isinstance(raw_body, Mapping):
                raise WorkflowDocumentError("loop body must be an object")
            if raw_body.get("kind") != "sequence":
                raise WorkflowDocumentError("loop body kind must be 'sequence'")
            raw_children = raw_body.get("children")
            if not isinstance(raw_children, list):
                raise WorkflowDocumentError("loop body children must be an array")
            return cls(
                node_id=data["node_id"],
                loop_uuid=data["loop_uuid"],
                repeat_count=data["repeat_count"],
                body=WorkflowSequence.from_dict(raw_body),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"loop node is missing {exc.args[0]!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkflowParallelBranch:
    branch_id: str
    body: WorkflowSequence

    def __post_init__(self) -> None:
        _require_text(self.branch_id, "parallel branch_id")
        if not isinstance(self.body, WorkflowSequence):
            raise TypeError("parallel branch body must be a workflow sequence")

    def to_dict(self) -> dict[str, Any]:
        return {"branch_id": self.branch_id, "body": self.body.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowParallelBranch:
        try:
            raw_body = data["body"]
            if not isinstance(raw_body, Mapping):
                raise WorkflowDocumentError("parallel branch body must be an object")
            return cls(
                branch_id=data["branch_id"],
                body=WorkflowSequence.from_dict(raw_body),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"parallel branch is missing {exc.args[0]!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkflowParallelNode:
    node_id: str
    parallel_uuid: str
    branches: tuple[WorkflowParallelBranch, ...]
    join_policy: ParallelJoinPolicy = ParallelJoinPolicy.ALL
    failure_policy: ParallelFailurePolicy = ParallelFailurePolicy.CANCEL_ALL

    def __post_init__(self) -> None:
        _require_text(self.node_id, "parallel node_id")
        _require_text(self.parallel_uuid, "parallel_uuid")
        if not isinstance(self.branches, tuple) or not all(
            isinstance(branch, WorkflowParallelBranch) for branch in self.branches
        ):
            raise TypeError("parallel branches must be a tuple")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "parallel",
            "node_id": self.node_id,
            "parallel_uuid": self.parallel_uuid,
            "join_policy": self.join_policy.value,
            "failure_policy": self.failure_policy.value,
            "branches": [branch.to_dict() for branch in self.branches],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowParallelNode:
        try:
            raw_branches = data["branches"]
            if not isinstance(raw_branches, list):
                raise WorkflowDocumentError("parallel branches must be an array")
            return cls(
                node_id=data["node_id"],
                parallel_uuid=data["parallel_uuid"],
                branches=tuple(
                    WorkflowParallelBranch.from_dict(branch)
                    for branch in raw_branches
                ),
                join_policy=ParallelJoinPolicy(data["join_policy"]),
                failure_policy=ParallelFailurePolicy(data["failure_policy"]),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"parallel node is missing {exc.args[0]!r}"
            ) from exc
        except ValueError as exc:
            raise WorkflowDocumentError("parallel policy is invalid") from exc


@dataclass(frozen=True, slots=True)
class WorkflowSubworkflowNode:
    """Named self-contained workflow body embedded as an editable snapshot."""

    node_id: str
    subworkflow_uuid: str
    name: str
    body: WorkflowSequence
    source_workflow_id: str = ""
    source_revision: int = 0

    def __post_init__(self) -> None:
        _require_text(self.node_id, "subworkflow node_id")
        _require_text(self.subworkflow_uuid, "subworkflow_uuid")
        _require_text(self.name, "subworkflow name")
        if not isinstance(self.body, WorkflowSequence):
            raise TypeError("subworkflow body must be a WorkflowSequence")
        if not isinstance(self.source_workflow_id, str):
            raise TypeError("source_workflow_id must be a string")
        if (
            isinstance(self.source_revision, bool)
            or not isinstance(self.source_revision, int)
            or self.source_revision < 0
        ):
            raise ValueError("source_revision must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "subworkflow",
            "node_id": self.node_id,
            "subworkflow_uuid": self.subworkflow_uuid,
            "name": self.name,
            "source_workflow_id": self.source_workflow_id,
            "source_revision": self.source_revision,
            "body": self.body.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowSubworkflowNode:
        try:
            raw_body = data["body"]
            if not isinstance(raw_body, Mapping):
                raise WorkflowDocumentError("subworkflow body must be an object")
            return cls(
                node_id=data["node_id"],
                subworkflow_uuid=data["subworkflow_uuid"],
                name=data["name"],
                source_workflow_id=data.get("source_workflow_id", ""),
                source_revision=data.get("source_revision", 0),
                body=WorkflowSequence.from_dict(raw_body),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"subworkflow node is missing {exc.args[0]!r}"
            ) from exc


WorkflowNode: TypeAlias = (
    WorkflowActionNode
    | WorkflowLoopNode
    | WorkflowParallelNode
    | WorkflowSubworkflowNode
)


@dataclass(frozen=True, slots=True)
class WorkflowSequence:
    """Ordered root control-flow container."""

    children: tuple[WorkflowNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple) or not all(
            isinstance(
                node,
                (
                    WorkflowActionNode,
                    WorkflowLoopNode,
                    WorkflowParallelNode,
                    WorkflowSubworkflowNode,
                ),
            )
            for node in self.children
        ):
            raise TypeError("workflow children must be workflow nodes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sequence",
            "children": [node.to_dict() for node in self.children],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowSequence:
        if not isinstance(data, Mapping) or data.get("kind") != "sequence":
            raise WorkflowDocumentError("workflow root kind must be 'sequence'")
        raw_children = data.get("children")
        if not isinstance(raw_children, list):
            raise WorkflowDocumentError("workflow root children must be an array")
        return cls(tuple(_parse_node(child) for child in raw_children))


@dataclass(frozen=True, slots=True)
class WorkflowDocument:
    """Canonical persisted workflow; execution state is intentionally excluded."""

    workflow_id: str
    name: str
    revision: int
    robot_profile_id: str
    root: WorkflowSequence
    positions: tuple[tuple[str, CanvasPosition], ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.workflow_id, "workflow_id")
        _require_text(self.name, "workflow name")
        _require_text(self.robot_profile_id, "robot_profile_id")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("workflow revision must be an integer")
        if self.revision < 0:
            raise WorkflowDocumentError("workflow revision must not be negative")
        if not isinstance(self.root, WorkflowSequence):
            raise TypeError("workflow root must be a WorkflowSequence")
        if not isinstance(self.positions, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], CanvasPosition)
            for item in self.positions
        ):
            raise TypeError("workflow positions must contain node-position pairs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "$schema": WORKFLOW_SCHEMA_REFERENCE,
            "schema": WORKFLOW_DOCUMENT_SCHEMA,
            "schema_version": WORKFLOW_DOCUMENT_VERSION,
            "workflow_id": self.workflow_id,
            "name": self.name,
            "revision": self.revision,
            "robot_profile_id": self.robot_profile_id,
            "root": self.root.to_dict(),
            "presentation": {
                "positions": {
                    node_id: position.to_dict()
                    for node_id, position in self.positions
                }
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowDocument:
        if not isinstance(data, Mapping):
            raise WorkflowDocumentError("workflow document must be an object")
        if data.get("schema") != WORKFLOW_DOCUMENT_SCHEMA:
            raise WorkflowDocumentError(
                f"workflow schema must be {WORKFLOW_DOCUMENT_SCHEMA!r}"
            )
        version = data.get("schema_version")
        if version != WORKFLOW_DOCUMENT_VERSION:
            raise UnsupportedWorkflowDocumentVersion(
                f"workflow schema version {version!r} is unsupported; "
                f"expected {WORKFLOW_DOCUMENT_VERSION}"
            )
        try:
            raw_root = data["root"]
            raw_presentation = data.get("presentation", {})
            if not isinstance(raw_root, Mapping):
                raise WorkflowDocumentError("workflow root must be an object")
            if not isinstance(raw_presentation, Mapping):
                raise WorkflowDocumentError("workflow presentation must be an object")
            raw_positions = raw_presentation.get("positions", {})
            if not isinstance(raw_positions, Mapping):
                raise WorkflowDocumentError("presentation positions must be an object")
            return cls(
                workflow_id=data["workflow_id"],
                name=data["name"],
                revision=data["revision"],
                robot_profile_id=data["robot_profile_id"],
                root=WorkflowSequence.from_dict(raw_root),
                positions=tuple(
                    (node_id, CanvasPosition.from_dict(position))
                    for node_id, position in raw_positions.items()
                ),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"workflow document is missing {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowDocumentError):
                raise
            raise WorkflowDocumentError("workflow document is invalid") from exc

    @classmethod
    def from_entries(
        cls,
        *,
        workflow_id: str,
        name: str,
        revision: int,
        entries: Sequence[SequenceEntry],
        robot_profile_id: str = "unscoped",
        positions: Mapping[str, CanvasPosition] | None = None,
    ) -> WorkflowDocument:
        nodes: list[WorkflowNode] = []
        for entry in entries:
            if isinstance(entry, SequenceItem):
                nodes.append(_action_node(entry, node_id=entry.uuid))
            elif isinstance(entry, LoopBlock):
                nodes.append(
                    WorkflowLoopNode(
                        node_id=entry.uuid,
                        loop_uuid=entry.uuid,
                        repeat_count=entry.repeat_count,
                        body=_sequence_from_entries(entry.items),
                    )
                )
            elif isinstance(entry, ParallelBlock):
                nodes.append(
                    WorkflowParallelNode(
                        node_id=entry.uuid,
                        parallel_uuid=entry.uuid,
                        branches=tuple(
                            WorkflowParallelBranch(
                                branch.branch_id,
                                _sequence_from_entries(branch.items),
                            )
                            for branch in entry.branches
                        ),
                        join_policy=entry.join_policy,
                        failure_policy=entry.failure_policy,
                    )
                )
            elif isinstance(entry, SubworkflowBlock):
                nodes.append(
                    WorkflowSubworkflowNode(
                        node_id=entry.uuid,
                        subworkflow_uuid=entry.uuid,
                        name=entry.name,
                        source_workflow_id=entry.source_workflow_id,
                        source_revision=entry.source_revision,
                        body=_sequence_from_entries(entry.items),
                    )
                )
            else:
                raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")
        selected_positions = positions or {}
        return cls(
            workflow_id=workflow_id,
            name=name,
            revision=revision,
            robot_profile_id=robot_profile_id,
            root=WorkflowSequence(tuple(nodes)),
            positions=tuple(
                (node.node_id, selected_positions[node.node_id])
                for node in nodes
                if node.node_id in selected_positions
            ),
        )

    def to_entries(self) -> tuple[SequenceEntry, ...]:
        entries: list[SequenceEntry] = []
        for node in self.root.children:
            if isinstance(node, WorkflowActionNode):
                entries.append(_sequence_item(node))
            elif isinstance(node, WorkflowLoopNode):
                entries.append(
                    LoopBlock(
                        uuid=node.loop_uuid,
                        items=list(_entries_from_sequence(node.body)),
                        repeat_count=node.repeat_count,
                        current_iteration=0,
                    )
                )
            elif isinstance(node, WorkflowParallelNode):
                entries.append(
                    ParallelBlock(
                        uuid=node.parallel_uuid,
                        branches=[
                            ParallelBranch(
                                branch.branch_id,
                                list(_entries_from_sequence(branch.body)),
                            )
                            for branch in node.branches
                        ],
                        join_policy=node.join_policy,
                        failure_policy=node.failure_policy,
                    )
                )
            else:
                entries.append(
                    SubworkflowBlock(
                        uuid=node.subworkflow_uuid,
                        name=node.name,
                        source_workflow_id=node.source_workflow_id,
                        source_revision=node.source_revision,
                        items=list(_entries_from_sequence(node.body)),
                    )
                )
        return tuple(entries)

    def position_map(self) -> dict[str, CanvasPosition]:
        return dict(self.positions)


def clone_sequence_entry(entry: SequenceEntry) -> SequenceEntry:
    """Return a defensive pending-state copy for application boundaries."""
    if isinstance(entry, LoopBlock):
        return LoopBlock(
            uuid=entry.uuid,
            items=[clone_sequence_entry(item) for item in entry.items],
            repeat_count=entry.repeat_count,
            current_iteration=0,
        )
    if isinstance(entry, ParallelBlock):
        return ParallelBlock(
            uuid=entry.uuid,
            branches=[
                ParallelBranch(
                    branch.branch_id,
                    [clone_sequence_entry(item) for item in branch.items],
                )
                for branch in entry.branches
            ],
            join_policy=entry.join_policy,
            failure_policy=entry.failure_policy,
        )
    if isinstance(entry, SubworkflowBlock):
        return SubworkflowBlock(
            uuid=entry.uuid,
            name=entry.name,
            source_workflow_id=entry.source_workflow_id,
            source_revision=entry.source_revision,
            items=[clone_sequence_entry(item) for item in entry.items],
        )
    if isinstance(entry, SequenceItem):
        return SequenceItem(
            uuid=entry.uuid,
            definition=ActionDefinition.from_dict(deepcopy(entry.definition.to_dict())),
            status=SequenceItemStatus.PENDING,
        )
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def instantiate_subworkflow(
    document: WorkflowDocument,
    *,
    id_factory: Callable[[], str] | None = None,
) -> SubworkflowBlock:
    """Create an isolated editable snapshot with entirely fresh runtime identities."""
    create_id = id_factory or (lambda: str(uuid4()))
    return SubworkflowBlock(
        uuid=create_id(),
        name=document.name,
        source_workflow_id=document.workflow_id,
        source_revision=document.revision,
        items=[
            _clone_entry_with_new_ids(entry, create_id)
            for entry in document.to_entries()
        ],
    )


def _clone_entry_with_new_ids(
    entry: SequenceEntry,
    create_id: Callable[[], str],
) -> SequenceEntry:
    if isinstance(entry, SequenceItem):
        return SequenceItem(
            uuid=create_id(),
            definition=ActionDefinition.from_dict(
                deepcopy(entry.definition.to_dict())
            ),
            status=SequenceItemStatus.PENDING,
        )
    if isinstance(entry, LoopBlock):
        return LoopBlock(
            uuid=create_id(),
            items=[_clone_entry_with_new_ids(item, create_id) for item in entry.items],
            repeat_count=entry.repeat_count,
            current_iteration=0,
        )
    if isinstance(entry, ParallelBlock):
        return ParallelBlock(
            uuid=create_id(),
            branches=[
                ParallelBranch(
                    create_id(),
                    [
                        _clone_entry_with_new_ids(item, create_id)
                        for item in branch.items
                    ],
                )
                for branch in entry.branches
            ],
            join_policy=entry.join_policy,
            failure_policy=entry.failure_policy,
        )
    if isinstance(entry, SubworkflowBlock):
        return SubworkflowBlock(
            uuid=create_id(),
            name=entry.name,
            source_workflow_id=entry.source_workflow_id,
            source_revision=entry.source_revision,
            items=[_clone_entry_with_new_ids(item, create_id) for item in entry.items],
        )
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _parse_node(data: object) -> WorkflowNode:
    if not isinstance(data, Mapping):
        raise WorkflowDocumentError("workflow child must be an object")
    kind = data.get("kind")
    if kind == "action":
        return WorkflowActionNode.from_dict(data)
    if kind == "loop":
        return WorkflowLoopNode.from_dict(data)
    if kind == "parallel":
        return WorkflowParallelNode.from_dict(data)
    if kind == "subworkflow":
        return WorkflowSubworkflowNode.from_dict(data)
    raise WorkflowDocumentError(f"unsupported workflow node kind {kind!r}")


def _action_node(item: SequenceItem, *, node_id: str) -> WorkflowActionNode:
    return WorkflowActionNode(
        node_id=node_id,
        item_uuid=item.uuid,
        definition=ActionDefinition.from_dict(deepcopy(item.definition.to_dict())),
    )


def _sequence_item(node: WorkflowActionNode) -> SequenceItem:
    return SequenceItem(
        uuid=node.item_uuid,
        definition=ActionDefinition.from_dict(deepcopy(node.definition.to_dict())),
        status=SequenceItemStatus.PENDING,
    )


def _clone_item(item: SequenceItem) -> SequenceItem:
    return SequenceItem(
        uuid=item.uuid,
        definition=ActionDefinition.from_dict(deepcopy(item.definition.to_dict())),
        status=SequenceItemStatus.PENDING,
    )


def _sequence_from_entries(entries: Sequence[SequenceEntry]) -> WorkflowSequence:
    return WorkflowDocument.from_entries(
        workflow_id="nested",
        name="nested",
        revision=0,
        entries=entries,
        robot_profile_id="unscoped",
    ).root


def _entries_from_sequence(sequence: WorkflowSequence) -> tuple[SequenceEntry, ...]:
    document = WorkflowDocument(
        workflow_id="nested",
        name="nested",
        revision=0,
        robot_profile_id="unscoped",
        root=sequence,
    )
    return document.to_entries()


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDocumentError(f"{label} must not be empty")
    return value

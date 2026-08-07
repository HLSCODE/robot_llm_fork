"""Versioned, runtime-independent workflow document model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence, TypeAlias

from .models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)


WORKFLOW_DOCUMENT_SCHEMA = "robot_llm.workflow"
WORKFLOW_DOCUMENT_VERSION = 2
WORKFLOW_SCHEMA_REFERENCE = "../schemas/workflow.schema.json"


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
    body: tuple[WorkflowActionNode, ...]

    def __post_init__(self) -> None:
        _require_text(self.node_id, "loop node_id")
        _require_text(self.loop_uuid, "loop_uuid")
        if isinstance(self.repeat_count, bool) or not isinstance(self.repeat_count, int):
            raise TypeError("loop repeat_count must be an integer")
        if not isinstance(self.body, tuple) or not all(
            isinstance(node, WorkflowActionNode) for node in self.body
        ):
            raise TypeError("loop body must be a tuple of action nodes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "loop",
            "node_id": self.node_id,
            "loop_uuid": self.loop_uuid,
            "repeat_count": self.repeat_count,
            "body": {
                "kind": "sequence",
                "children": [node.to_dict() for node in self.body],
            },
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
            children = tuple(
                _parse_action_node(child, context="loop body")
                for child in raw_children
            )
            return cls(
                node_id=data["node_id"],
                loop_uuid=data["loop_uuid"],
                repeat_count=data["repeat_count"],
                body=children,
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"loop node is missing {exc.args[0]!r}"
            ) from exc


WorkflowNode: TypeAlias = WorkflowActionNode | WorkflowLoopNode


@dataclass(frozen=True, slots=True)
class WorkflowSequence:
    """Ordered root control-flow container."""

    children: tuple[WorkflowNode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.children, tuple) or not all(
            isinstance(node, (WorkflowActionNode, WorkflowLoopNode))
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
    root: WorkflowSequence
    positions: tuple[tuple[str, CanvasPosition], ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.workflow_id, "workflow_id")
        _require_text(self.name, "workflow name")
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
                        body=tuple(
                            _action_node(item, node_id=item.uuid)
                            for item in entry.items
                        ),
                    )
                )
            else:
                raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")
        selected_positions = positions or {}
        return cls(
            workflow_id=workflow_id,
            name=name,
            revision=revision,
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
            else:
                entries.append(
                    LoopBlock(
                        uuid=node.loop_uuid,
                        items=[_sequence_item(child) for child in node.body],
                        repeat_count=node.repeat_count,
                        current_iteration=0,
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
            items=[_clone_item(item) for item in entry.items],
            repeat_count=entry.repeat_count,
            current_iteration=0,
        )
    if isinstance(entry, SequenceItem):
        return SequenceItem(
            uuid=entry.uuid,
            definition=ActionDefinition.from_dict(deepcopy(entry.definition.to_dict())),
            status=SequenceItemStatus.PENDING,
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
    raise WorkflowDocumentError(f"unsupported workflow node kind {kind!r}")


def _parse_action_node(data: object, *, context: str) -> WorkflowActionNode:
    node = _parse_node(data)
    if not isinstance(node, WorkflowActionNode):
        raise WorkflowDocumentError(f"{context} currently supports action nodes only")
    return node


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


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDocumentError(f"{label} must not be empty")
    return value

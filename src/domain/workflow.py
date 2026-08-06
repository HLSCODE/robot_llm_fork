"""Pure workflow editor document and versioned serialization."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .models import (
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)


WORKFLOW_DOCUMENT_SCHEMA = "robot_llm.workflow"
WORKFLOW_DOCUMENT_VERSION = 1


class WorkflowDocumentError(ValueError):
    """Base error for malformed workflow editor documents."""


class UnsupportedWorkflowDocumentVersion(WorkflowDocumentError):
    """Raised when a document was produced by an unsupported schema version."""


@dataclass(frozen=True, slots=True)
class CanvasPosition:
    """Device-independent canvas coordinates."""

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
            raise WorkflowDocumentError("node position must be an object")
        try:
            return cls(x=data["x"], y=data["y"])
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"node position is missing {exc.args[0]!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    """One canonical sequence entry and its presentation metadata."""

    node_id: str
    entry: SequenceEntry
    position: CanvasPosition

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise WorkflowDocumentError("node_id must not be empty")
        if not isinstance(self.entry, (SequenceItem, LoopBlock)):
            raise TypeError("workflow node entry must be a SequenceItem or LoopBlock")

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "entry": _pending_entry(self.entry).to_dict(),
            "position": self.position.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowNode:
        if not isinstance(data, Mapping):
            raise WorkflowDocumentError("workflow node must be an object")
        try:
            raw_entry = data["entry"]
            if not isinstance(raw_entry, Mapping):
                raise WorkflowDocumentError("node entry must be an object")
            entry = (
                LoopBlock.from_dict(dict(raw_entry))
                if raw_entry.get("kind") == "loop"
                else SequenceItem.from_dict(dict(raw_entry))
            )
            return cls(
                node_id=data["node_id"],
                entry=_pending_entry(entry),
                position=CanvasPosition.from_dict(data["position"]),
            )
        except KeyError as exc:
            raise WorkflowDocumentError(
                f"workflow node is missing {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowDocumentError):
                raise
            raise WorkflowDocumentError("workflow node is invalid") from exc


@dataclass(frozen=True, slots=True)
class WorkflowDocument:
    """Qt-independent editable workflow snapshot.

    Start and End nodes are presentation-only and are intentionally absent.
    ``order`` is the sole execution-order source; coordinates never affect it.
    """

    workflow_id: str
    name: str
    revision: int
    nodes: tuple[WorkflowNode, ...]
    order: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise WorkflowDocumentError("workflow_id must not be empty")
        if not isinstance(self.name, str) or not self.name.strip():
            raise WorkflowDocumentError("workflow name must not be empty")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("workflow revision must be an integer")
        if self.revision < 0:
            raise WorkflowDocumentError("workflow revision must not be negative")
        if not isinstance(self.nodes, tuple) or not all(
            isinstance(node, WorkflowNode) for node in self.nodes
        ):
            raise TypeError("workflow nodes must be a tuple of WorkflowNode")
        if not isinstance(self.order, tuple) or not all(
            isinstance(node_id, str) for node_id in self.order
        ):
            raise TypeError("workflow order must be a tuple of node ids")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_DOCUMENT_SCHEMA,
            "schema_version": WORKFLOW_DOCUMENT_VERSION,
            "workflow_id": self.workflow_id,
            "name": self.name,
            "revision": self.revision,
            "nodes": [node.to_dict() for node in self.nodes],
            "order": list(self.order),
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
            raw_nodes = data["nodes"]
            raw_order = data["order"]
            if not isinstance(raw_nodes, list):
                raise WorkflowDocumentError("workflow nodes must be an array")
            if not isinstance(raw_order, list):
                raise WorkflowDocumentError("workflow order must be an array")
            return cls(
                workflow_id=data["workflow_id"],
                name=data["name"],
                revision=data["revision"],
                nodes=tuple(WorkflowNode.from_dict(node) for node in raw_nodes),
                order=tuple(raw_order),
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
        """Create a document while preserving every canonical entry UUID."""
        selected_positions = positions or {}
        nodes = tuple(
            WorkflowNode(
                node_id=entry.uuid,
                entry=_pending_entry(entry),
                position=selected_positions.get(
                    entry.uuid,
                    CanvasPosition(0.0, 0.0),
                ),
            )
            for entry in entries
        )
        return cls(
            workflow_id=workflow_id,
            name=name,
            revision=revision,
            nodes=nodes,
            order=tuple(node.node_id for node in nodes),
        )


def clone_sequence_entry(entry: SequenceEntry) -> SequenceEntry:
    """Return a defensive pending-state copy for application boundaries."""
    return _pending_entry(entry)


def _pending_entry(entry: SequenceEntry) -> SequenceEntry:
    if isinstance(entry, LoopBlock):
        cloned = LoopBlock.from_dict(deepcopy(entry.to_dict()))
        cloned.current_iteration = 0
        for child in cloned.items:
            child.status = SequenceItemStatus.PENDING
        return cloned
    if isinstance(entry, SequenceItem):
        cloned = SequenceItem.from_dict(deepcopy(entry.to_dict()))
        cloned.status = SequenceItemStatus.PENDING
        return cloned
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")

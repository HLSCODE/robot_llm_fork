"""Immutable structured execution plan consumed by the single runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterator, Sequence, TypeAlias

from .models import (
    ActionDefinition,
    LoopBlock,
    ParallelBlock,
    ParallelFailurePolicy,
    ParallelJoinPolicy,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)


@dataclass(frozen=True, slots=True)
class ExecutionAction:
    node_id: str
    item: SequenceItem


@dataclass(frozen=True, slots=True)
class ExecutionSequence:
    sequence_id: str
    children: tuple[ExecutionNode, ...]


@dataclass(frozen=True, slots=True)
class ExecutionLoop:
    node_id: str
    loop_id: str
    repeat_count: int
    body: ExecutionSequence


@dataclass(frozen=True, slots=True)
class ExecutionBranch:
    branch_id: str
    body: ExecutionSequence


@dataclass(frozen=True, slots=True)
class ExecutionParallel:
    node_id: str
    parallel_id: str
    branches: tuple[ExecutionBranch, ...]
    join_policy: ParallelJoinPolicy
    failure_policy: ParallelFailurePolicy


@dataclass(frozen=True, slots=True)
class ExecutionSubworkflow:
    node_id: str
    subworkflow_id: str
    name: str
    body: ExecutionSequence


ExecutionNode: TypeAlias = (
    ExecutionAction | ExecutionLoop | ExecutionParallel | ExecutionSubworkflow
)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    root: ExecutionSequence

    def __post_init__(self) -> None:
        if not isinstance(self.root, ExecutionSequence):
            raise TypeError("execution plan root must be an ExecutionSequence")
        expanded_steps = _validate_sequence(
            self.root,
            node_ids=set(),
            item_ids=set(),
            loop_ids=set(),
            parallel_ids=set(),
            depth=0,
        )
        if expanded_steps > 10_000:
            raise ValueError("execution plan exceeds 10000 expanded steps")

    @classmethod
    def from_entries(cls, entries: Sequence[SequenceEntry]) -> ExecutionPlan:
        return cls(
            ExecutionSequence(
                sequence_id="root",
                children=tuple(
                    _node_from_entry(entry, path=f"root/{index}")
                    for index, entry in enumerate(entries)
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ExecutionStepIdentity:
    runtime_index: int
    step_id: str
    node_id: str
    path: str
    loop_id: str = ""
    loop_iteration: int = 0
    parallel_id: str = ""
    branch_id: str = ""

    def to_event_data(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "node_id": self.node_id,
            "path": self.path,
            "loop_id": self.loop_id,
            "loop_iteration": self.loop_iteration,
            "parallel_id": self.parallel_id,
            "branch_id": self.branch_id,
        }


def iter_execution_steps(
    plan: ExecutionPlan,
) -> Iterator[tuple[ExecutionStepIdentity, SequenceItem]]:
    """Yield deterministic identities in structural order, independent of scheduling."""
    index = 0

    def visit(
        sequence: ExecutionSequence,
        *,
        path: str,
        loop_id: str = "",
        loop_iteration: int = 0,
        parallel_id: str = "",
        branch_id: str = "",
    ) -> Iterator[tuple[ExecutionStepIdentity, SequenceItem]]:
        nonlocal index
        for child_index, node in enumerate(sequence.children):
            node_path = f"{path}/{child_index}"
            if isinstance(node, ExecutionAction):
                identity = ExecutionStepIdentity(
                    runtime_index=index,
                    step_id=node_path,
                    node_id=node.node_id,
                    path=node_path,
                    loop_id=loop_id,
                    loop_iteration=loop_iteration,
                    parallel_id=parallel_id,
                    branch_id=branch_id,
                )
                index += 1
                yield identity, node.item
                continue
            if isinstance(node, ExecutionLoop):
                for iteration in range(1, node.repeat_count + 1):
                    yield from visit(
                        node.body,
                        path=f"{node_path}/iteration/{iteration}",
                        loop_id=node.loop_id,
                        loop_iteration=iteration,
                        parallel_id=parallel_id,
                        branch_id=branch_id,
                    )
                continue
            if isinstance(node, ExecutionSubworkflow):
                yield from visit(
                    node.body,
                    path=f"{node_path}/subworkflow/{node.subworkflow_id}",
                    loop_id=loop_id,
                    loop_iteration=loop_iteration,
                    parallel_id=parallel_id,
                    branch_id=branch_id,
                )
                continue
            for branch in node.branches:
                yield from visit(
                    branch.body,
                    path=f"{node_path}/branch/{branch.branch_id}",
                    loop_id=loop_id,
                    loop_iteration=loop_iteration,
                    parallel_id=node.parallel_id,
                    branch_id=branch.branch_id,
                )

    yield from visit(plan.root, path="root")


def _node_from_entry(entry: SequenceEntry, *, path: str) -> ExecutionNode:
    if isinstance(entry, SequenceItem):
        return ExecutionAction(entry.uuid, _clone_item(entry))
    if isinstance(entry, LoopBlock):
        return ExecutionLoop(
            node_id=entry.uuid,
            loop_id=entry.uuid,
            repeat_count=entry.repeat_count,
            body=ExecutionSequence(
                sequence_id=f"{entry.uuid}.body",
                children=tuple(
                    _node_from_entry(child, path=f"{path}/loop/{index}")
                    for index, child in enumerate(entry.items)
                ),
            ),
        )
    if isinstance(entry, ParallelBlock):
        return ExecutionParallel(
            node_id=entry.uuid,
            parallel_id=entry.uuid,
            branches=tuple(
                ExecutionBranch(
                    branch_id=branch.branch_id,
                    body=ExecutionSequence(
                        sequence_id=f"{entry.uuid}.{branch.branch_id}",
                        children=tuple(
                            _node_from_entry(
                                child,
                                path=f"{path}/branch/{branch.branch_id}/{index}",
                            )
                            for index, child in enumerate(branch.items)
                        ),
                    ),
                )
                for branch in entry.branches
            ),
            join_policy=entry.join_policy,
            failure_policy=entry.failure_policy,
        )
    if isinstance(entry, SubworkflowBlock):
        return ExecutionSubworkflow(
            node_id=entry.uuid,
            subworkflow_id=entry.uuid,
            name=entry.name,
            body=ExecutionSequence(
                sequence_id=f"{entry.uuid}.body",
                children=tuple(
                    _node_from_entry(child, path=f"{path}/subworkflow/{index}")
                    for index, child in enumerate(entry.items)
                ),
            ),
        )
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _clone_item(item: SequenceItem) -> SequenceItem:
    return SequenceItem(
        uuid=item.uuid,
        definition=ActionDefinition.from_dict(deepcopy(item.definition.to_dict())),
        status=SequenceItemStatus.PENDING,
    )


def _validate_sequence(
    sequence: ExecutionSequence,
    *,
    node_ids: set[str],
    item_ids: set[str],
    loop_ids: set[str],
    parallel_ids: set[str],
    depth: int,
) -> int:
    if depth > 32:
        raise ValueError("execution plan nesting exceeds 32 levels")
    expanded_steps = 0
    for node in sequence.children:
        if not isinstance(
            node,
            (ExecutionAction, ExecutionLoop, ExecutionParallel, ExecutionSubworkflow),
        ):
            raise TypeError("execution sequence contains an invalid node")
        _require_unique_id(node.node_id, "node", node_ids)
        if isinstance(node, ExecutionAction):
            _require_unique_id(node.item.uuid, "item", item_ids)
            expanded_steps += 1
            continue
        if isinstance(node, ExecutionLoop):
            _require_unique_id(node.loop_id, "loop", loop_ids)
            if isinstance(node.repeat_count, bool) or node.repeat_count < 2:
                raise ValueError("execution loop repeat_count must be at least 2")
            if not node.body.children:
                raise ValueError("execution loop body cannot be empty")
            expanded_steps += node.repeat_count * _validate_sequence(
                node.body,
                node_ids=node_ids,
                item_ids=item_ids,
                loop_ids=loop_ids,
                parallel_ids=parallel_ids,
                depth=depth + 1,
            )
            continue
        if isinstance(node, ExecutionSubworkflow):
            if not node.name.strip():
                raise ValueError("execution subworkflow name cannot be empty")
            if not node.body.children:
                raise ValueError("execution subworkflow body cannot be empty")
            expanded_steps += _validate_sequence(
                node.body,
                node_ids=node_ids,
                item_ids=item_ids,
                loop_ids=loop_ids,
                parallel_ids=parallel_ids,
                depth=depth + 1,
            )
            continue
        _require_unique_id(node.parallel_id, "parallel", parallel_ids)
        if not 2 <= len(node.branches) <= 8:
            raise ValueError("execution parallel must contain 2 to 8 branches")
        if not isinstance(node.join_policy, ParallelJoinPolicy):
            raise TypeError("execution parallel join policy is invalid")
        if not isinstance(node.failure_policy, ParallelFailurePolicy):
            raise TypeError("execution parallel failure policy is invalid")
        branch_ids: set[str] = set()
        for branch in node.branches:
            _require_unique_id(branch.branch_id, "parallel branch", branch_ids)
            if not branch.body.children:
                raise ValueError("execution parallel branch cannot be empty")
            expanded_steps += _validate_sequence(
                branch.body,
                node_ids=node_ids,
                item_ids=item_ids,
                loop_ids=loop_ids,
                parallel_ids=parallel_ids,
                depth=depth + 1,
            )
    return expanded_steps


def _require_unique_id(value: str, label: str, seen: set[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"execution {label} id cannot be empty")
    if value in seen:
        raise ValueError(f"duplicate execution {label} id: {value}")
    seen.add(value)

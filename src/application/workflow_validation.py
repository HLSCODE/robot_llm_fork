"""Structural workflow validation independent of Qt and device state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.action_schema import validate_action_parameters
from ..domain.workflow import (
    WorkflowActionNode,
    WorkflowDocument,
    WorkflowLoopNode,
    WorkflowNode,
    WorkflowParallelNode,
    WorkflowSequence,
)


DEFAULT_MAX_EXPANDED_STEPS = 10_000
DEFAULT_MAX_NESTING_DEPTH = 32
DEFAULT_MAX_PARALLEL_BRANCHES = 8


class WorkflowIssueCode(str, Enum):
    EMPTY = "empty"
    DUPLICATE_NODE_ID = "duplicate_node_id"
    DUPLICATE_ENTRY_UUID = "duplicate_entry_uuid"
    DUPLICATE_ITEM_UUID = "duplicate_item_uuid"
    DUPLICATE_BRANCH_ID = "duplicate_branch_id"
    INVALID_ACTION = "invalid_action"
    INVALID_LOOP = "invalid_loop"
    INVALID_PARALLEL = "invalid_parallel"
    EXPANSION_LIMIT = "expansion_limit"
    NESTING_LIMIT = "nesting_limit"


@dataclass(frozen=True, slots=True)
class WorkflowValidationIssue:
    code: WorkflowIssueCode
    message: str
    node_id: str = ""
    field: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowValidationResult:
    issues: tuple[WorkflowValidationIssue, ...]
    expanded_step_count: int

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(slots=True)
class _ValidationState:
    issues: list[WorkflowValidationIssue]
    node_ids: set[str]
    entry_uuids: set[str]
    item_uuids: set[str]
    branch_ids: set[str]


class WorkflowValidator:
    """Validate the canonical recursive control-flow tree and action snapshots."""

    def __init__(
        self,
        *,
        max_expanded_steps: int = DEFAULT_MAX_EXPANDED_STEPS,
        max_nesting_depth: int = DEFAULT_MAX_NESTING_DEPTH,
        max_parallel_branches: int = DEFAULT_MAX_PARALLEL_BRANCHES,
    ) -> None:
        self._max_expanded_steps = _positive_int(
            max_expanded_steps,
            "max_expanded_steps",
        )
        self._max_nesting_depth = _positive_int(
            max_nesting_depth,
            "max_nesting_depth",
        )
        self._max_parallel_branches = _positive_int(
            max_parallel_branches,
            "max_parallel_branches",
        )

    def validate(self, document: WorkflowDocument) -> WorkflowValidationResult:
        state = _ValidationState([], set(), set(), set(), set())
        if not document.root.children:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.EMPTY,
                "工作流至少需要一个动作、循环块或并行块",
            ))
        expanded = self._validate_sequence(document.root, state, depth=0)
        if expanded > self._max_expanded_steps:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.EXPANSION_LIMIT,
                f"工作流展开后包含 {expanded} 个步骤，超过限制 "
                f"{self._max_expanded_steps}",
            ))
        return WorkflowValidationResult(tuple(state.issues), expanded)

    def _validate_sequence(
        self,
        sequence: WorkflowSequence,
        state: _ValidationState,
        *,
        depth: int,
    ) -> int:
        if depth > self._max_nesting_depth:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.NESTING_LIMIT,
                f"工作流嵌套深度超过限制 {self._max_nesting_depth}",
            ))
            return 0
        return sum(
            self._validate_node(node, state, depth=depth)
            for node in sequence.children
        )

    def _validate_node(
        self,
        node: WorkflowNode,
        state: _ValidationState,
        *,
        depth: int,
    ) -> int:
        self._register_unique(
            node.node_id,
            state.node_ids,
            WorkflowIssueCode.DUPLICATE_NODE_ID,
            "节点 ID 重复",
            node.node_id,
            state.issues,
        )
        if isinstance(node, WorkflowActionNode):
            self._validate_action(node, state)
            self._register_unique(
                node.item_uuid,
                state.entry_uuids,
                WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
                "序列条目 UUID 重复",
                node.node_id,
                state.issues,
            )
            return 1
        if isinstance(node, WorkflowLoopNode):
            return self._validate_loop(node, state, depth=depth + 1)
        return self._validate_parallel(node, state, depth=depth + 1)

    def _validate_loop(
        self,
        node: WorkflowLoopNode,
        state: _ValidationState,
        *,
        depth: int,
    ) -> int:
        self._register_unique(
            node.loop_uuid,
            state.entry_uuids,
            WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
            "序列条目 UUID 重复",
            node.node_id,
            state.issues,
        )
        if node.repeat_count < 2:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.INVALID_LOOP,
                "循环次数必须是至少为 2 的整数",
                node_id=node.node_id,
                field="repeat_count",
            ))
        if not node.body.children:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.INVALID_LOOP,
                "循环块至少需要一个子节点",
                node_id=node.node_id,
                field="body.children",
            ))
        body_steps = self._validate_sequence(node.body, state, depth=depth)
        return body_steps * max(node.repeat_count, 0)

    def _validate_parallel(
        self,
        node: WorkflowParallelNode,
        state: _ValidationState,
        *,
        depth: int,
    ) -> int:
        self._register_unique(
            node.parallel_uuid,
            state.entry_uuids,
            WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
            "序列条目 UUID 重复",
            node.node_id,
            state.issues,
        )
        branch_count = len(node.branches)
        if not 2 <= branch_count <= self._max_parallel_branches:
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.INVALID_PARALLEL,
                "并行块分支数必须在 2 到 "
                f"{self._max_parallel_branches} 之间",
                node_id=node.node_id,
                field="branches",
            ))
        expanded = 0
        for branch in node.branches:
            self._register_unique(
                branch.branch_id,
                state.branch_ids,
                WorkflowIssueCode.DUPLICATE_BRANCH_ID,
                "并行分支 ID 重复",
                node.node_id,
                state.issues,
            )
            if not branch.body.children:
                state.issues.append(WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_PARALLEL,
                    f"并行分支不能为空: {branch.branch_id}",
                    node_id=node.node_id,
                    field="branches",
                ))
            expanded += self._validate_sequence(
                branch.body,
                state,
                depth=depth,
            )
        return expanded

    @staticmethod
    def _validate_action(
        node: WorkflowActionNode,
        state: _ValidationState,
    ) -> None:
        WorkflowValidator._register_unique(
            node.item_uuid,
            state.item_uuids,
            WorkflowIssueCode.DUPLICATE_ITEM_UUID,
            "动作 UUID 重复",
            node.node_id,
            state.issues,
        )
        definition = node.definition
        if not definition.id.strip() or not definition.name.strip():
            state.issues.append(WorkflowValidationIssue(
                WorkflowIssueCode.INVALID_ACTION,
                "动作 ID 和名称不能为空",
                node_id=node.node_id,
            ))
        validation = validate_action_parameters(
            definition.type,
            definition.parameters,
            apply_defaults=True,
            reject_unknown=True,
        )
        state.issues.extend(
            WorkflowValidationIssue(
                WorkflowIssueCode.INVALID_ACTION,
                issue.message,
                node_id=node.node_id,
                field=issue.field,
            )
            for issue in validation.issues
        )

    @staticmethod
    def _register_unique(
        value: str,
        seen: set[str],
        code: WorkflowIssueCode,
        label: str,
        node_id: str,
        issues: list[WorkflowValidationIssue],
    ) -> None:
        if value in seen:
            issues.append(WorkflowValidationIssue(
                code,
                f"{label}: {value}",
                node_id=node_id,
            ))
        seen.add(value)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value

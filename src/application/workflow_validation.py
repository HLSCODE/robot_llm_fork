"""Structural workflow validation independent of Qt and device state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.action_schema import validate_action_parameters
from ..domain.workflow import (
    WorkflowActionNode,
    WorkflowDocument,
    WorkflowLoopNode,
)


DEFAULT_MAX_EXPANDED_STEPS = 10_000


class WorkflowIssueCode(str, Enum):
    EMPTY = "empty"
    DUPLICATE_NODE_ID = "duplicate_node_id"
    DUPLICATE_ENTRY_UUID = "duplicate_entry_uuid"
    DUPLICATE_ITEM_UUID = "duplicate_item_uuid"
    INVALID_ACTION = "invalid_action"
    INVALID_LOOP = "invalid_loop"
    EXPANSION_LIMIT = "expansion_limit"


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


class WorkflowValidator:
    """Validate the canonical v2 control-flow tree and action snapshots."""

    def __init__(self, *, max_expanded_steps: int = DEFAULT_MAX_EXPANDED_STEPS) -> None:
        if (
            isinstance(max_expanded_steps, bool)
            or not isinstance(max_expanded_steps, int)
            or max_expanded_steps < 1
        ):
            raise ValueError("max_expanded_steps must be a positive integer")
        self._max_expanded_steps = max_expanded_steps

    def validate(self, document: WorkflowDocument) -> WorkflowValidationResult:
        issues: list[WorkflowValidationIssue] = []
        if not document.root.children:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.EMPTY,
                    "工作流至少需要一个动作或循环块",
                )
            )
        node_ids: set[str] = set()
        entry_uuids: set[str] = set()
        item_uuids: set[str] = set()
        expanded_step_count = 0
        for node in document.root.children:
            self._register_unique(
                node.node_id,
                node_ids,
                WorkflowIssueCode.DUPLICATE_NODE_ID,
                "节点 ID 重复",
                node.node_id,
                issues,
            )
            if isinstance(node, WorkflowActionNode):
                expanded_step_count += 1
                self._validate_action(node, item_uuids, issues)
                self._register_unique(
                    node.item_uuid,
                    entry_uuids,
                    WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
                    "序列条目 UUID 重复",
                    node.node_id,
                    issues,
                )
            else:
                expanded_step_count += self._validate_loop(
                    node,
                    node_ids,
                    entry_uuids,
                    item_uuids,
                    issues,
                )
        if expanded_step_count > self._max_expanded_steps:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.EXPANSION_LIMIT,
                    "工作流展开后包含 "
                    f"{expanded_step_count} 个步骤，超过限制 "
                    f"{self._max_expanded_steps}",
                )
            )
        return WorkflowValidationResult(tuple(issues), expanded_step_count)

    def _validate_loop(
        self,
        node: WorkflowLoopNode,
        node_ids: set[str],
        entry_uuids: set[str],
        item_uuids: set[str],
        issues: list[WorkflowValidationIssue],
    ) -> int:
        self._register_unique(
            node.loop_uuid,
            entry_uuids,
            WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
            "序列条目 UUID 重复",
            node.node_id,
            issues,
        )
        if node.repeat_count < 2:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_LOOP,
                    "循环次数必须是至少为 2 的整数",
                    node_id=node.node_id,
                    field="repeat_count",
                )
            )
        if not node.body:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_LOOP,
                    "循环块至少需要一个动作",
                    node_id=node.node_id,
                    field="body.children",
                )
            )
        for child in node.body:
            self._register_unique(
                child.node_id,
                node_ids,
                WorkflowIssueCode.DUPLICATE_NODE_ID,
                "节点 ID 重复",
                child.node_id,
                issues,
            )
            self._validate_action(child, item_uuids, issues)
        return len(node.body) * max(node.repeat_count, 0)

    @staticmethod
    def _validate_action(
        node: WorkflowActionNode,
        item_uuids: set[str],
        issues: list[WorkflowValidationIssue],
    ) -> None:
        WorkflowValidator._register_unique(
            node.item_uuid,
            item_uuids,
            WorkflowIssueCode.DUPLICATE_ITEM_UUID,
            "动作 UUID 重复",
            node.node_id,
            issues,
        )
        definition = node.definition
        if not definition.id.strip() or not definition.name.strip():
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_ACTION,
                    "动作 ID 和名称不能为空",
                    node_id=node.node_id,
                )
            )
        validation = validate_action_parameters(
            definition.type,
            definition.parameters,
            apply_defaults=True,
            reject_unknown=True,
        )
        issues.extend(
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
            issues.append(
                WorkflowValidationIssue(code, f"{label}: {value}", node_id=node_id)
            )
        seen.add(value)

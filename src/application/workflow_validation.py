"""Structural workflow validation independent of Qt and device state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.action_schema import validate_action_parameters
from ..domain.models import LoopBlock, SequenceItem
from ..domain.workflow import WorkflowDocument, WorkflowNode


DEFAULT_MAX_EXPANDED_STEPS = 10_000


class WorkflowIssueCode(str, Enum):
    EMPTY = "empty"
    DUPLICATE_NODE_ID = "duplicate_node_id"
    DUPLICATE_ENTRY_UUID = "duplicate_entry_uuid"
    DUPLICATE_ITEM_UUID = "duplicate_item_uuid"
    ORDER_DUPLICATE = "order_duplicate"
    ORDER_UNKNOWN_NODE = "order_unknown_node"
    ORDER_MISSING_NODE = "order_missing_node"
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
    """Validate persistent structure and canonical action schemas only."""

    def __init__(
        self,
        *,
        max_expanded_steps: int = DEFAULT_MAX_EXPANDED_STEPS,
    ) -> None:
        if (
            isinstance(max_expanded_steps, bool)
            or not isinstance(max_expanded_steps, int)
            or max_expanded_steps < 1
        ):
            raise ValueError("max_expanded_steps must be a positive integer")
        self._max_expanded_steps = max_expanded_steps

    def validate(self, document: WorkflowDocument) -> WorkflowValidationResult:
        issues: list[WorkflowValidationIssue] = []
        if not document.nodes:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.EMPTY,
                    "工作流至少需要一个动作或循环块",
                )
            )

        nodes_by_id: dict[str, WorkflowNode] = {}
        entry_uuids: set[str] = set()
        item_uuids: set[str] = set()
        expanded_step_count = 0
        for node in document.nodes:
            if node.node_id in nodes_by_id:
                issues.append(
                    WorkflowValidationIssue(
                        WorkflowIssueCode.DUPLICATE_NODE_ID,
                        f"节点 ID 重复: {node.node_id}",
                        node_id=node.node_id,
                    )
                )
            else:
                nodes_by_id[node.node_id] = node

            entry_uuid = node.entry.uuid
            if entry_uuid in entry_uuids:
                issues.append(
                    WorkflowValidationIssue(
                        WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
                        f"序列条目 UUID 重复: {entry_uuid}",
                        node_id=node.node_id,
                    )
                )
            entry_uuids.add(entry_uuid)

            if isinstance(node.entry, LoopBlock):
                expanded_step_count += self._validate_loop(
                    node,
                    item_uuids,
                    issues,
                )
            else:
                expanded_step_count += 1
                self._validate_item(node.entry, node.node_id, issues)
                self._register_item_uuid(
                    node.entry.uuid,
                    node.node_id,
                    item_uuids,
                    issues,
                )

        self._validate_order(document, nodes_by_id, issues)
        if expanded_step_count > self._max_expanded_steps:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.EXPANSION_LIMIT,
                    "工作流展开后包含 "
                    f"{expanded_step_count} 个步骤，超过限制 "
                    f"{self._max_expanded_steps}",
                )
            )
        return WorkflowValidationResult(
            issues=tuple(issues),
            expanded_step_count=expanded_step_count,
        )

    def _validate_loop(
        self,
        node: WorkflowNode,
        item_uuids: set[str],
        issues: list[WorkflowValidationIssue],
    ) -> int:
        block = node.entry
        if not isinstance(block, LoopBlock):
            raise TypeError("loop validator requires LoopBlock")
        if (
            isinstance(block.repeat_count, bool)
            or not isinstance(block.repeat_count, int)
            or block.repeat_count < 2
        ):
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_LOOP,
                    "循环次数必须是至少为 2 的整数",
                    node_id=node.node_id,
                    field="repeat_count",
                )
            )
        if not block.items:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_LOOP,
                    "循环块至少需要一个动作",
                    node_id=node.node_id,
                    field="items",
                )
            )
        for item in block.items:
            if not isinstance(item, SequenceItem):
                issues.append(
                    WorkflowValidationIssue(
                        WorkflowIssueCode.INVALID_LOOP,
                        "循环块只能包含规范动作条目",
                        node_id=node.node_id,
                        field="items",
                    )
                )
                continue
            self._validate_item(item, node.node_id, issues)
            self._register_item_uuid(
                item.uuid,
                node.node_id,
                item_uuids,
                issues,
            )
        if not isinstance(block.repeat_count, int) or isinstance(
            block.repeat_count,
            bool,
        ):
            return 0
        return len(block.items) * max(block.repeat_count, 0)

    @staticmethod
    def _validate_item(
        item: SequenceItem,
        node_id: str,
        issues: list[WorkflowValidationIssue],
    ) -> None:
        if not item.uuid.strip():
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_ACTION,
                    "动作 UUID 不能为空",
                    node_id=node_id,
                    field="uuid",
                )
            )
        definition = item.definition
        if not definition.id.strip() or not definition.name.strip():
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.INVALID_ACTION,
                    "动作 ID 和名称不能为空",
                    node_id=node_id,
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
                node_id=node_id,
                field=issue.field,
            )
            for issue in validation.issues
        )

    @staticmethod
    def _register_item_uuid(
        item_uuid: str,
        node_id: str,
        item_uuids: set[str],
        issues: list[WorkflowValidationIssue],
    ) -> None:
        if item_uuid in item_uuids:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.DUPLICATE_ITEM_UUID,
                    f"动作 UUID 重复: {item_uuid}",
                    node_id=node_id,
                )
            )
        item_uuids.add(item_uuid)

    @staticmethod
    def _validate_order(
        document: WorkflowDocument,
        nodes_by_id: dict[str, WorkflowNode],
        issues: list[WorkflowValidationIssue],
    ) -> None:
        ordered_ids: set[str] = set()
        for node_id in document.order:
            if node_id in ordered_ids:
                issues.append(
                    WorkflowValidationIssue(
                        WorkflowIssueCode.ORDER_DUPLICATE,
                        f"执行顺序重复引用节点: {node_id}",
                        node_id=node_id,
                    )
                )
            ordered_ids.add(node_id)
            if node_id not in nodes_by_id:
                issues.append(
                    WorkflowValidationIssue(
                        WorkflowIssueCode.ORDER_UNKNOWN_NODE,
                        f"执行顺序引用未知节点: {node_id}",
                        node_id=node_id,
                    )
                )
        for node_id in nodes_by_id.keys() - ordered_ids:
            issues.append(
                WorkflowValidationIssue(
                    WorkflowIssueCode.ORDER_MISSING_NODE,
                    f"节点未进入执行顺序: {node_id}",
                    node_id=node_id,
                )
            )

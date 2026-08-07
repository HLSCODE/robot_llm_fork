"""Compile a validated workflow document into one structured execution plan."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..domain.action_schema import validate_action_parameters
from ..domain.execution_plan import (
    ExecutionAction,
    ExecutionBranch,
    ExecutionLoop,
    ExecutionParallel,
    ExecutionPlan,
    ExecutionSequence,
    iter_execution_steps,
)
from ..domain.models import (
    ActionDefinition,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..domain.workflow import (
    WorkflowActionNode,
    WorkflowDocument,
    WorkflowLoopNode,
    WorkflowNode,
    WorkflowParallelNode,
    WorkflowSequence,
)
from .workflow_validation import WorkflowValidationResult, WorkflowValidator


@dataclass(frozen=True, slots=True)
class CompiledStep:
    runtime_index: int
    node_id: str
    item_uuid: str
    path: str
    loop_uuid: str = ""
    loop_iteration: int = 0
    parallel_uuid: str = ""
    branch_id: str = ""


@dataclass(frozen=True, slots=True)
class LoopNodeMapping:
    loop_uuid: str
    node_id: str


@dataclass(frozen=True, slots=True)
class ParallelNodeMapping:
    parallel_uuid: str
    node_id: str
    branch_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    workflow_id: str
    revision: int
    plan: ExecutionPlan
    entries: tuple[SequenceEntry, ...]
    steps: tuple[CompiledStep, ...]
    loops: tuple[LoopNodeMapping, ...]
    parallels: tuple[ParallelNodeMapping, ...]

    def node_id_for_step(self, runtime_index: int) -> str | None:
        if isinstance(runtime_index, bool) or not isinstance(runtime_index, int):
            raise TypeError("runtime_index must be an integer")
        if not 0 <= runtime_index < len(self.steps):
            return None
        return self.steps[runtime_index].node_id

    def node_id_for_loop(self, loop_uuid: str) -> str | None:
        return next(
            (mapping.node_id for mapping in self.loops if mapping.loop_uuid == loop_uuid),
            None,
        )

    def node_id_for_parallel(self, parallel_uuid: str) -> str | None:
        return next(
            (
                mapping.node_id
                for mapping in self.parallels
                if mapping.parallel_uuid == parallel_uuid
            ),
            None,
        )


class WorkflowCompilationError(ValueError):
    def __init__(self, validation: WorkflowValidationResult) -> None:
        self.validation = validation
        summary = "; ".join(issue.message for issue in validation.issues)
        super().__init__(summary or "workflow compilation failed")


class WorkflowCompiler:
    """Pure compiler; it never submits execution or touches a device."""

    def __init__(self, validator: WorkflowValidator | None = None) -> None:
        self._validator = validator or WorkflowValidator()

    def compile(self, document: WorkflowDocument) -> CompiledWorkflow:
        validation = self._validator.validate(document)
        if not validation.valid:
            raise WorkflowCompilationError(validation)
        plan_root, entries = self._compile_sequence(document.root, "root")
        plan = ExecutionPlan(plan_root)
        steps = tuple(
            CompiledStep(
                identity.runtime_index,
                identity.parallel_id or identity.loop_id or identity.node_id,
                item.uuid,
                identity.path,
                loop_uuid=identity.loop_id,
                loop_iteration=identity.loop_iteration,
                parallel_uuid=identity.parallel_id,
                branch_id=identity.branch_id,
            )
            for identity, item in iter_execution_steps(plan)
        )
        loops: list[LoopNodeMapping] = []
        parallels: list[ParallelNodeMapping] = []
        self._collect_mappings(document.root, loops, parallels)
        return CompiledWorkflow(
            workflow_id=document.workflow_id,
            revision=document.revision,
            plan=plan,
            entries=entries,
            steps=steps,
            loops=tuple(loops),
            parallels=tuple(parallels),
        )

    def _compile_sequence(
        self,
        sequence: WorkflowSequence,
        sequence_id: str,
    ) -> tuple[ExecutionSequence, tuple[SequenceEntry, ...]]:
        compiled = tuple(
            self._compile_node(node, f"{sequence_id}/{index}")
            for index, node in enumerate(sequence.children)
        )
        return (
            ExecutionSequence(sequence_id, tuple(node for node, _entry in compiled)),
            tuple(entry for _node, entry in compiled),
        )

    def _compile_node(
        self,
        node: WorkflowNode,
        path: str,
    ) -> tuple[ExecutionAction | ExecutionLoop | ExecutionParallel, SequenceEntry]:
        if isinstance(node, WorkflowActionNode):
            item = self._compile_item(node)
            return ExecutionAction(node.node_id, item), item
        if isinstance(node, WorkflowLoopNode):
            body, entries = self._compile_sequence(node.body, f"{path}.body")
            return (
                ExecutionLoop(node.node_id, node.loop_uuid, node.repeat_count, body),
                LoopBlock(node.loop_uuid, list(entries), node.repeat_count),
            )
        branches: list[ExecutionBranch] = []
        persisted_branches: list[ParallelBranch] = []
        for branch in node.branches:
            body, entries = self._compile_sequence(
                branch.body,
                f"{path}.branch.{branch.branch_id}",
            )
            branches.append(ExecutionBranch(branch.branch_id, body))
            persisted_branches.append(ParallelBranch(branch.branch_id, list(entries)))
        return (
            ExecutionParallel(
                node.node_id,
                node.parallel_uuid,
                tuple(branches),
                node.join_policy,
                node.failure_policy,
            ),
            ParallelBlock(
                node.parallel_uuid,
                persisted_branches,
                node.join_policy,
                node.failure_policy,
            ),
        )

    @staticmethod
    def _compile_item(node: WorkflowActionNode) -> SequenceItem:
        definition = node.definition
        validation = validate_action_parameters(
            definition.type,
            definition.parameters,
            apply_defaults=True,
            reject_unknown=True,
        )
        if not validation.is_valid:
            raise AssertionError("validator accepted invalid action parameters")
        return SequenceItem(
            uuid=node.item_uuid,
            definition=ActionDefinition(
                id=definition.id,
                name=definition.name,
                type=definition.type,
                parameters=deepcopy(validation.parameters),
            ),
            status=SequenceItemStatus.PENDING,
        )

    def _collect_mappings(
        self,
        sequence: WorkflowSequence,
        loops: list[LoopNodeMapping],
        parallels: list[ParallelNodeMapping],
    ) -> None:
        for node in sequence.children:
            if isinstance(node, WorkflowLoopNode):
                loops.append(LoopNodeMapping(node.loop_uuid, node.node_id))
                self._collect_mappings(node.body, loops, parallels)
            elif isinstance(node, WorkflowParallelNode):
                parallels.append(ParallelNodeMapping(
                    node.parallel_uuid,
                    node.node_id,
                    tuple(branch.branch_id for branch in node.branches),
                ))
                for branch in node.branches:
                    self._collect_mappings(branch.body, loops, parallels)

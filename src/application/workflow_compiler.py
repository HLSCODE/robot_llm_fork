"""Compile a validated workflow document into the execution sequence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..domain.action_schema import validate_action_parameters
from ..domain.models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..domain.workflow import WorkflowActionNode, WorkflowDocument, WorkflowLoopNode
from .workflow_validation import WorkflowValidationResult, WorkflowValidator


@dataclass(frozen=True, slots=True)
class CompiledStep:
    runtime_index: int
    node_id: str
    item_uuid: str
    loop_uuid: str = ""
    loop_iteration: int = 0


@dataclass(frozen=True, slots=True)
class LoopNodeMapping:
    loop_uuid: str
    node_id: str


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    workflow_id: str
    revision: int
    entries: tuple[SequenceEntry, ...]
    steps: tuple[CompiledStep, ...]
    loops: tuple[LoopNodeMapping, ...]

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
        entries: list[SequenceEntry] = []
        steps: list[CompiledStep] = []
        loops: list[LoopNodeMapping] = []
        for node in document.root.children:
            if isinstance(node, WorkflowActionNode):
                item = self._compile_item(node)
                entries.append(item)
                steps.append(CompiledStep(len(steps), node.node_id, item.uuid))
                continue
            loop = self._compile_loop(node)
            entries.append(loop)
            loops.append(LoopNodeMapping(loop.uuid, node.node_id))
            for iteration in range(1, loop.repeat_count + 1):
                for child in loop.items:
                    steps.append(
                        CompiledStep(
                            len(steps),
                            node.node_id,
                            child.uuid,
                            loop_uuid=loop.uuid,
                            loop_iteration=iteration,
                        )
                    )
        return CompiledWorkflow(
            workflow_id=document.workflow_id,
            revision=document.revision,
            entries=tuple(entries),
            steps=tuple(steps),
            loops=tuple(loops),
        )

    @classmethod
    def _compile_loop(cls, node: WorkflowLoopNode) -> LoopBlock:
        return LoopBlock(
            uuid=node.loop_uuid,
            items=[cls._compile_item(child) for child in node.body],
            repeat_count=node.repeat_count,
            current_iteration=0,
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

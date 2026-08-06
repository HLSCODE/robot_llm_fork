"""Compile a validated editor document into the canonical execution sequence."""

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
from ..domain.workflow import WorkflowDocument, WorkflowNode
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
            (
                mapping.node_id
                for mapping in self.loops
                if mapping.loop_uuid == loop_uuid
            ),
            None,
        )


class WorkflowCompilationError(ValueError):
    """Raised when a structurally invalid workflow cannot be compiled."""

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

        nodes_by_id = {node.node_id: node for node in document.nodes}
        entries: list[SequenceEntry] = []
        steps: list[CompiledStep] = []
        loops: list[LoopNodeMapping] = []
        for node_id in document.order:
            node = nodes_by_id[node_id]
            entry = self._compile_entry(node)
            entries.append(entry)
            if isinstance(entry, LoopBlock):
                loops.append(LoopNodeMapping(entry.uuid, node_id))
                for iteration in range(1, entry.repeat_count + 1):
                    for child in entry.items:
                        steps.append(
                            CompiledStep(
                                runtime_index=len(steps),
                                node_id=node_id,
                                item_uuid=child.uuid,
                                loop_uuid=entry.uuid,
                                loop_iteration=iteration,
                            )
                        )
            else:
                steps.append(
                    CompiledStep(
                        runtime_index=len(steps),
                        node_id=node_id,
                        item_uuid=entry.uuid,
                    )
                )

        return CompiledWorkflow(
            workflow_id=document.workflow_id,
            revision=document.revision,
            entries=tuple(entries),
            steps=tuple(steps),
            loops=tuple(loops),
        )

    @staticmethod
    def _compile_entry(node: WorkflowNode) -> SequenceEntry:
        if isinstance(node.entry, LoopBlock):
            return LoopBlock(
                uuid=node.entry.uuid,
                items=[
                    WorkflowCompiler._compile_item(child)
                    for child in node.entry.items
                ],
                repeat_count=node.entry.repeat_count,
                current_iteration=0,
            )
        return WorkflowCompiler._compile_item(node.entry)

    @staticmethod
    def _compile_item(item: SequenceItem) -> SequenceItem:
        definition = item.definition
        validation = validate_action_parameters(
            definition.type,
            definition.parameters,
            apply_defaults=True,
            reject_unknown=True,
        )
        if not validation.is_valid:
            raise AssertionError("validator accepted invalid action parameters")
        return SequenceItem(
            uuid=item.uuid,
            definition=ActionDefinition(
                id=definition.id,
                name=definition.name,
                type=definition.type,
                parameters=deepcopy(validation.parameters),
            ),
            status=SequenceItemStatus.PENDING,
        )

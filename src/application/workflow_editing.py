"""Single application-owned editing session for the current workflow document."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from ..domain.models import SubworkflowBlock
from ..domain.workflow import WorkflowDocument, instantiate_subworkflow
from .composition import CompositionService


@dataclass(frozen=True, slots=True)
class WorkflowEditingState:
    document: WorkflowDocument
    workflow_name: str = ""
    dirty: bool = False


class WorkflowEditingSession:
    """Own the current editable document, persistence identity and dirty state."""

    def __init__(self, composition: CompositionService) -> None:
        self._composition = composition
        self._lock = RLock()
        self._state = WorkflowEditingState(
            _empty_document(composition.robot_profile_id)
        )

    def snapshot(self) -> WorkflowEditingState:
        with self._lock:
            return WorkflowEditingState(
                WorkflowDocument.from_dict(self._state.document.to_dict()),
                self._state.workflow_name,
                self._state.dirty,
            )

    def new(self, *, name: str = "未命名任务") -> WorkflowEditingState:
        normalized = name.strip()
        if not normalized:
            raise ValueError("workflow name must not be empty")
        document = WorkflowDocument.from_entries(
            workflow_id=str(uuid4()),
            name=normalized,
            revision=0,
            entries=(),
            robot_profile_id=self._composition.robot_profile_id,
        )
        return self._set_state(document, workflow_name="", dirty=False)

    def open(self, workflow_name: str) -> WorkflowEditingState:
        document = self._composition.load_workflow(workflow_name)
        return self._set_state(
            document,
            workflow_name=workflow_name,
            dirty=False,
        )

    def replace_document(
        self,
        document: WorkflowDocument,
        *,
        dirty: bool = True,
    ) -> WorkflowEditingState:
        with self._lock:
            workflow_name = self._state.workflow_name
        return self._set_state(document, workflow_name=workflow_name, dirty=dirty)

    def instantiate(self, workflow_name: str) -> SubworkflowBlock:
        return instantiate_subworkflow(
            self._composition.load_workflow(workflow_name)
        )

    def save(self) -> tuple[str, WorkflowEditingState]:
        with self._lock:
            state = self._state
        if not state.workflow_name:
            raise ValueError("workflow has no storage name")
        stored_name, stored = self._composition.save_workflow(
            state.workflow_name,
            state.document,
            origin="gui",
            expected_revision=state.document.revision,
        )
        return stored_name, self._set_state(
            stored,
            workflow_name=stored_name,
            dirty=False,
        )

    def save_as(
        self,
        workflow_name: str,
    ) -> tuple[str, WorkflowEditingState]:
        with self._lock:
            document = self._state.document
        new_document = WorkflowDocument.from_entries(
            workflow_id=str(uuid4()),
            name=workflow_name.removesuffix(".workflow.json"),
            revision=0,
            entries=document.to_entries(),
            robot_profile_id=self._composition.robot_profile_id,
            positions=document.position_map(),
        )
        stored_name, stored = self._composition.save_workflow(
            workflow_name,
            new_document,
            origin="gui",
            expected_revision=0,
        )
        return stored_name, self._set_state(
            stored,
            workflow_name=stored_name,
            dirty=False,
        )

    def save_draft(self) -> None:
        with self._lock:
            document = self._state.document
        self._composition.save_workflow_draft(document)

    def _set_state(
        self,
        document: WorkflowDocument,
        *,
        workflow_name: str,
        dirty: bool,
    ) -> WorkflowEditingState:
        scoped_document = self._composition.scope_workflow(document)
        state = WorkflowEditingState(
            WorkflowDocument.from_dict(scoped_document.to_dict()),
            workflow_name,
            dirty,
        )
        with self._lock:
            self._state = state
        return self.snapshot()


def _empty_document(robot_profile_id: str) -> WorkflowDocument:
    return WorkflowDocument.from_entries(
        workflow_id=str(uuid4()),
        name="未命名任务",
        revision=0,
        entries=(),
        robot_profile_id=robot_profile_id,
    )

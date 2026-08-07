from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.application import (
    CompositionRevisionConflict,
    CompositionService,
    WorkflowCompilationError,
    WorkflowCompiler,
    WorkflowIssueCode,
    WorkflowPreflightIssueCode,
    WorkflowPreflightService,
    WorkflowValidator,
)
from src.domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)
from src.domain.execution_plan import (
    ExecutionPlan,
    ExecutionSubworkflow,
    iter_execution_steps,
)
from src.domain.workflow import (
    CanvasPosition,
    UnsupportedWorkflowDocumentVersion,
    WorkflowActionNode,
    WorkflowDocument,
    WorkflowLoopNode,
    WorkflowParallelNode,
)
from src.execution import ExecutionSnapshot, ExecutionState
from src.persistence.storage import JsonCompositionRepository


class WorkflowDocumentTests(unittest.TestCase):
    def test_subworkflow_round_trip_and_execution_plan_preserve_scope(self) -> None:
        subworkflow = SubworkflowBlock(
            uuid="subworkflow-1",
            name="Reusable",
            items=[_item("child")],
            source_workflow_id="source-workflow",
            source_revision=3,
        )
        document = WorkflowDocument.from_entries(
            workflow_id="workflow-subworkflow",
            name="Subworkflow",
            revision=1,
            entries=(subworkflow,),
        )

        restored = WorkflowDocument.from_dict(document.to_dict())
        plan = ExecutionPlan.from_entries(restored.to_entries())

        self.assertEqual(document, restored)
        self.assertIsInstance(plan.root.children[0], ExecutionSubworkflow)
        self.assertIn(
            "/subworkflow/subworkflow-1/",
            tuple(iter_execution_steps(plan))[0][0].path,
        )

    def test_versioned_round_trip_preserves_layout_and_resets_runtime_state(self) -> None:
        action = _item("action-item")
        action.status = SequenceItemStatus.SUCCESS
        loop_child = _item("loop-child")
        loop_child.status = SequenceItemStatus.FAILED
        loop = LoopBlock(
            uuid="loop-entry",
            items=[loop_child],
            repeat_count=3,
            current_iteration=2,
        )
        document = WorkflowDocument.from_entries(
            workflow_id="workflow-1",
            name="Example",
            revision=4,
            entries=(action, loop),
            positions={
                "action-item": CanvasPosition(12.5, 24.0),
                "loop-entry": CanvasPosition(12.5, 80.0),
            },
        )

        restored = WorkflowDocument.from_dict(document.to_dict())

        self.assertEqual(document.workflow_id, restored.workflow_id)
        self.assertEqual(document.root, restored.root)
        self.assertEqual(
            CanvasPosition(12.5, 24.0),
            restored.position_map()["action-item"],
        )
        restored_action, restored_loop = restored.root.children
        self.assertIsInstance(restored_action, WorkflowActionNode)
        self.assertIsInstance(restored_loop, WorkflowLoopNode)
        self.assertNotIn("status", restored.to_dict()["root"]["children"][0])
        self.assertNotIn("current_iteration", restored.to_dict()["root"]["children"][1])

    def test_future_document_version_is_rejected(self) -> None:
        document = _document((_node("node-1", _item("item-1")),))
        payload = document.to_dict()
        payload["schema_version"] = 99

        with self.assertRaises(UnsupportedWorkflowDocumentVersion):
            WorkflowDocument.from_dict(payload)

    def test_nested_parallel_and_loop_round_trip_as_v4(self) -> None:
        parallel = ParallelBlock(
            uuid="parallel-1",
            branches=[
                ParallelBranch("left", [_item("left-item")]),
                ParallelBranch(
                    "right",
                    [LoopBlock("nested-loop", [_item("right-item")], 2)],
                ),
            ],
        )
        document = WorkflowDocument.from_entries(
            workflow_id="workflow-structured",
            name="Structured",
            revision=1,
            entries=(parallel,),
        )

        payload = document.to_dict()
        restored = WorkflowDocument.from_dict(payload)

        self.assertEqual(4, payload["schema_version"])
        restored_parallel = restored.root.children[0]
        self.assertIsInstance(restored_parallel, WorkflowParallelNode)
        assert isinstance(restored_parallel, WorkflowParallelNode)
        self.assertIsInstance(
            restored_parallel.branches[1].body.children[0],
            WorkflowLoopNode,
        )
        self.assertEqual(document, restored)

    def test_direct_execution_plan_rejects_invalid_parallel_structure(self) -> None:
        invalid = ParallelBlock(
            uuid="parallel-invalid",
            branches=[ParallelBranch("only", [_item("item-1")])],
        )

        with self.assertRaisesRegex(ValueError, "2 to 8 branches"):
            ExecutionPlan.from_entries((invalid,))

    def test_direct_execution_plan_rejects_duplicate_action_identity(self) -> None:
        item = _item("duplicate-item")
        parallel = ParallelBlock(
            uuid="parallel-duplicate",
            branches=[
                ParallelBranch("left", [item]),
                ParallelBranch("right", [item]),
            ],
        )

        with self.assertRaisesRegex(ValueError, "duplicate execution node id"):
            ExecutionPlan.from_entries((parallel,))


class WorkflowPersistenceTests(unittest.TestCase):
    def test_composition_service_owns_revisioned_workflow_persistence(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = CompositionService(
                JsonCompositionRepository(
                    actions_directory=root / "actions",
                    workflows_directory=root / "workflows",
                    workflow_drafts_directory=root / "drafts",
                )
            )
            document = _document((_node("node-1", _item("item-1")),))

            stored_name, stored = service.save_workflow(
                "demo",
                document,
                origin="test",
                expected_revision=0,
            )

            self.assertEqual("demo.workflow.json", stored_name)
            self.assertEqual(1, stored.revision)
            self.assertEqual(("demo.workflow.json",), service.list_workflows())
            self.assertEqual(stored, service.load_workflow("demo"))
            with self.assertRaises(CompositionRevisionConflict):
                service.save_workflow(
                    "demo",
                    document,
                    origin="stale-editor",
                    expected_revision=0,
                )
            self.assertEqual(1, service.load_workflow("demo").revision)

    def test_workflow_draft_can_be_recovered_and_discarded(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = CompositionService(
                JsonCompositionRepository(
                    actions_directory=root / "actions",
                    workflows_directory=root / "workflows",
                    workflow_drafts_directory=root / "drafts",
                )
            )
            document = _document((_node("node-1", _item("item-1")),))

            service.save_workflow_draft(document)

            self.assertEqual(document, service.load_workflow_draft())
            self.assertTrue(service.discard_workflow_draft())
            self.assertIsNone(service.load_workflow_draft())
            self.assertFalse(service.discard_workflow_draft())


class WorkflowValidationTests(unittest.TestCase):
    def test_invalid_order_duplicate_uuid_and_action_parameters_are_reported(self) -> None:
        first = _item("shared", wait_seconds=0)
        second = _item("shared")
        document = WorkflowDocument.from_entries(
            workflow_id="workflow-1",
            name="Invalid",
            revision=0,
            entries=(first, second),
        )

        result = WorkflowValidator().validate(document)

        self.assertFalse(result.valid)
        self.assertEqual(
            {
                WorkflowIssueCode.DUPLICATE_NODE_ID,
                WorkflowIssueCode.DUPLICATE_ENTRY_UUID,
                WorkflowIssueCode.DUPLICATE_ITEM_UUID,
                WorkflowIssueCode.INVALID_ACTION,
            },
            {issue.code for issue in result.issues},
        )

    def test_loop_expansion_limit_is_checked_without_mutating_document(self) -> None:
        loop = LoopBlock(
            uuid="loop-1",
            items=[_item("child-1"), _item("child-2")],
            repeat_count=3,
        )
        document = _document((_node("loop-node", loop),))

        result = WorkflowValidator(max_expanded_steps=5).validate(document)

        self.assertEqual(6, result.expanded_step_count)
        self.assertIn(
            WorkflowIssueCode.EXPANSION_LIMIT,
            {issue.code for issue in result.issues},
        )
        self.assertEqual(3, loop.repeat_count)


class WorkflowCompilerTests(unittest.TestCase):
    def test_compile_uses_document_order_and_builds_runtime_event_mapping(self) -> None:
        plain = _item("plain-item", wait_seconds=None)
        loop = LoopBlock(
            uuid="loop-entry",
            items=[_item("loop-a"), _item("loop-b")],
            repeat_count=2,
        )
        document = WorkflowDocument.from_entries(
            workflow_id="workflow-1",
            name="Compile",
            revision=7,
            entries=(loop, plain),
        )

        compiled = WorkflowCompiler().compile(document)

        self.assertEqual(7, compiled.revision)
        self.assertEqual(("loop-entry", "plain-item"), tuple(
            entry.uuid for entry in compiled.entries
        ))
        self.assertEqual(5, len(compiled.steps))
        self.assertEqual(
            ("loop-entry", "loop-entry", "loop-entry", "loop-entry", "plain-item"),
            tuple(step.node_id for step in compiled.steps),
        )
        self.assertEqual((1, 1, 2, 2, 0), tuple(
            step.loop_iteration for step in compiled.steps
        ))
        self.assertEqual("loop-entry", compiled.node_id_for_loop("loop-entry"))
        self.assertEqual("plain-item", compiled.node_id_for_step(4))
        self.assertIsNone(compiled.node_id_for_step(99))
        compiled_plain = compiled.entries[1]
        assert isinstance(compiled_plain, SequenceItem)
        self.assertEqual(1.0, compiled_plain.definition.parameters["wait_seconds"])

    def test_invalid_document_is_not_compiled(self) -> None:
        document = _document((_node("node-1", _item("item-1", wait_seconds=0)),))

        with self.assertRaises(WorkflowCompilationError) as context:
            WorkflowCompiler().compile(document)

        self.assertFalse(context.exception.validation.valid)

    def test_parallel_compile_has_stable_branch_paths_and_mapping(self) -> None:
        parallel = ParallelBlock(
            uuid="parallel-1",
            branches=[
                ParallelBranch("left", [_item("left-item")]),
                ParallelBranch(
                    "right",
                    [LoopBlock("nested-loop", [_item("right-item")], 2)],
                ),
            ],
        )

        compiled = WorkflowCompiler().compile(
            WorkflowDocument.from_entries(
                workflow_id="workflow-parallel",
                name="Parallel",
                revision=2,
                entries=(parallel,),
            )
        )

        self.assertEqual(3, len(compiled.steps))
        self.assertEqual(
            ("left", "right", "right"),
            tuple(step.branch_id for step in compiled.steps),
        )
        self.assertEqual((0, 1, 2), tuple(step.runtime_index for step in compiled.steps))
        self.assertEqual("parallel-1", compiled.node_id_for_parallel("parallel-1"))
        self.assertTrue(all(step.path.startswith("root/0/branch/") for step in compiled.steps))


class WorkflowPreflightTests(unittest.TestCase):
    def test_active_execution_and_device_readiness_are_reported_separately(self) -> None:
        compiled = WorkflowCompiler().compile(
            _document((_node("node-1", _item("item-1")),))
        )
        execution = _ExecutionProbe(
            state=ExecutionState.RUNNING,
            resources=("robot_system", "camera"),
        )
        devices = _DeviceProbe(
            {
                "robot_system": {"ready": False},
            }
        )

        result = WorkflowPreflightService(execution, devices).check(compiled)

        self.assertFalse(result.ready)
        self.assertEqual(("robot_system", "camera"), result.required_resources)
        self.assertEqual(
            {
                WorkflowPreflightIssueCode.EXECUTION_ACTIVE,
                WorkflowPreflightIssueCode.DEVICE_NOT_READY,
                WorkflowPreflightIssueCode.DEVICE_MISSING,
            },
            {issue.code for issue in result.issues},
        )

    def test_policy_failure_is_a_typed_preflight_issue(self) -> None:
        compiled = WorkflowCompiler().compile(
            _document((_node("node-1", _item("item-1")),))
        )
        execution = _ExecutionProbe(
            state=ExecutionState.IDLE,
            error=ValueError("unsupported action policy"),
        )

        result = WorkflowPreflightService(
            execution,
            _DeviceProbe({}),
        ).check(compiled)

        self.assertEqual((), result.required_resources)
        self.assertEqual(
            (WorkflowPreflightIssueCode.POLICY_REJECTED,),
            tuple(issue.code for issue in result.issues),
        )


class _ExecutionProbe:
    def __init__(
        self,
        *,
        state: ExecutionState,
        resources: tuple[str, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._state = state
        self._resources = resources
        self._error = error

    def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(run_id=None, state=self._state)

    def required_resources(self, sequence) -> tuple[str, ...]:
        if self._error is not None:
            raise self._error
        return self._resources


class _DeviceProbe:
    def __init__(self, statuses: dict[str, dict[str, object]]) -> None:
        self._statuses = statuses

    def status(self) -> dict[str, dict[str, object]]:
        return self._statuses


def _action(
    action_id: str,
    *,
    wait_seconds: float | None = 1.0,
) -> ActionDefinition:
    parameters = {} if wait_seconds is None else {"wait_seconds": wait_seconds}
    return ActionDefinition(
        id=action_id,
        name=f"Action {action_id}",
        type=ActionType.WAIT,
        parameters=parameters,
    )


def _item(
    item_uuid: str,
    *,
    wait_seconds: float | None = 1.0,
) -> SequenceItem:
    return SequenceItem(
        uuid=item_uuid,
        definition=_action(item_uuid, wait_seconds=wait_seconds),
    )


def _node(node_id: str, entry):
    del node_id
    return entry


def _document(nodes) -> WorkflowDocument:
    return WorkflowDocument.from_entries(
        workflow_id="workflow-1",
        name="Workflow",
        revision=0,
        entries=nodes,
    )


if __name__ == "__main__":
    unittest.main()

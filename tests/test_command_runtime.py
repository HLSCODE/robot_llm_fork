from __future__ import annotations

import unittest

from src.application import (
    CommandValidation,
    CommandRuntime,
    ExecutionControlAction,
    PreviewExpiredError,
    PreviewNotFoundError,
    PreviewSourceMismatchError,
    PreviewState,
    PreviewStateError,
    PreviewVersionConflictError,
    RiskAcknowledgementRequiredError,
)
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.commands import ActionCommand
from src.execution import ExecutionSnapshot, ExecutionState


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Execution:
    def __init__(self) -> None:
        self.state = ExecutionState.IDLE
        self.calls: list[str] = []

    def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot("run" if self.state != ExecutionState.IDLE else None, self.state)

    def cancel(self) -> None:
        self.calls.append("cancel")

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")


class _SkillEngine:
    def list_all_skills(self):
        return []


class _Catalog:
    def entries(self):
        return []


def _item(action_type: ActionType = ActionType.WAIT) -> SequenceItem:
    return SequenceItem.from_definition(
        ActionDefinition(
            id="a",
            name="a",
            type=action_type,
            parameters={},
        )
    )


class CommandRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.execution = _Execution()
        ids = iter(("preview-1", "preview-2", "preview-3"))
        self.runtime = CommandRuntime(
            execution=self.execution,
            skill_engine=_SkillEngine(),
            composition=object(),
            workflow_compiler=object(),
            catalog=_Catalog(),
            preview_ttl_s=10,
            clock=self.clock,
            id_factory=lambda: next(ids),
        )

    def _register(
        self,
        action_type: ActionType = ActionType.WAIT,
    ):
        return self.runtime.register(
            [_item(action_type)],
            source="test",
            plan={},
            command_info={},
            validation=CommandValidation.succeeded("valid"),
        )

    def test_new_preview_supersedes_old_version(self):
        first = self._register()
        second = self._register()

        self.assertEqual(2, second.version)
        with self.assertRaises(PreviewNotFoundError):
            self.runtime.confirm(first.preview_id, second.version)
        with self.assertRaises(PreviewVersionConflictError):
            self.runtime.confirm(second.preview_id, first.version)

    def test_typed_action_is_validated_and_expanded_by_runtime(self):
        preparation = self.runtime.prepare(
            ActionCommand(ActionType.WAIT, {"wait_seconds": 0.1}),
            source="test",
            plan={"provenance": "deterministic"},
        )

        self.assertTrue(preparation.validation.is_valid)
        assert preparation.preview is not None
        self.assertEqual(
            "WAIT",
            preparation.preview.sequence[0]["definition"]["type"],
        )
        self.assertEqual(
            "action",
            preparation.preview.command_info["kind"],
        )

    def test_preview_expires_and_cannot_be_confirmed(self):
        preview = self._register()
        self.clock.now += 11

        with self.assertRaises(PreviewExpiredError):
            self.runtime.confirm(preview.preview_id, preview.version)
        self.assertEqual(PreviewState.EXPIRED, self.runtime.current().state)

    def test_confirmation_is_single_use(self):
        preview = self._register()
        confirmed = self.runtime.confirm(
            preview.preview_id,
            preview.version,
        )

        self.assertEqual(preview.preview_id, confirmed.preview_id)
        self.assertIsNone(self.runtime.pending())
        with self.assertRaises(PreviewStateError):
            self.runtime.confirm(preview.preview_id, preview.version)

    def test_high_risk_requires_explicit_acknowledgement(self):
        preview = self._register(ActionType.MOVE)

        with self.assertRaises(RiskAcknowledgementRequiredError):
            self.runtime.confirm(preview.preview_id, preview.version)
        confirmed = self.runtime.confirm(
            preview.preview_id,
            preview.version,
            risk_acknowledged=True,
        )

        self.assertEqual(preview.preview_id, confirmed.preview_id)

    def test_preview_cannot_be_confirmed_by_another_surface(self):
        preview = self._register()

        with self.assertRaises(PreviewSourceMismatchError):
            self.runtime.confirm(
                preview.preview_id,
                preview.version,
                expected_source="websocket-ai",
            )

    def test_cancel_targets_execution_before_pending_preview(self):
        self._register()
        self.execution.state = ExecutionState.RUNNING

        result = self.runtime.control_execution(
            ExecutionControlAction.CANCEL
        )

        self.assertEqual("execution_cancel_requested", result)
        self.assertEqual(["cancel"], self.execution.calls)
        self.assertEqual(PreviewState.PENDING, self.runtime.current().state)

    def test_cancel_does_not_cross_interaction_surface(self):
        self._register()

        result = self.runtime.control_execution(
            ExecutionControlAction.CANCEL,
            expected_source="websocket-ai",
        )

        self.assertEqual("nothing_to_cancel", result)
        self.assertIsNotNone(self.runtime.pending(expected_source="test"))


if __name__ == "__main__":
    unittest.main()

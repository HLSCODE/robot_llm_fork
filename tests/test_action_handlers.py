from __future__ import annotations

import time
import unittest

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.execution import (
    ActionExecutionContext,
    ActionHandlerNotFoundError,
    ActionHandlerResult,
    ActionHandlerRegistry,
    ActionResultCode,
    ActionResultStatus,
    ActionTimeoutError,
    ExecutionControl,
    ExecutionEventType,
    ExecutionState,
)


class ActionHandlerRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_registration(self):
        registry = ActionHandlerRegistry()
        handler = lambda _parameters, _context: (
            ActionHandlerResult.succeeded()
        )
        registry.register(ActionType.WAIT, handler)

        with self.assertRaisesRegex(
            ValueError,
            "handler already registered",
        ):
            registry.register(ActionType.WAIT, handler)

    def test_registry_reports_all_missing_action_types(self):
        registry = ActionHandlerRegistry()
        registry.register(
            ActionType.WAIT,
            lambda _parameters, _context: (
                ActionHandlerResult.succeeded()
            ),
        )

        with self.assertRaises(ActionHandlerNotFoundError) as raised:
            registry.validate_complete()

        self.assertIn(ActionType.MOVE.value, str(raised.exception))
        self.assertNotIn(ActionType.WAIT.value, str(raised.exception))

    def test_complete_registry_is_frozen_before_execution(self):
        registry = ActionHandlerRegistry()
        handler = lambda _parameters, _context: (
            ActionHandlerResult.succeeded()
        )
        for action_type in ActionType:
            registry.register(action_type, handler)

        registry.validate_complete()

        with self.assertRaisesRegex(RuntimeError, "registry is frozen"):
            registry.register(ActionType.WAIT, handler)

    def test_registry_rejects_legacy_boolean_result(self):
        registry = ActionHandlerRegistry()
        registry.register(
            ActionType.WAIT,
            lambda _parameters, _context: True,
        )
        context = ActionExecutionContext(
            action_name="legacy handler",
            control=ExecutionControl(),
            timeout_seconds=1,
            log=lambda _message, _level: None,
        )

        with self.assertRaisesRegex(
            TypeError,
            "expected ActionHandlerResult",
        ):
            registry.execute(ActionType.WAIT, {}, context)


class ActionHandlerResultTests(unittest.TestCase):
    def test_failure_exposes_stable_code_and_operation_context(self):
        result = ActionHandlerResult.failed(
            ActionResultCode.DEVICE_OPERATION_FAILED,
            "device call failed",
            operation="robot.move",
            device_id="robot-system",
        )

        self.assertEqual(ActionResultStatus.FAILED, result.status)
        self.assertFalse(result.successful)
        self.assertEqual(
            {
                "status": "failed",
                "code": "device_operation_failed",
                "operation": "robot.move",
                "device_id": "robot-system",
            },
            result.to_event_data(),
        )
        with self.assertRaisesRegex(TypeError, "no boolean compatibility"):
            bool(result)

    def test_result_rejects_contradictory_status_and_code(self):
        with self.assertRaisesRegex(
            ValueError,
            "successful action result",
        ):
            ActionHandlerResult(
                status=ActionResultStatus.SUCCEEDED,
                code=ActionResultCode.INTERNAL_ERROR,
            )


class ActionExecutionContextTests(unittest.TestCase):
    def test_invoke_reports_timeout_only_after_blocking_call_returns(self):
        context = ActionExecutionContext(
            action_name="blocking device call",
            control=ExecutionControl(),
            timeout_seconds=0.01,
            log=lambda _message, _level: None,
        )
        returned = False

        def operation() -> None:
            nonlocal returned
            time.sleep(0.03)
            returned = True

        with self.assertRaisesRegex(
            ActionTimeoutError,
            "returned after the deadline",
        ):
            context.invoke("fake.block", operation)

        self.assertTrue(returned)

    def test_invoke_does_not_hide_timeout_behind_device_failure(self):
        context = ActionExecutionContext(
            action_name="failed blocking device call",
            control=ExecutionControl(),
            timeout_seconds=0.01,
            log=lambda _message, _level: None,
        )

        def operation() -> None:
            time.sleep(0.03)
            raise RuntimeError("late device failure")

        with self.assertRaises(ActionTimeoutError):
            context.invoke("fake.block", operation)

    def test_pause_does_not_disable_hard_action_deadline(self):
        control = ExecutionControl()
        control.pause()
        context = ActionExecutionContext(
            action_name="paused action",
            control=control,
            timeout_seconds=0.02,
            log=lambda _message, _level: None,
        )

        with self.assertRaises(ActionTimeoutError):
            context.checkpoint()


class ActionTimeoutIntegrationTests(unittest.TestCase):
    def test_wait_action_timeout_is_a_failed_terminal_result(self):
        config = type(
            "TestConfig",
            (),
            {"EXECUTION_ACTION_TIMEOUT_SECONDS": 1.0},
        )()
        services = create_application_services(config, simulation=True)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="timed-wait",
                name="timed wait",
                type=ActionType.WAIT,
                parameters={
                    "wait_seconds": 0.2,
                    "timeout_seconds": 0.02,
                },
            )
        )

        events = []
        final = services.execution.start(
            [item],
            origin="test",
            listener=events.append,
        ).wait(1)

        self.assertEqual(ExecutionState.FAILED, final.state)
        self.assertIn("action timed out after 0.02s", final.error)
        self.assertEqual("action_timeout", final.error_code)
        self.assertEqual("action.execute", final.error_operation)
        step_failed = next(
            event
            for event in events
            if event.event_type is ExecutionEventType.STEP_FAILED
        )
        self.assertEqual("action_timeout", step_failed.data["code"])
        self.assertEqual("action.execute", step_failed.data["operation"])
        terminal = next(
            event
            for event in events
            if event.event_type is ExecutionEventType.FAILED
        )
        self.assertEqual("action_timeout", terminal.data["code"])
        self.assertFalse(services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()

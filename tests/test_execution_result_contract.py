from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

from src.core.models import (
    ActionDefinition,
    ActionType,
    SequenceItem,
    SequenceItemStatus,
)
from src.execution import (
    ActionHandlerResult,
    ActionResultCode,
    ExecutionEvent,
    ExecutionEventType,
    ExecutionSnapshot,
    ExecutionState,
)
from src.robot_server.ws_server import RobotWebSocketServer


class ExecutionResultWebSocketContractTests(unittest.TestCase):
    def test_step_started_exposes_action_control_policy(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        messages: list[dict] = []
        server._broadcast_threadsafe = messages.append
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={},
            )
        )
        item.status = SequenceItemStatus.RUNNING
        policy = {
            "operation": "wait",
            "cancellation_mode": "bounded_cooperative",
            "blocking_device_call": False,
            "device_ids": [],
            "stop_targets": [],
            "expected_max_cancel_latency_seconds": 0.1,
            "hardware_validation_required": False,
        }
        event = ExecutionEvent(
            run_id="run-1",
            event_type=ExecutionEventType.STEP_STARTED,
            origin="test",
            index=1,
            item=item,
            data=policy,
        )

        server._on_execution_event(event)

        self.assertEqual(
            {
                "event": "step_started",
                "index": 1,
                "name": "wait",
                "status": "RUNNING",
                "control_policy": policy,
            },
            messages[0],
        )

    def test_step_failure_preserves_structured_handler_result(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        messages: list[dict] = []
        server._broadcast_threadsafe = messages.append
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="move",
                name="move arm",
                type=ActionType.MOVE,
                parameters={},
            )
        )
        failure = ActionHandlerResult.failed(
            ActionResultCode.DEVICE_OPERATION_FAILED,
            "robot move failed",
            operation="robot_system.move_to_pose",
            device_id="robot-system",
        )
        event = ExecutionEvent(
            run_id="run-1",
            event_type=ExecutionEventType.STEP_FAILED,
            origin="test",
            index=2,
            item=item,
            message=failure.message,
            data=failure.to_event_data(),
        )

        server._on_execution_event(event)

        self.assertEqual(
            {
                "event": "step_failed",
                "index": 2,
                "name": "move arm",
                "error": "robot move failed",
                "failure": {
                    "status": "failed",
                    "code": "device_operation_failed",
                    "operation": "robot_system.move_to_pose",
                    "device_id": "robot-system",
                },
            },
            messages[0],
        )

    def test_status_exposes_latest_structured_execution_failure(self):
        snapshot = ExecutionSnapshot(
            run_id="run-1",
            state=ExecutionState.FAILED,
            origin="test",
            error="robot move failed",
            error_code="device_operation_failed",
            error_operation="robot_system.move_to_pose",
            error_device_id="robot-system",
        )
        services = SimpleNamespace(
            execution=SimpleNamespace(snapshot=lambda: snapshot),
            devices=SimpleNamespace(status=lambda: {}),
            device_runtime=SimpleNamespace(
                get_if_ready=lambda _device_id: None,
            ),
            composition=SimpleNamespace(sequence_entries=lambda: ()),
        )
        server = RobotWebSocketServer(services=services)
        websocket = _RecordingWebSocket()

        asyncio.run(server._handle_status(websocket, {}))

        response = json.loads(websocket.messages[0])
        self.assertEqual(
            {
                "run_id": "run-1",
                "state": "failed",
                "running": False,
                "paused": False,
                "error": "robot move failed",
                "error_code": "device_operation_failed",
                "error_operation": "robot_system.move_to_pose",
                "error_device_id": "robot-system",
            },
            response["executor"],
        )


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


if __name__ == "__main__":
    unittest.main()

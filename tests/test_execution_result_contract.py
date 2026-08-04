from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from src.application import (
    DataCollectionSnapshot,
    DataCollectionState,
)
from src.domain.models import (
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
from src.robot_server.protocol import WEBSOCKET_API_VERSION
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

        server._execution_handler._on_execution_event(event)

        self.assertEqual(
            {
                "event": "step_started",
                "index": 1,
                "name": "wait",
                "status": "RUNNING",
                "control_policy": policy,
                "run_id": "run-1",
                "origin": "test",
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
            error_category="protocol",
            raw_error_code="crc",
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

        server._execution_handler._on_execution_event(event)

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
                    "error_category": "protocol",
                    "raw_error_code": "crc",
                },
                "run_id": "run-1",
                "origin": "test",
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
            error_category="rejected",
            raw_error_code="17",
        )
        services = SimpleNamespace(
            data_collection=SimpleNamespace(
                snapshot=lambda: DataCollectionSnapshot(
                    state=DataCollectionState.SESSION_READY,
                    task="pick",
                    description="test",
                    next_episode_id=3,
                    episode_id=None,
                    teleoperation_shared=True,
                )
            ),
            execution=SimpleNamespace(snapshot=lambda: snapshot),
            devices=SimpleNamespace(status=lambda: {}),
            camera_access=SimpleNamespace(
                status=lambda: SimpleNamespace(to_dict=lambda: {
                    "available": False,
                    "camera_count": 0,
                    "cameras": [],
                }),
            ),
            composition=SimpleNamespace(sequence_entries=lambda: ()),
        )
        server = RobotWebSocketServer(services=services)
        websocket = _RecordingWebSocket()

        asyncio.run(server._device_handler._handle_status(websocket, {}))

        response = json.loads(websocket.messages[0])
        self.assertEqual(
            {
                "state": "session_ready",
                "task": "pick",
                "next_episode_id": 3,
                "episode_id": None,
                "recording": False,
                "teleoperation_shared": True,
            },
            response["data_collection"],
        )
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
                "error_category": "rejected",
                "raw_error_code": "17",
            },
            response["executor"],
        )

    def test_execution_request_is_correlated_through_terminal_audit(self):
        audit_events = []
        execution = _ImmediateExecution()
        services = SimpleNamespace(
            execution=execution,
            teleoperation=SimpleNamespace(active=False),
        )
        server = RobotWebSocketServer(
            services=services,
            auth_token="test-secret",
            audit_sink=audit_events.append,
        )
        websocket = _RecordingWebSocket()
        client_id = server._register_client(websocket, "local")
        server._access.authenticate(client_id, "test-secret")
        server._access.acquire_control(client_id)
        execution_events: list[dict] = []
        server._broadcast_threadsafe = execution_events.append

        asyncio.run(server._dispatch(
            websocket,
            {
                "api_version": WEBSOCKET_API_VERSION,
                "action": "execute",
                "request_id": "execute-42",
                "sequence": [{
                    "id": "wait",
                    "name": "wait",
                    "type": ActionType.WAIT.value,
                    "parameters": {"duration": 0},
                }],
            },
        ))

        accepted = next(
            payload
            for payload in websocket.payloads
            if payload["event"] == "accepted"
        )
        self.assertEqual("run-42", accepted["run_id"])
        self.assertEqual("execute-42", accepted["request_id"])
        self.assertEqual("execute", accepted["action"])

        terminal = next(
            payload
            for payload in execution_events
            if payload["event"] == "execution_finished"
        )
        self.assertEqual("run-42", terminal["run_id"])
        self.assertEqual("execute-42", terminal["request_id"])
        self.assertEqual("execute", terminal["action"])
        self.assertEqual("succeeded", terminal["state"])
        self.assertTrue(terminal["success"])

        correlated_audits = [
            event
            for event in audit_events
            if event.run_id == "run-42"
        ]
        self.assertEqual(
            ["accepted", "succeeded"],
            [event.outcome for event in correlated_audits],
        )
        self.assertTrue(all(
            event.request_id == "execute-42"
            for event in correlated_audits
        ))


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)

    @property
    def payloads(self) -> list[dict]:
        return [json.loads(message) for message in self.messages]


class _ImmediateExecution:
    def snapshot(self):
        return SimpleNamespace(active=False)

    def start(self, _sequence, *, origin, listener):
        listener(ExecutionEvent(
            run_id="run-42",
            event_type=ExecutionEventType.ACCEPTED,
            origin=origin,
        ))
        listener(ExecutionEvent(
            run_id="run-42",
            event_type=ExecutionEventType.SUCCEEDED,
            origin=origin,
        ))
        return SimpleNamespace(run_id="run-42")


if __name__ == "__main__":
    unittest.main()

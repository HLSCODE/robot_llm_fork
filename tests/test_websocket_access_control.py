from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

from src.execution import ExecutionState
from src.robot_server.access_control import (
    WebSocketAccessController,
    WebSocketAccessError,
)
from src.robot_server.protocol import (
    RequestCorrelation,
    WebSocketRequestContext,
)
from src.robot_server.ws_server import RobotWebSocketServer


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.remote_address = ("127.0.0.1", 12345)

    async def send(self, message: str) -> None:
        self.messages.append(message)

    @property
    def payloads(self) -> list[dict]:
        return [json.loads(message) for message in self.messages]


class _FakeExecution:
    def __init__(self) -> None:
        self.pause_count = 0
        self.state = ExecutionState.RUNNING

    def snapshot(self):
        return SimpleNamespace(state=self.state)

    def pause(self) -> None:
        self.pause_count += 1


class _FakeTeleoperation:
    def __init__(self) -> None:
        self.active = False
        self.stop_count = 0

    def stop(self) -> None:
        self.active = False
        self.stop_count += 1


class WebSocketAccessControllerTests(unittest.TestCase):
    def test_authentication_and_expiring_single_controller_lease(self):
        clock = _FakeClock()
        access = WebSocketAccessController(
            "correct-secret",
            control_lease_seconds=10,
            clock=clock,
        )
        access.register("client-1", "local")
        access.register("client-2", "local")

        with self.assertRaisesRegex(
            WebSocketAccessError,
            "认证凭据无效",
        ):
            access.authenticate("client-1", "wrong-secret")

        access.authenticate("client-1", "correct-secret")
        access.authenticate("client-2", "correct-secret")
        lease = access.acquire_control("client-1")
        self.assertEqual("client-1", lease.owner_client_id)

        with self.assertRaisesRegex(
            WebSocketAccessError,
            "另一个客户端",
        ):
            access.acquire_control("client-2")

        clock.now += 11
        self.assertEqual("client-1", access.expire_control())
        self.assertEqual(
            "client-2",
            access.acquire_control("client-2").owner_client_id,
        )

    def test_missing_server_token_keeps_write_authentication_locked(self):
        access = WebSocketAccessController(
            "",
            control_lease_seconds=30,
        )
        access.register("client", "local")

        with self.assertRaisesRegex(
            WebSocketAccessError,
            "尚未配置",
        ) as raised:
            access.authenticate("client", "any-token")

        self.assertEqual(
            "authentication_not_configured",
            raised.exception.code,
        )


class WebSocketRequestContextTests(unittest.TestCase):
    def test_specialized_errors_are_normalized_at_protocol_boundary(self):
        context = WebSocketRequestContext(RequestCorrelation(
            client_id="client-1",
            principal="api-key",
            action="start_teleoperation",
            request_id="teleop-1",
        ))

        response = context.decorate({
            "event": "teleop_error",
            "message": "device rejected command",
        })

        self.assertEqual("error", response["event"])
        self.assertEqual("teleop_error", response["error_source"])
        self.assertEqual("teleoperation_failed", response["code"])
        self.assertEqual("teleop-1", response["request_id"])
        self.assertEqual("start_teleoperation", response["action"])


class WebSocketDispatchAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.execution = _FakeExecution()
        self.teleoperation = _FakeTeleoperation()
        self.audit_events = []
        self.server = RobotWebSocketServer(
            services=SimpleNamespace(
                execution=self.execution,
                teleoperation=self.teleoperation,
            ),
            auth_token="correct-secret",
            control_lease_seconds=30,
            audit_sink=self.audit_events.append,
        )

    def test_write_requires_authentication_and_control_lease(self):
        websocket = _RecordingWebSocket()
        self.server._register_client(
            websocket,
            websocket.remote_address,
        )

        async def scenario() -> None:
            await self.server._dispatch(
                websocket,
                {"action": "pause", "request_id": "pause-0"},
            )
            await self.server._dispatch(
                websocket,
                {
                    "action": "authenticate",
                    "token": "wrong-secret",
                    "request_id": "auth-0",
                },
            )
            await self.server._dispatch(
                websocket,
                {
                    "action": "authenticate",
                    "token": "correct-secret",
                    "request_id": "auth-1",
                },
            )
            await self.server._dispatch(
                websocket,
                {"action": "pause", "request_id": "pause-1"},
            )
            await self.server._dispatch(
                websocket,
                {
                    "action": "acquire_control",
                    "request_id": "control-1",
                },
            )
            await self.server._dispatch(
                websocket,
                {"action": "pause", "request_id": "pause-2"},
            )

        asyncio.run(scenario())

        access_errors = [
            payload["code"]
            for payload in websocket.payloads
            if payload["event"] == "access_denied"
        ]
        self.assertEqual(
            [
                "authentication_required",
                "invalid_credentials",
                "control_required",
            ],
            access_errors,
        )
        self.assertEqual(1, self.execution.pause_count)
        self.assertEqual(
            "completed",
            self.audit_events[-1].outcome,
        )
        self.assertEqual("pause-2", self.audit_events[-1].request_id)
        serialized_audit = json.dumps(
            [event.to_dict() for event in self.audit_events]
        )
        self.assertNotIn("correct-secret", serialized_audit)
        self.assertNotIn("wrong-secret", serialized_audit)

    def test_handler_error_uses_correlated_stable_envelope(self):
        websocket = _RecordingWebSocket()
        client_id = self.server._register_client(
            websocket,
            websocket.remote_address,
        )
        self.server._access.authenticate(client_id, "correct-secret")
        self.server._access.acquire_control(client_id)
        self.execution.state = ExecutionState.IDLE

        asyncio.run(self.server._dispatch(
            websocket,
            {"action": "pause", "request_id": "pause-invalid"},
        ))

        response = websocket.payloads[-1]
        self.assertEqual("error", response["event"])
        self.assertEqual("request_failed", response["code"])
        self.assertEqual("pause-invalid", response["request_id"])
        self.assertEqual("pause", response["action"])
        self.assertEqual("rejected", self.audit_events[-1].outcome)
        self.assertEqual(
            "request_failed",
            self.audit_events[-1].code,
        )

    def test_unexpected_handler_error_is_isolated_and_not_disclosed(self):
        websocket = _RecordingWebSocket()
        client_id = self.server._register_client(
            websocket,
            websocket.remote_address,
        )
        self.server._access.authenticate(client_id, "correct-secret")
        self.server._access.acquire_control(client_id)

        def raise_sensitive_error() -> None:
            raise RuntimeError("sensitive internal detail")

        self.execution.pause = raise_sensitive_error
        with patch("src.robot_server.ws_server.logger.exception"):
            asyncio.run(self.server._dispatch(
                websocket,
                {"action": "pause", "request_id": "pause-failed"},
            ))

        response = websocket.payloads[-1]
        self.assertEqual("error", response["event"])
        self.assertEqual("internal_error", response["code"])
        self.assertEqual("pause-failed", response["request_id"])
        self.assertEqual("pause", response["action"])
        self.assertNotIn("sensitive internal detail", response["message"])
        self.assertEqual("failed", self.audit_events[-1].outcome)
        self.assertEqual("RuntimeError", self.audit_events[-1].code)

    def test_non_owner_disconnect_does_not_stop_owner_session(self):
        owner = _RecordingWebSocket()
        observer = _RecordingWebSocket()
        owner_id = self.server._register_client(
            owner,
            owner.remote_address,
        )
        observer_id = self.server._register_client(
            observer,
            observer.remote_address,
        )
        self.server._access.authenticate(owner_id, "correct-secret")
        self.server._access.authenticate(observer_id, "correct-secret")
        self.server._access.acquire_control(owner_id)
        self.teleoperation.active = True

        async def scenario() -> None:
            await self.server._unregister_client(
                observer,
                reason="disconnect",
            )
            self.assertEqual(0, self.teleoperation.stop_count)
            await self.server._unregister_client(
                owner,
                reason="disconnect",
            )

        asyncio.run(scenario())

        self.assertEqual(1, self.teleoperation.stop_count)
        self.assertIsNone(self.server._access.control_snapshot())

    def test_expired_control_lease_stops_owned_teleoperation(self):
        websocket = _RecordingWebSocket()
        server = RobotWebSocketServer(
            services=SimpleNamespace(
                execution=self.execution,
                teleoperation=self.teleoperation,
            ),
            auth_token="correct-secret",
            control_lease_seconds=0.05,
            audit_sink=self.audit_events.append,
        )
        client_id = server._register_client(
            websocket,
            websocket.remote_address,
        )
        server._access.authenticate(client_id, "correct-secret")
        server._access.acquire_control(client_id)
        self.teleoperation.active = True

        async def scenario() -> None:
            monitor = asyncio.create_task(
                server._control_lease_monitor()
            )
            try:
                for _ in range(20):
                    if self.teleoperation.stop_count:
                        break
                    await asyncio.sleep(0.02)
            finally:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor

        asyncio.run(scenario())

        self.assertEqual(1, self.teleoperation.stop_count)
        self.assertIsNone(server._access.control_snapshot())


if __name__ == "__main__":
    unittest.main()

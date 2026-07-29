from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

from src.robot_server.protocol import WEBSOCKET_API_VERSION
from src.robot_server.request_limits import WebSocketRequestLimiter
from src.robot_server.ws_server import (
    RobotWebSocketServer,
    _BoundedWebSocket,
)


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


def _request(action: str, request_id: str) -> dict[str, str]:
    return {
        "api_version": WEBSOCKET_API_VERSION,
        "action": action,
        "request_id": request_id,
    }


class WebSocketRequestLimiterTests(unittest.TestCase):
    def test_rate_limit_has_deterministic_retry_window(self):
        clock = _FakeClock()
        limiter = WebSocketRequestLimiter(
            max_requests_per_second=2,
            max_concurrent_requests=4,
            clock=clock,
        )

        for _ in range(2):
            self.assertTrue(limiter.admit("client-1").accepted)
            limiter.release("client-1")

        rejected = limiter.admit("client-1")
        self.assertFalse(rejected.accepted)
        self.assertEqual("rate_limited", rejected.code)
        self.assertAlmostEqual(1.0, rejected.retry_after_seconds or 0)

        clock.now += 1.0
        self.assertTrue(limiter.admit("client-1").accepted)
        limiter.release("client-1")

    def test_concurrency_limit_is_global_and_released_explicitly(self):
        limiter = WebSocketRequestLimiter(
            max_requests_per_second=10,
            max_concurrent_requests=1,
        )

        self.assertTrue(limiter.admit("client-1").accepted)
        rejected = limiter.admit("client-2")
        self.assertFalse(rejected.accepted)
        self.assertEqual("server_busy", rejected.code)

        limiter.release("client-1")
        self.assertTrue(limiter.admit("client-2").accepted)
        limiter.release("client-2")


class WebSocketProtocolContractTests(unittest.TestCase):
    def test_live_connection_send_has_a_deadline(self):
        class SlowWebSocket:
            async def send(self, _message: str) -> None:
                await asyncio.Event().wait()

        websocket = _BoundedWebSocket(
            SlowWebSocket(),
            timeout_seconds=0.01,
        )

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(websocket.send("message"))

    def test_api_version_is_required_and_echoed_on_all_responses(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        websocket = _RecordingWebSocket()
        server._register_client(websocket, websocket.remote_address)

        async def scenario() -> None:
            await server._dispatch(
                websocket,
                {
                    "action": "control_status",
                    "request_id": "missing-version",
                },
            )
            await server._dispatch(
                websocket,
                {
                    "api_version": "2.0",
                    "action": "control_status",
                    "request_id": "wrong-version",
                },
            )
            await server._dispatch(
                websocket,
                _request("control_status", "supported-version"),
            )

        asyncio.run(scenario())

        self.assertEqual(
            [
                "api_version_required",
                "unsupported_api_version",
            ],
            [
                payload["code"]
                for payload in websocket.payloads[:2]
            ],
        )
        self.assertEqual(
            "control_status",
            websocket.payloads[-1]["event"],
        )
        self.assertTrue(all(
            payload["api_version"] == WEBSOCKET_API_VERSION
            for payload in websocket.payloads
        ))

    def test_rate_limit_response_keeps_request_correlation(self):
        server = RobotWebSocketServer(
            services=SimpleNamespace(),
            max_requests_per_second=1,
        )
        websocket = _RecordingWebSocket()
        server._register_client(websocket, websocket.remote_address)

        async def scenario() -> None:
            await server._dispatch(
                websocket,
                _request("control_status", "status-1"),
            )
            await server._dispatch(
                websocket,
                _request("control_status", "status-2"),
            )

        asyncio.run(scenario())

        rejected = websocket.payloads[-1]
        self.assertEqual("error", rejected["event"])
        self.assertEqual("rate_limited", rejected["code"])
        self.assertEqual("status-2", rejected["request_id"])
        self.assertEqual("control_status", rejected["action"])
        self.assertGreater(rejected["retry_after_seconds"], 0)

    def test_server_rejects_requests_over_global_concurrency_limit(self):
        server = RobotWebSocketServer(
            services=SimpleNamespace(),
            max_concurrent_requests=1,
        )
        first = _RecordingWebSocket()
        second = _RecordingWebSocket()
        server._register_client(first, first.remote_address)
        server._register_client(second, second.remote_address)
        entered = asyncio.Event()
        release = asyncio.Event()
        route_type = type(server._routes["control_status"])

        async def hold_request(websocket, _request_payload) -> None:
            entered.set()
            await release.wait()
            await websocket.send(server._json_msg({"event": "held"}))

        server._routes["hold"] = route_type(
            hold_request,
            server._routes["control_status"].access_level,
            audited=False,
        )

        async def scenario() -> None:
            first_task = asyncio.create_task(server._dispatch(
                first,
                _request("hold", "hold-1"),
            ))
            await entered.wait()
            await server._dispatch(
                second,
                _request("hold", "hold-2"),
            )
            release.set()
            await first_task

        asyncio.run(scenario())

        self.assertEqual("held", first.payloads[-1]["event"])
        self.assertEqual("server_busy", second.payloads[-1]["code"])
        self.assertEqual("hold-2", second.payloads[-1]["request_id"])

    def test_event_audience_distinguishes_broadcast_and_subscription(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        first = _RecordingWebSocket()
        second = _RecordingWebSocket()
        server._clients.update((first, second))

        async def scenario() -> None:
            await server._broadcast({"event": "system_event"})
            await server._send_to_subscribers(
                {"event": "camera_frames", "frames": []},
                {first},
            )

        asyncio.run(scenario())

        self.assertEqual(
            ["system_event", "camera_frames"],
            [payload["event"] for payload in first.payloads],
        )
        self.assertEqual(
            ["system_event"],
            [payload["event"] for payload in second.payloads],
        )


if __name__ == "__main__":
    unittest.main()

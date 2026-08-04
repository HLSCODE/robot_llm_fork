from __future__ import annotations

import asyncio
import json
import unittest

from src.application import create_application_services
from src.core.settings import ApplicationSettings
from src.devices.runtime.ids import ROBOT_SYSTEM
from src.robot_server.ws_server import RobotWebSocketServer


class _RecordingWebSocket:
    remote_address = ("test", 1)

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)

    @property
    def payloads(self) -> list[dict]:
        return [json.loads(message) for message in self.messages]


class TeleoperationWebSocketTests(unittest.TestCase):
    def test_websocket_uses_but_does_not_own_application_llm_registry(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        server = RobotWebSocketServer(services)

        server._interaction_handler._init_ai()

        controller = server._interaction_controller
        self.assertIsNotNone(controller)
        self.assertIs(controller.llm_registry, services.llm)
        server._interaction_handler._close_interaction_session()
        self.assertIsNone(server._interaction_controller)
        self.assertIs(
            services.llm.get_provider(),
            services.llm.get_provider(),
        )

        asyncio.run(services.llm.close())

    def test_handler_uses_application_owned_session_and_command_counts(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.device_runtime.initialize(ROBOT_SYSTEM)
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()
        client_id = server._register_client(
            websocket,
            websocket.remote_address,
        )

        async def scenario() -> None:
            await server._teleoperation_handler._handle_teleop_start(
                websocket,
                {"arm": "左"},
            )
            await server._teleoperation_handler._handle_teleop_joint(
                websocket,
                {
                    "arm": "左",
                    "joints": [0, 1, 2, 3, 4, 5],
                    "follow": True,
                    "trajectory_mode": 0,
                },
            )
            owner = services.teleoperation.snapshot().owner(
                f"websocket:{client_id}"
            )
            assert owner is not None
            self.assertEqual(1, owner.command_count("left"))

            await server._teleoperation_handler._handle_teleop_stop(
                websocket,
                {"arm": "左"},
            )

        asyncio.run(scenario())

        self.assertFalse(services.teleoperation.active)
        stopped = next(
            payload
            for payload in websocket.payloads
            if payload["event"] == "teleop_stopped"
        )
        self.assertEqual(1, stopped["total_counts"]["左"])
        self.assertFalse(services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()

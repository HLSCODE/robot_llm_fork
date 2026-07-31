import asyncio
import json
import unittest

from src.core.models import SequenceItemStatus
from src.robot_server.ws_server import RobotWebSocketServer


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class FakeExecutor:
    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.executions = []
        self.stop_called = False

    def execute(self, entries):
        self.executions.append(entries)
        self.is_running = True

    def stop(self):
        self.stop_called = True

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False


class TaskCommandWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = RobotWebSocketServer()
        self.server._executor = FakeExecutor()
        self.server._loop = asyncio.get_running_loop()
        self.websocket = FakeWebSocket()
        self.server._clients.add(self.websocket)

    async def _flush_broadcasts(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_accept_start_step_and_complete_events_are_correlated(self):
        await self.server._dispatch(
            self.websocket,
            {
                "command_type": "730-3-1",
                "request_id": "request-123",
                "station_id": 4,
                "method": "circular",
            },
        )
        self.assertEqual(1, len(self.server._executor.executions))
        events = [message["event"] for message in self.websocket.messages]
        self.assertEqual("command_accepted", events[0])
        self.assertIn("command_started", events)

        item = self.server._executor.executions[0][0]
        item.status = SequenceItemStatus.RUNNING
        self.server._on_step_started(0, item)
        await self._flush_broadcasts()
        step_event = next(
            message for message in self.websocket.messages if message["event"] == "step_started"
        )
        self.assertEqual("request-123", step_event["request_id"])
        self.assertEqual("730-3-1", step_event["command_type"])

        self.server._executor.is_running = False
        self.server._on_finished()
        await self._flush_broadcasts()
        completed = next(
            message
            for message in self.websocket.messages
            if message["event"] == "command_completed"
        )
        self.assertEqual("request-123", completed["request_id"])
        self.assertIsNone(self.server._active_task_command)
        self.assertIn(
            "execution_finished",
            [message["event"] for message in self.websocket.messages],
        )

    async def test_busy_command_is_rejected_without_queueing(self):
        self.server._executor.is_running = True
        await self.server._dispatch(
            self.websocket,
            {"command_type": "730-peiye", "request_id": "busy-1"},
        )
        rejection = self.websocket.messages[-1]
        self.assertEqual("command_rejected", rejection["event"])
        self.assertEqual("BUSY", rejection["code"])
        self.assertEqual("busy-1", rejection["request_id"])
        self.assertEqual([], self.server._executor.executions)

    async def test_action_and_command_type_are_rejected_as_ambiguous(self):
        await self.server._dispatch(
            self.websocket,
            {
                "action": "execute_task",
                "command_type": "730-peiye",
                "request_id": "ambiguous-1",
            },
        )
        rejection = self.websocket.messages[-1]
        self.assertEqual("command_rejected", rejection["event"])
        self.assertEqual("AMBIGUOUS_REQUEST", rejection["code"])

    async def test_step_failure_produces_command_failed(self):
        await self.server._dispatch(
            self.websocket,
            {"command_type": "730-3-1", "request_id": "failure-1"},
        )
        item = self.server._executor.executions[0][0]
        self.server._on_step_failed(0, item, "mock failure")
        self.server._executor.is_running = False
        self.server._on_finished()
        await self._flush_broadcasts()
        failed = next(
            message
            for message in self.websocket.messages
            if message["event"] == "command_failed"
        )
        self.assertEqual("EXECUTION_FAILED", failed["code"])
        self.assertEqual("mock failure", failed["message"])
        self.assertEqual("failure-1", failed["request_id"])

    async def test_stop_marks_active_command_failed(self):
        await self.server._dispatch(
            self.websocket,
            {"command_type": "730-3-1", "request_id": "stop-1"},
        )
        await self.server._handle_stop(self.websocket, {"action": "stop"})
        self.assertTrue(self.server._executor.stop_called)
        self.server._executor.is_running = False
        self.server._on_finished()
        await self._flush_broadcasts()
        failed = next(
            message
            for message in self.websocket.messages
            if message["event"] == "command_failed"
        )
        self.assertEqual("STOPPED", failed["code"])
        self.assertEqual("stop-1", failed["request_id"])


if __name__ == "__main__":
    unittest.main()

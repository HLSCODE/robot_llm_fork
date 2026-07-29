from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.llm import LLMPlanResult
from src.robot_server.ws_server import RobotWebSocketServer
from src.skill_system.models import ValidationCode, ValidationResult
from src.voice_interaction.core.router import VoiceIntentRouter
from src.voice_interaction.core.session import VoiceSession


class _Planner:
    async def plan(self, _text, _skills) -> LLMPlanResult:
        return LLMPlanResult(
            skill_id="safe-skill",
            skill_name="safe skill",
            parameters={},
            reasoning="matched",
            confidence=1.0,
        )


class _TaskRunner:
    async def stream_chat(self, **_options):
        if False:
            yield None


class _SkillEngine:
    def __init__(self, item: SequenceItem) -> None:
        self._item = item

    def list_all_skills(self) -> list[dict]:
        return [{"id": "safe-skill"}]

    def get_skill_info(self, _skill_id: str) -> dict:
        return {"id": "safe-skill"}

    def parse_and_expand(self, _match):
        return [self._item], ValidationResult.succeeded("valid")


class _RejectedSkillEngine(_SkillEngine):
    def parse_and_expand(self, _match):
        return [], ValidationResult.failed(
            ValidationCode.UNSUPPORTED_ACTION_TYPE,
            "unsupported action type",
        )


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def _sequence_item() -> SequenceItem:
    return SequenceItem.from_definition(
        ActionDefinition(
            id="wait",
            name="wait",
            type=ActionType.WAIT,
            parameters={"seconds": 0},
        )
    )


class VoiceCommandSafetyTests(unittest.TestCase):
    def test_valid_command_only_emits_confirmable_preview(self):
        registry = SimpleNamespace(
            skill_planner=_Planner(),
            task_runner=_TaskRunner(),
        )
        router = VoiceIntentRouter(
            registry,
            VoiceSession(),
            skill_engine=_SkillEngine(_sequence_item()),
        )

        async def scenario():
            return [
                event
                async for event in router.route(
                    "run safe skill",
                    {"intent": "command"},
                )
            ]

        events = asyncio.run(scenario())
        preview = next(
            event for event in events if event.type == "command_preview"
        )

        self.assertTrue(preview.data["validation"]["is_valid"])
        self.assertEqual("valid", preview.data["validation"]["code"])
        self.assertIs(True, preview.data["requires_confirmation"])
        self.assertNotIn("auto_execute", preview.data)
        self.assertNotIn(
            "command_started",
            [event.type for event in events],
        )

    def test_invalid_action_type_never_produces_a_preview(self):
        registry = SimpleNamespace(
            skill_planner=_Planner(),
            task_runner=_TaskRunner(),
        )
        router = VoiceIntentRouter(
            registry,
            VoiceSession(),
            skill_engine=_RejectedSkillEngine(_sequence_item()),
        )

        async def scenario():
            return [
                event
                async for event in router.route(
                    "run unsafe skill",
                    {"intent": "command"},
                )
            ]

        events = asyncio.run(scenario())

        self.assertNotIn(
            "command_preview",
            [event.type for event in events],
        )
        feedback = next(event for event in events if event.type == "done")
        self.assertEqual(
            "unsupported_action_type",
            feedback.data["validation"]["code"],
        )

    def test_websocket_rejects_preview_without_confirmation_policy(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        broadcasts: list[dict] = []

        async def record(payload: dict) -> None:
            broadcasts.append(payload)

        server._broadcast = record
        event = {
            "type": "command_preview",
            "text": "preview",
            "data": {
                "sequence": [_sequence_item().to_dict()],
                "validation": {"is_valid": True, "code": "valid"},
            },
        }

        asyncio.run(server._emit_interaction_event(event))

        self.assertFalse(server._ai_preview_sequence)
        self.assertFalse(server._ai_preview_validated)
        self.assertEqual("error", broadcasts[0]["event"])

    def test_websocket_rejects_inconsistent_validation_code(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        broadcasts: list[dict] = []

        async def record(payload: dict) -> None:
            broadcasts.append(payload)

        server._broadcast = record
        event = {
            "type": "command_preview",
            "text": "preview",
            "data": {
                "sequence": [_sequence_item().to_dict()],
                "validation": {
                    "is_valid": True,
                    "code": "unsupported_action_type",
                },
                "requires_confirmation": True,
            },
        }

        asyncio.run(server._emit_interaction_event(event))

        self.assertFalse(server._ai_preview_sequence)
        self.assertFalse(server._ai_preview_validated)
        self.assertEqual("error", broadcasts[0]["event"])

    def test_websocket_confirm_rejects_unvalidated_sequence(self):
        server = RobotWebSocketServer(services=SimpleNamespace())
        server._ai_preview_sequence = [_sequence_item()]
        server._ai_preview_validated = False
        websocket = _RecordingWebSocket()

        asyncio.run(server._handle_ai_confirm(websocket, {}))

        self.assertEqual(1, len(websocket.messages))
        self.assertIn("未通过校验", websocket.messages[0])


if __name__ == "__main__":
    unittest.main()

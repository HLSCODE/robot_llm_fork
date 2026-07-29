from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from src.application import CommandRuntime
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.execution import ExecutionSnapshot, ExecutionState
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
    def __init__(
        self,
        item: SequenceItem,
        validation: ValidationResult | None = None,
    ) -> None:
        self._item = item
        self._validation = validation or ValidationResult.succeeded("valid")

    def list_all_skills(self) -> list[dict]:
        return [{"id": "safe-skill"}]

    def get_skill_info(self, _skill_id: str) -> dict:
        return {"id": "safe-skill"}

    def parse_and_expand(self, _match):
        if not self._validation.is_valid:
            return [], self._validation
        return [self._item], self._validation


class _Execution:
    def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(None, ExecutionState.IDLE)


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def _sequence_item(
    action_type: ActionType = ActionType.WAIT,
) -> SequenceItem:
    parameters = (
        {"seconds": 0}
        if action_type is ActionType.WAIT
        else {"position_name": "home"}
    )
    return SequenceItem.from_definition(
        ActionDefinition(
            id="action",
            name="action",
            type=action_type,
            parameters=parameters,
        )
    )


def _runtime(
    item: SequenceItem,
    validation: ValidationResult | None = None,
) -> CommandRuntime:
    return CommandRuntime(
        execution=_Execution(),
        skill_engine=_SkillEngine(item, validation),
    )


class VoiceCommandSafetyTests(unittest.TestCase):
    def test_valid_command_emits_versioned_confirmable_preview(self):
        registry = SimpleNamespace(
            skill_planner=_Planner(),
            task_runner=_TaskRunner(),
        )
        router = VoiceIntentRouter(
            registry,
            VoiceSession(),
            command_runtime=_runtime(_sequence_item()),
            source="test",
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

        self.assertTrue(preview.data["preview_id"])
        self.assertEqual(1, preview.data["version"])
        self.assertTrue(preview.data["validation"]["is_valid"])
        self.assertIs(True, preview.data["requires_confirmation"])
        self.assertEqual("low", preview.data["risk"]["level"])
        self.assertNotIn("command_started", [event.type for event in events])

    def test_invalid_action_never_becomes_a_preview(self):
        validation = ValidationResult.failed(
            ValidationCode.UNSUPPORTED_ACTION_TYPE,
            "unsupported action type",
        )
        registry = SimpleNamespace(
            skill_planner=_Planner(),
            task_runner=_TaskRunner(),
        )
        router = VoiceIntentRouter(
            registry,
            VoiceSession(),
            command_runtime=_runtime(_sequence_item(), validation),
            source="test",
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

    def test_websocket_rejects_preview_without_identity(self):
        server = RobotWebSocketServer(
            services=SimpleNamespace(commands=_runtime(_sequence_item()))
        )
        broadcasts: list[dict] = []

        async def record(payload: dict) -> None:
            broadcasts.append(payload)

        server._broadcast = record
        asyncio.run(server._emit_interaction_event({
            "type": "command_preview",
            "text": "preview",
            "data": {"sequence": [_sequence_item().to_dict()]},
        }))

        self.assertEqual("error", broadcasts[0]["event"])
        self.assertIn("ID", broadcasts[0]["message"])

    def test_websocket_confirm_requires_exact_reference(self):
        server = RobotWebSocketServer(
            services=SimpleNamespace(commands=_runtime(_sequence_item()))
        )
        websocket = _RecordingWebSocket()

        asyncio.run(server._handle_ai_confirm(websocket, {}))

        payload = json.loads(websocket.messages[0])
        self.assertEqual("invalid_preview_reference", payload["code"])

    def test_websocket_requires_high_risk_acknowledgement(self):
        runtime = _runtime(_sequence_item(ActionType.MOVE))
        preview = runtime.register(
            [_sequence_item(ActionType.MOVE)],
            source="websocket-ai",
            plan={},
            skill_info={},
            validation=ValidationResult.succeeded("valid"),
        )
        server = RobotWebSocketServer(
            services=SimpleNamespace(commands=runtime)
        )
        websocket = _RecordingWebSocket()

        asyncio.run(server._handle_ai_confirm(websocket, {
            "preview_id": preview.preview_id,
            "version": preview.version,
        }))

        payload = json.loads(websocket.messages[0])
        self.assertEqual(
            "risk_acknowledgement_required",
            payload["code"],
        )


if __name__ == "__main__":
    unittest.main()

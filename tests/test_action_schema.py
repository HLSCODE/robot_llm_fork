from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from src.domain.action_schema import (
    ActionParameterIssueCode,
    get_action_schema,
    validate_action_parameters,
)
from src.domain.models import ActionType
from src.robot_server.ws_server import RobotWebSocketServer


class ActionSchemaTests(unittest.TestCase):
    def test_schema_covers_every_action_type(self):
        self.assertEqual(
            {action_type.value for action_type in ActionType},
            set(get_action_schema()),
        )

    def test_schema_callers_receive_independent_copies(self):
        first = get_action_schema()
        first[ActionType.WAIT.value]["label"] = "mutated"

        second = get_action_schema()

        self.assertEqual("等待类", second[ActionType.WAIT.value]["label"])

    def test_variant_can_be_inferred_and_defaults_are_applied(self):
        result = validate_action_parameters(
            ActionType.MOVE,
            {
                "臂": "左",
                "模式": "move_j",
                "点位": "[0, 0, 0, 0, 0, 0]",
            },
        )

        self.assertTrue(result.is_valid)
        self.assertEqual("机械臂", result.parameters["目标"])

    def test_out_of_range_and_unknown_fields_are_rejected(self):
        result = validate_action_parameters(
            ActionType.MANIPULATE,
            {
                "执行器": "吸液枪",
                "操作": "吸",
                "容量": 10001,
                "legacy_value": 1,
            },
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(
            {
                ActionParameterIssueCode.OUT_OF_RANGE,
                ActionParameterIssueCode.UNKNOWN_FIELD,
            },
            {issue.code for issue in result.issues},
        )

    def test_removed_legacy_move_compensation_field_is_rejected(self):
        result = validate_action_parameters(
            ActionType.MOVE,
            {
                "目标": "机械臂",
                "臂": "左",
                "模式": "move_j",
                "点位": "[0, 0, 0, 0, 0, 0]",
                "定位补偿": {"enabled": True},
            },
        )

        self.assertFalse(result.is_valid)
        self.assertIn(
            ActionParameterIssueCode.UNKNOWN_FIELD,
            {issue.code for issue in result.issues},
        )

    def test_text_field_requires_string(self):
        result = validate_action_parameters(
            ActionType.INSPECT,
            {"Sensor_ID": 2},
        )

        self.assertFalse(result.is_valid)
        self.assertIs(
            ActionParameterIssueCode.INVALID_TYPE,
            result.issues[0].code,
        )

    def test_neck_schema_applies_defaults_and_rejects_out_of_range_pwm(self):
        valid = validate_action_parameters(
            ActionType.MANIPULATE,
            {"执行器": "颈部", "操作": "复位"},
        )
        invalid = validate_action_parameters(
            ActionType.MANIPULATE,
            {
                "执行器": "颈部",
                "操作": "水平移动",
                "水平PWM": 2501,
            },
        )

        self.assertTrue(valid.is_valid)
        self.assertEqual(1600, valid.parameters["水平PWM"])
        self.assertEqual(1000, valid.parameters["时长ms"])
        self.assertFalse(invalid.is_valid)
        self.assertIn(
            ActionParameterIssueCode.OUT_OF_RANGE,
            {issue.code for issue in invalid.issues},
        )


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


class ActionSchemaWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_returns_the_canonical_schema(self):
        server = RobotWebSocketServer(SimpleNamespace())
        websocket = _RecordingWebSocket()

        await server._composition_handler._handle_get_action_schema(websocket, {})

        payload = json.loads(websocket.messages[0])
        self.assertEqual("action_schema", payload["event"])
        self.assertEqual(get_action_schema(), payload["types"])
        self.assertIn(ActionType.BASE_MOVE.value, payload["types"])
        self.assertIn(ActionType.WAIT.value, payload["types"])


if __name__ == "__main__":
    unittest.main()

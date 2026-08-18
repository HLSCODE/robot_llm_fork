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

    def test_live_pose_capture_metadata_covers_only_pose_inputs(self):
        schemas = get_action_schema()
        actual: dict[tuple[str, str, str], dict[str, str]] = {}
        for action_type, action_schema in schemas.items():
            variants = action_schema.get("variants")
            if variants is None:
                field_groups = {"": action_schema.get("fields", {})}
            else:
                field_groups = {
                    variant_name: variant["fields"]
                    for variant_name, variant in variants.items()
                }
            for variant_name, fields in field_groups.items():
                for field_name, field_schema in fields.items():
                    source = field_schema.get("current_pose")
                    if source is not None:
                        actual[(action_type, variant_name, field_name)] = source

        self.assertEqual(
            {
                (ActionType.MOVE.value, "机械臂", "点位"): {
                    "arm_field": "臂",
                    "arm": "left",
                },
                (ActionType.MANIPULATE.value, "右臂转圈注液", "位姿"): {
                    "arm": "right",
                },
                (
                    ActionType.VISION_RELOCALIZE.value,
                    "teach",
                    "photo_pose",
                ): {
                    "arm_field": "arm",
                    "arm": "left",
                },
            },
            actual,
        )

    def test_visual_camera_uses_the_shared_dynamic_catalog(self):
        teach_fields = get_action_schema()[ActionType.VISION_RELOCALIZE.value][
            "variants"
        ]["teach"]["fields"]

        self.assertEqual("select", teach_fields["camera_name"]["type"])
        self.assertEqual("cameras", teach_fields["camera_name"]["options_source"])

    def test_variant_can_be_inferred_and_defaults_are_applied(self):
        result = validate_action_parameters(
            ActionType.MOVE,
            {
                "臂": "左",
                "模式": "move_j",
                "点位": [0, 0, 0, 0, 0, 0],
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
                "点位": [0, 0, 0, 0, 0, 0],
                "定位补偿": {"enabled": True},
            },
        )

        self.assertFalse(result.is_valid)
        self.assertIn(
            ActionParameterIssueCode.UNKNOWN_FIELD,
            {issue.code for issue in result.issues},
        )

    def test_move_compensation_schema_validates_supported_nested_modes(self):
        base = {
            "目标": "机械臂",
            "臂": "左",
            "模式": "move_j",
            "点位": [0, 0, 0, 0, 0, 0],
        }
        udp = validate_action_parameters(
            ActionType.MOVE,
            {
                **base,
                "补偿": {
                    "mode": "udp",
                    "udp": {
                        "teach_offset": {
                            "x": 1.0,
                            "y": 2.0,
                            "angle": 3.0,
                        }
                    },
                },
            },
        )
        vision = validate_action_parameters(
            ActionType.MOVE,
            {
                **base,
                "补偿": {
                    "mode": "vision",
                    "vision": {"station_id": "station-a", "arm": "left"},
                },
            },
        )
        invalid = validate_action_parameters(
            ActionType.MOVE,
            {**base, "补偿": {"mode": "vision", "vision": {}}},
        )

        self.assertTrue(udp.is_valid)
        self.assertTrue(vision.is_valid)
        self.assertFalse(invalid.is_valid)
        self.assertIn("选择视觉工位", invalid.message)

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

    def test_visual_relocalization_validates_mode_specific_fields(self):
        run = validate_action_parameters(
            ActionType.VISION_RELOCALIZE,
            {
                "action_mode": "run",
                "arm": "left",
                "station_id": "station-left",
                "move_mode": "move_j",
            },
        )
        teach = validate_action_parameters(
            ActionType.VISION_RELOCALIZE,
            {
                "action_mode": "teach",
                "arm": "right",
                "station_name": "右臂示教工位",
            },
        )
        missing_run_station = validate_action_parameters(
            ActionType.VISION_RELOCALIZE,
            {"action_mode": "run", "arm": "left"},
        )
        legacy_run_metadata = validate_action_parameters(
            ActionType.VISION_RELOCALIZE,
            {
                "action_mode": "run",
                "arm": "left",
                "station_id": "station-left",
                "station_name": "左臂示教工位",
            },
        )

        self.assertTrue(run.is_valid, run.message)
        self.assertTrue(teach.is_valid, teach.message)
        self.assertTrue(legacy_run_metadata.is_valid, legacy_run_metadata.message)
        self.assertEqual("teach", teach.parameters["action_mode"])
        self.assertIn("station_id", missing_run_station.message)


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

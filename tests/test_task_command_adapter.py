import json
import unittest
from pathlib import Path

from src.core.models import LoopBlock, SequenceItem
from src.core.pose_compensation import parse_pose
from src.robot_server.task_command_adapter import (
    ALLOWED_COMMANDS,
    DEFAULT_POINTS_FILE,
    TaskCommandAdapter,
    TaskCommandError,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "data" / "tasks"


def sequence_items(entries):
    for entry in entries:
        if isinstance(entry, LoopBlock):
            yield from entry.items
        elif isinstance(entry, SequenceItem):
            yield entry


class TaskCommandAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = TaskCommandAdapter()
        cls.original_task_bytes = {
            command: (TASKS_DIR / f"{command}.task").read_bytes()
            for command in ALLOWED_COMMANDS
        }
        cls.points = json.loads(DEFAULT_POINTS_FILE.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        for command, original in cls.original_task_bytes.items():
            current = (TASKS_DIR / f"{command}.task").read_bytes()
            if current != original:
                raise AssertionError(f"{command}.task was modified by adapter tests")

    def test_all_allowed_commands_load_with_optional_suffix(self):
        for command in ALLOWED_COMMANDS:
            with self.subTest(command=command):
                prepared = self.adapter.prepare({"command_type": f"{command}.task"})
                self.assertEqual(command, prepared.command_type)
                self.assertTrue(prepared.entries)
                self.assertGreater(prepared.total_steps, 0)

    def test_unknown_and_path_commands_are_rejected(self):
        for command in ("other", "../730-peiye", r"..\730-peiye", "730-peiye.json"):
            with self.subTest(command=command):
                with self.assertRaises(TaskCommandError) as raised:
                    self.adapter.prepare({"command_type": command})
                self.assertEqual("UNKNOWN_COMMAND", raised.exception.code)

    def test_prepare_solution_updates_every_xi200_only(self):
        prepared = self.adapter.prepare(
            {
                "command_type": "730-peiye",
                "aspirate_volume_ml": 200.5,
                "station_id": 4,
                "height_level": "lower",
                "method": "circular",
                "flow_rate_ml_min": 999,
                "volume_ml": 888,
            }
        )
        items = list(sequence_items(prepared.entries))
        targets = [item for item in items if item.definition.name == "XI200"]
        self.assertEqual(2, len(targets))
        self.assertEqual([201, 201], [item.definition.parameters["容量"] for item in targets])
        self.assertEqual(
            [1000, 1000],
            [
                item.definition.parameters["容量"]
                for item in items
                if item.definition.name == "tuye"
            ],
        )

    def test_prepare_solution_missing_volume_keeps_template_defaults(self):
        prepared = self.adapter.prepare({"command_type": "730-peiye"})
        targets = [
            item
            for item in sequence_items(prepared.entries)
            if item.definition.name == "XI200"
        ]
        self.assertEqual([200, 200], [item.definition.parameters["容量"] for item in targets])

    def test_dispense_all_station_height_method_combinations(self):
        height_keys = ("upper", "middle", "lower")
        methods = ("vertical", "circular")
        for station_id in range(1, 5):
            for height_level in height_keys:
                for method in methods:
                    with self.subTest(
                        station_id=station_id,
                        height_level=height_level,
                        method=method,
                    ):
                        prepared = self.adapter.prepare(
                            {
                                "command_type": "730-zhuye",
                                "station_id": station_id,
                                "height_level": height_level,
                                "method": method,
                                "flow_rate_ml_min": 700.5,
                                "volume_ml": 400.5,
                                "aspirate_volume_ml": 1234,
                            }
                        )
                        items = list(sequence_items(prepared.entries))
                        shang = [
                            item
                            for item in items
                            if item.definition.name == "730-1-shang"
                        ]
                        high = [
                            item
                            for item in items
                            if item.definition.name == "730-1-high"
                        ]
                        self.assertEqual(2, len(shang))
                        self.assertEqual(1, len(high))
                        expected_shang = self.points[str(station_id)]["shang"]
                        expected_height = self.points[str(station_id)][height_level]
                        for item in shang:
                            self.assertEqual(expected_shang, parse_pose(item.definition.parameters["点位"]))
                        self.assertEqual(
                            expected_height,
                            parse_pose(high[0].definition.parameters["点位"]),
                        )

                        if method == "vertical":
                            dispense = [
                                item for item in items if item.definition.name == "tuye"
                            ]
                            self.assertEqual(1, len(dispense))
                            params = dispense[0].definition.parameters
                            self.assertEqual(701, params["吐液速度"])
                            self.assertEqual(401, params["容量"])
                        else:
                            dispense = [
                                item
                                for item in items
                                if item.definition.name == "zhuanquanzhuye"
                            ]
                            self.assertEqual(1, len(dispense))
                            definition = dispense[0].definition
                            params = definition.parameters
                            self.assertEqual(
                                "f8a20c9c-ba91-4088-b61c-e12a77658df3",
                                definition.id,
                            )
                            self.assertEqual(expected_height, parse_pose(params["位姿"]))
                            self.assertEqual(701, params["吐液速度"])
                            self.assertEqual(401, params["吐液量"])
                            self.assertEqual(5.0, params["半径R"])
                            self.assertEqual(1.0, params["圈数"])
                            self.assertEqual(72, params["分段数"])
                            self.assertEqual(10, params["过渡半径"])
                            self.assertEqual(10, params["运动速度"])
                            self.assertIs(True, params["连续运动"])
                            self.assertIs(False, params["顺时针"])

    def test_dispense_defaults_to_station_one_upper_vertical(self):
        prepared = self.adapter.prepare({"command_type": "730-zhuye"})
        items = list(sequence_items(prepared.entries))
        high = next(item for item in items if item.definition.name == "730-1-high")
        dispense = next(item for item in items if item.definition.name == "tuye")
        self.assertEqual(self.points["1"]["upper"], parse_pose(high.definition.parameters["点位"]))
        self.assertEqual(800, dispense.definition.parameters["吐液速度"])
        self.assertEqual(500, dispense.definition.parameters["容量"])

    def test_invalid_present_parameters_do_not_fall_back(self):
        invalid_cases = (
            ({"command_type": "730-zhuye", "station_id": 5}, "INVALID_ARGUMENT"),
            ({"command_type": "730-zhuye", "height_level": "highest"}, "INVALID_ARGUMENT"),
            ({"command_type": "730-zhuye", "method": "wall_side"}, "INVALID_ARGUMENT"),
            ({"command_type": "730-zhuye", "flow_rate_ml_min": 0}, "INVALID_ARGUMENT"),
            ({"command_type": "730-zhuye", "flow_rate_ml_min": 10000}, "INVALID_ARGUMENT"),
            ({"command_type": "730-zhuye", "volume_ml": 65536}, "INVALID_ARGUMENT"),
            ({"command_type": "730-peiye", "aspirate_volume_ml": None}, "INVALID_ARGUMENT"),
        )
        for payload, code in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(TaskCommandError) as raised:
                    self.adapter.prepare(payload)
                self.assertEqual(code, raised.exception.code)

    def test_no_task_template_is_written(self):
        for command in ALLOWED_COMMANDS:
            self.assertEqual(
                self.original_task_bytes[command],
                (TASKS_DIR / f"{command}.task").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()

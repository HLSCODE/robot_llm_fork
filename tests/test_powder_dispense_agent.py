import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agents import powder_dispense_logger
from src.agents.powder_dispense_agent import PowderDispenseAgent, PowderDispenseConfig


class FakePowderController:
    def __init__(self):
        self.actions = []

    def enable_all(self):
        self.actions.append("enable_all")

    def lift_to_dispense(self, position):
        self.actions.append(("lift_to_dispense", position))

    def lift_to_safe(self, position):
        self.actions.append(("lift_to_safe", position))

    def rotation_move_relative(self, delta_steps):
        self.actions.append(("rotation_move_relative", delta_steps))

    def rotation_to_home(self, position):
        self.actions.append(("rotation_to_home", position))

    def rotation_stop(self):
        self.actions.append("rotation_stop")

    def close(self):
        self.actions.append("close")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class PowderDispenseAgentLogTest(unittest.TestCase):
    def test_powder_dispense_log_records_rounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "powder_dispense.jsonl"
            readings = iter([1.0, 1.002, 1.004])
            controller = FakePowderController()

            with patch.object(powder_dispense_logger, "DEFAULT_LOG_PATH", log_path):
                agent = PowderDispenseAgent(lambda: controller, lambda: next(readings))
                result = agent.run(
                    PowderDispenseConfig(target_mg=5, tolerance_mg=1, max_rounds=5, settle_seconds=0),
                    context={"task_name": "powder_a.task", "action_name": "加粉100mg"},
                )

            records = _read_jsonl(log_path)
            self.assertTrue(result.success)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["task_name"], "powder_a.task")
            self.assertEqual(records[0]["action_name"], "加粉100mg")
            self.assertEqual(records[0]["message"], "达到目标")
            self.assertEqual(records[0]["rounds"], 2)
            deltas = [round_item["delta_mg"] for round_item in records[0]["round_records"]]
            self.assertAlmostEqual(deltas[0], 2.0)
            self.assertAlmostEqual(deltas[1], 2.0)

    def test_powder_dispense_max_rounds_is_logged_as_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "powder_dispense.jsonl"
            readings = iter([1.0, 1.001, 1.002])

            with patch.object(powder_dispense_logger, "DEFAULT_LOG_PATH", log_path):
                agent = PowderDispenseAgent(lambda: FakePowderController(), lambda: next(readings))
                result = agent.run(PowderDispenseConfig(target_mg=100, max_rounds=2, settle_seconds=0))

            record = _read_jsonl(log_path)[0]
            self.assertTrue(result.success)
            self.assertTrue(record["success"])
            self.assertEqual(record["message"], "达到最大轮次，继续后续流程")
            self.assertEqual(record["rounds"], 2)

    def test_powder_dispense_read_failure_still_writes_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "powder_dispense.jsonl"

            def read_balance():
                raise RuntimeError("balance offline")

            with patch.object(powder_dispense_logger, "DEFAULT_LOG_PATH", log_path):
                agent = PowderDispenseAgent(lambda: FakePowderController(), read_balance)
                with self.assertRaises(RuntimeError):
                    agent.run(PowderDispenseConfig(max_read_failures=1, settle_seconds=0))

            record = _read_jsonl(log_path)[0]
            self.assertFalse(record["success"])
            self.assertIn("balance offline", record["message"])


if __name__ == "__main__":
    unittest.main()

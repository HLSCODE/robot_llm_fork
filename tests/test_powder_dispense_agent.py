from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.agents.powder_dispense_agent import (
    PowderDispenseAgent,
    PowderDispenseConfig,
    PowderDispenseOutcome,
    choose_rotation_step,
    config_from_params,
)


POLICY_CASES_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "regression"
    / "powder_dispense_policy_cases.json"
)


class _PowderController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def enable_all(self) -> None:
        self.calls.append(("enable", None))

    def lift_to_dispense(self, position: int) -> None:
        self.calls.append(("lift_dispense", position))

    def lift_to_safe(self, position: int) -> None:
        self.calls.append(("lift_safe", position))

    def rotation_move_relative(self, delta_steps: int) -> None:
        self.calls.append(("rotation_move", delta_steps))

    def rotation_to_home(self, position: int) -> None:
        self.calls.append(("rotation_home", position))

    def rotation_stop(self) -> None:
        self.calls.append(("rotation_stop", None))


class PowderDispenseAgentTests(unittest.TestCase):
    def test_versioned_offline_policy_regression_cases(self):
        document = json.loads(POLICY_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual({"schema_version", "cases"}, set(document))
        self.assertEqual(1, document["schema_version"])
        self.assertGreaterEqual(len(document["cases"]), 5)

        for case in document["cases"]:
            with self.subTest(case=case["name"]):
                controller = _PowderController()
                readings = iter(case["readings_g"])
                agent = PowderDispenseAgent(
                    controller,
                    lambda: next(readings),
                    sleep=lambda _seconds: None,
                )

                result = agent.run(
                    PowderDispenseConfig(
                        target_mg=case["target_mg"],
                        tolerance_mg=case["tolerance_mg"],
                        max_rounds=case["max_rounds"],
                        settle_seconds=0,
                    )
                )

                self.assertEqual(case["expected_outcome"], result.outcome.value)
                self.assertEqual(case["expected_rounds"], result.rounds)
                self.assertEqual(
                    case["expected_rotation_steps"],
                    [
                        value
                        for operation, value in controller.calls
                        if operation == "rotation_move"
                    ],
                )
                self.assertEqual(result.rounds, len(result.round_records))

    def test_step_policy_boundaries_are_explicit_and_configurable(self):
        config = PowderDispenseConfig(
            large_step=40,
            medium_step=30,
            small_step=20,
            micro_step=10,
            large_step_threshold_mg=25,
            medium_step_threshold_mg=10,
            small_step_threshold_mg=3,
        )

        self.assertEqual(40, choose_rotation_step(25.01, config))
        self.assertEqual(30, choose_rotation_step(25.0, config))
        self.assertEqual(20, choose_rotation_step(10.0, config))
        self.assertEqual(10, choose_rotation_step(3.0, config))

    def test_parameter_config_overrides_device_threshold_defaults(self):
        config = config_from_params(
            {"大步阈值mg": 30},
            {
                "powder_large_step_threshold_mg": 25,
                "powder_medium_step_threshold_mg": 12,
                "powder_small_step_threshold_mg": 4,
            },
        )

        self.assertEqual(30, config.large_step_threshold_mg)
        self.assertEqual(12, config.medium_step_threshold_mg)
        self.assertEqual(4, config.small_step_threshold_mg)

    def test_invalid_policy_configuration_is_rejected_before_device_io(self):
        invalid_configs = (
            {"target_mg": 0},
            {"target_mg": float("inf")},
            {"settle_seconds": -0.1},
            {"max_read_failures": 0},
            {"large_step": 0},
            {
                "large_step_threshold_mg": 10,
                "medium_step_threshold_mg": 10,
            },
        )

        for values in invalid_configs:
            with self.subTest(values=values), self.assertRaises(ValueError):
                PowderDispenseConfig(**values)

    def test_balance_retry_preserves_cause_and_skips_delay_after_last_failure(self):
        controller = _PowderController()
        sleep_calls: list[float] = []

        def fail_reading() -> float:
            raise OSError("balance unavailable")

        agent = PowderDispenseAgent(
            controller,
            fail_reading,
            sleep=sleep_calls.append,
        )

        with self.assertRaises(RuntimeError) as raised:
            agent.run(
                PowderDispenseConfig(
                    max_read_failures=3,
                    read_retry_delay_seconds=0.25,
                )
            )

        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertEqual([0.25, 0.25], sleep_calls)
        self.assertEqual(
            [
                ("enable", None),
                ("rotation_stop", None),
                ("lift_safe", 0),
                ("rotation_home", 0),
            ],
            controller.calls,
        )

    def test_max_rounds_is_explicit_failure_and_returns_device_safe(self):
        controller = _PowderController()
        readings = iter((1.0, 1.001))
        agent = PowderDispenseAgent(
            controller,
            lambda: next(readings),
            sleep=lambda _seconds: None,
        )

        result = agent.run(
            PowderDispenseConfig(
                target_mg=100,
                tolerance_mg=1,
                max_rounds=1,
                settle_seconds=0,
                lift_safe_position=1,
                lift_dispense_position=2,
                rotation_home_position=3,
            )
        )

        self.assertIs(
            PowderDispenseOutcome.MAX_ROUNDS_REACHED,
            result.outcome,
        )
        self.assertFalse(result.successful)
        self.assertIn("未达到目标", result.message)
        self.assertIn(("rotation_stop", None), controller.calls)
        self.assertIn(("lift_safe", 1), controller.calls)
        self.assertIn(("rotation_home", 3), controller.calls)
        self.assertEqual(1, len(result.round_records))
        record = result.round_records[0]
        self.assertEqual(1, record.round_number)
        self.assertEqual(20000, record.rotation_steps)
        self.assertAlmostEqual(1.0, record.round_delta_mg)
        self.assertAlmostEqual(100.0, record.remaining_before_mg)


if __name__ == "__main__":
    unittest.main()

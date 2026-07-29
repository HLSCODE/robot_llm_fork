from __future__ import annotations

import unittest

from src.agents.powder_dispense_agent import (
    PowderDispenseAgent,
    PowderDispenseConfig,
    PowderDispenseOutcome,
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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import math
import unittest

import numpy as np

from src.vision.relocalization.geometry import (
    apply_planar_compensation,
    compensate_taught_pose,
    pose_to_transform,
)


class PlanarCompensationTests(unittest.TestCase):
    def test_base_turn_preserves_tool_tilt_and_marker_relative_position(self) -> None:
        teach_marker = pose_to_transform([1.0, 2.0, 0.7, 0.0, 0.0, 0.3])
        for roll, pitch, yaw in ((math.pi / 2, 0.2, -0.4), (math.pi, 0.0, 0.35)):
            taught = pose_to_transform([1.4, 1.8, 0.6, roll, pitch, yaw])
            for turn in (-0.2, 0.2, math.pi / 2):
                with self.subTest(roll=roll, pitch=pitch, turn=turn):
                    current_marker = pose_to_transform(
                        [3.0, 4.0, 1.2, 0.0, 0.0, 0.3 + turn]
                    )

                    actual, _ = apply_planar_compensation(
                        teach_marker, current_marker, taught
                    )

                    # The horizontal offset from the marker rotates with the base
                    # compensation; height and tool roll/pitch remain unchanged.
                    c, s = math.cos(turn), math.sin(turn)
                    expected = pose_to_transform(
                        [
                            3.0 + 0.4 * c + 0.2 * s,
                            4.0 + 0.4 * s - 0.2 * c,
                            0.6,
                            roll,
                            pitch,
                            yaw + turn,
                        ]
                    )
                    np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)

    def test_rpy_output_uses_configured_angle_unit_after_base_turn(self) -> None:
        teach_marker = pose_to_transform([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        current_marker = pose_to_transform([0.0, 0.0, 0.0, 0.0, 0.0, math.pi / 2])
        for unit, factor in (("rad", 1.0), ("deg", 180.0 / math.pi)):
            with self.subTest(unit=unit):
                taught = [0.4, 0.2, 0.6, math.pi / 2 * factor, 0.2 * factor, 0.0]

                actual = compensate_taught_pose(
                    taught,
                    teach_marker,
                    current_marker,
                    {"pose_rotation_type": "rpy", "pose_angle_unit": unit},
                )

                expected = [
                    -0.2, 0.4, 0.6, math.pi / 2 * factor, 0.2 * factor, math.pi / 2 * factor
                ]
                np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)

    def test_translation_constraints_preserve_tool_orientation_and_height(self) -> None:
        teach_marker = pose_to_transform([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        current_marker = pose_to_transform([0.2, -0.3, 0.5, 0.0, 0.0, 0.0])
        taught = pose_to_transform([0.4, 0.2, 0.6, math.pi / 2, 0.2, -0.4])
        for constraint, expected_xyz in (
            ("none", [0.6, -0.1, 0.6]),
            ("translation-only", [0.6, -0.1, 0.6]),
            ("x-only", [0.6, 0.2, 0.6]),
            ("y-only", [0.4, -0.1, 0.6]),
        ):
            with self.subTest(constraint=constraint):
                actual, _ = apply_planar_compensation(
                    teach_marker, current_marker, taught, planar_constraint=constraint
                )

                np.testing.assert_allclose(actual[:3, 3], expected_xyz, atol=1e-12, rtol=0)
                np.testing.assert_allclose(actual[:3, :3], taught[:3, :3], atol=1e-12, rtol=0)


if __name__ == "__main__":
    unittest.main()

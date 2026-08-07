from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import patch

import numpy as np

from src.configuration.settings import VisionSettings
from src.devices import DepthCameraFrame, DepthCameraSource, RobotSystem
from src.vision.pipelines.capture import execute_vision_capture
from src.vision.pipelines.grasp import VisionCaptureAction
from src.vision.pipelines.vertical import vertical_catch_main


def _depth_frame() -> DepthCameraFrame:
    color = np.zeros((12, 16, 3), dtype=np.uint8)
    color[4, 6] = (10, 20, 30)
    depth = np.full((12, 16), 750, dtype=np.uint16)
    intrinsics = np.array(
        [
            [400.0, 0.0, 8.0],
            [0.0, 410.0, 6.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return DepthCameraFrame(
        camera_name="fixture-camera",
        camera_serial="fixture-serial",
        color_bgr=color,
        depth_uint16=depth,
        intrinsics=intrinsics,
        distortion_coefficients=np.zeros(5, dtype=np.float64),
        distortion_model="none",
        depth_scale_metres=0.001,
        color_hardware_timestamp_ms=10.0,
        depth_hardware_timestamp_ms=10.0,
        color_frame_number=1,
        depth_frame_number=1,
        hardware_timestamp_domain="fixture",
        received_at_utc_ns=1,
        received_at_monotonic_ns=1,
        depth_aligned_to_color=True,
    )


class _DepthCamera:
    def __init__(self, frame: DepthCameraFrame) -> None:
        self.frame = frame
        self.requested_camera_name: str | None = None

    def get_latest_depth_frame(
        self,
        camera_name: str | None = None,
    ) -> DepthCameraFrame:
        self.requested_camera_name = camera_name
        return self.frame


class VisionPipelineRegressionTests(unittest.TestCase):
    def test_capture_action_adapts_depth_camera_frame(self) -> None:
        frame = _depth_frame()
        camera = _DepthCamera(frame)
        action = VisionCaptureAction(
            robot_system=cast(RobotSystem, object()),
            camera=cast(DepthCameraSource, camera),
            settings=VisionSettings(vision_camera_name="fixture-camera"),
        )

        color, depth, intrinsics = action._fetch_frames()

        self.assertIs(frame.color_bgr, color)
        self.assertIs(frame.depth_uint16, depth)
        self.assertEqual("fixture-camera", camera.requested_camera_name)
        self.assertEqual(
            {"fx": 400.0, "fy": 410.0, "ppx": 8.0, "ppy": 6.0},
            intrinsics,
        )

    def test_capture_parses_false_string_without_enabling_debug_images(self) -> None:
        received: dict[str, object] = {}

        class SuccessfulCaptureAction:
            last_error: str | None = None

            def __init__(self, **options: object) -> None:
                received.update(options)

            def execute(self) -> bool:
                return True

        with patch(
            "src.vision.pipelines.grasp.VisionCaptureAction",
            SuccessfulCaptureAction,
        ):
            result = execute_vision_capture(
                cast(RobotSystem, object()),
                cast(DepthCameraSource, object()),
                {"调试图片": "false"},
                VisionSettings(),
                lambda _message: None,
                ".",
            )

        self.assertTrue(result.successful)
        self.assertIs(False, received["save_debug_images"])

    def test_vertical_catch_rejects_mask_without_valid_depth(self) -> None:
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[3:11, 4:12] = 255
        depth = np.zeros((16, 16), dtype=np.uint16)

        with self.assertRaisesRegex(
            ValueError,
            "mask does not contain valid depth values",
        ):
            vertical_catch_main(
                mask,
                depth,
                {"fx": 100.0, "fy": 100.0, "ppx": 8.0, "ppy": 8.0},
                [0.0] * 6,
                0.0,
                [0.0] * 3,
                np.eye(3).tolist(),
                [0.0] * 3,
            )

    def test_vertical_catch_rounds_fractional_center_before_depth_indexing(self) -> None:
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[3:9, 2:8] = 255
        depth = np.zeros((16, 16), dtype=np.uint16)
        depth[6, 4] = 1_000

        poses = vertical_catch_main(
            mask,
            depth,
            {"fx": 100.0, "fy": 100.0, "ppx": 4.0, "ppy": 6.0},
            [0.0] * 6,
            0.0,
            [0.0] * 3,
            np.eye(3).tolist(),
            [0.0] * 3,
            use_point_depth_or_mean=False,
        )

        self.assertEqual(3, len(poses))
        self.assertTrue(all(np.isfinite(value) for pose in poses for value in pose))


if __name__ == "__main__":
    unittest.main()

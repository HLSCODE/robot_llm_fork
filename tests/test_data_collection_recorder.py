from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from src.data_collection.config import DataCollectionConfig
from src.data_collection.episode_writer import DataCollectionStoragePolicy
from src.data_collection.recorder import DemonstrationRecorder
from src.data_collection.schema import DataCollectionFormat
from src.device_runtime import (
    ArmId,
    ArmState,
    ArmTelemetry,
    CartesianPose,
    DepthCameraFrame,
    GripperTelemetry,
    JointVector,
)


class _Camera:
    is_running = True
    camera_count = 1

    def __init__(self, frame: DepthCameraFrame) -> None:
        self.frame = frame

    def get_cameras_info(self):
        return [{"name": self.frame.camera_name, "serial": self.frame.camera_serial}]

    def get_latest_depth_frame(self, _camera_name=None):
        return self.frame.detached_copy()


class _TelemetryReader:
    def __init__(self, values: dict[ArmId, ArmTelemetry]) -> None:
        self.values = values

    def try_read_arm_telemetry(self, arm: ArmId):
        return self.values.get(arm)


class _UnusedWriter:
    pass


class DemonstrationRecorderTests(unittest.TestCase):
    def test_dual_arm_capture_preserves_bounded_real_samples(self):
        camera = _camera_frame(monotonic_ns=1_000_000_000)
        reader = _TelemetryReader(
            {
                ArmId.LEFT: _arm_telemetry(
                    ArmId.LEFT,
                    monotonic_ns=1_002_000_000,
                    gripper_position=0.25,
                ),
                ArmId.RIGHT: _arm_telemetry(
                    ArmId.RIGHT,
                    monotonic_ns=1_004_000_000,
                    gripper_position=0.75,
                ),
            }
        )
        recorder = DemonstrationRecorder(
            reader,
            _Camera(camera),
            _config(maximum_sync_skew_ms=5.0),
            writer=_UnusedWriter(),
        )

        frame = recorder._get_current_frame()

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual({"left", "right"}, set(frame.arms))
        self.assertEqual(4.0, frame.sample_sync_skew_ms)
        self.assertEqual(0.25, frame.arms["left"].gripper_open)
        self.assertEqual(0.75, frame.arms["right"].gripper_open)
        np.testing.assert_allclose(
            np.arange(7, dtype=np.float64) / 10.0,
            frame.arms["left"].joint_currents,
        )

    def test_capture_rejects_samples_outside_sync_bound(self):
        camera = _camera_frame(monotonic_ns=1_000_000_000)
        reader = _TelemetryReader(
            {
                ArmId.LEFT: _arm_telemetry(
                    ArmId.LEFT,
                    monotonic_ns=1_020_000_000,
                    gripper_position=0.25,
                ),
                ArmId.RIGHT: _arm_telemetry(
                    ArmId.RIGHT,
                    monotonic_ns=1_002_000_000,
                    gripper_position=0.75,
                ),
            }
        )
        recorder = DemonstrationRecorder(
            reader,
            _Camera(camera),
            _config(maximum_sync_skew_ms=5.0),
            writer=_UnusedWriter(),
        )

        self.assertIsNone(recorder._get_current_frame())
        self.assertEqual(1, recorder._capture_error_count)


def _config(*, maximum_sync_skew_ms: float) -> DataCollectionConfig:
    return DataCollectionConfig(
        fps=30,
        camera_index=0,
        arm_ids=(ArmId.LEFT, ArmId.RIGHT),
        save_path=Path("data/demos"),
        format_variant=DataCollectionFormat.PORTABLE_SIMPLIFIED,
        storage_policy=DataCollectionStoragePolicy(
            minimum_free_bytes=0,
            overhead_factor=1.0,
            stale_write_seconds=60.0,
        ),
        random_seed=42,
        recording_stop_timeout_seconds=1.0,
        maximum_sync_skew_ms=maximum_sync_skew_ms,
        camera_extrinsics=None,
        camera_extrinsics_reference_frame=None,
        calibration_id=None,
    )


def _camera_frame(*, monotonic_ns: int) -> DepthCameraFrame:
    return DepthCameraFrame(
        camera_name="front",
        camera_serial="camera-1",
        color_bgr=np.zeros((4, 5, 3), dtype=np.uint8),
        depth_uint16=np.ones((4, 5), dtype=np.uint16),
        intrinsics=np.asarray([[100.0, 0.0, 2.0], [0.0, 100.0, 2.0], [0.0, 0.0, 1.0]]),
        distortion_coefficients=np.zeros(5, dtype=np.float64),
        distortion_model="brown_conrady",
        depth_scale_metres=0.001,
        color_hardware_timestamp_ms=10.0,
        depth_hardware_timestamp_ms=10.5,
        color_frame_number=1,
        depth_frame_number=1,
        hardware_timestamp_domain="hardware_clock",
        received_at_utc_ns=2_000_000_000,
        received_at_monotonic_ns=monotonic_ns,
        depth_aligned_to_color=True,
    )


def _arm_telemetry(
    arm: ArmId,
    *,
    monotonic_ns: int,
    gripper_position: float,
) -> ArmTelemetry:
    return ArmTelemetry(
        state=ArmState(
            arm=arm,
            pose=CartesianPose(0.1, 0.2, 0.3, 0.0, 0.0, 0.0),
            joints=JointVector(tuple(float(index) for index in range(7))),
        ),
        sampled_at_utc_ns=2_000_000_000,
        sampled_at_monotonic_ns=monotonic_ns,
        gripper=GripperTelemetry(
            position_normalized=gripper_position,
            force_newtons=2.5,
            raw_position=int(gripper_position * 1000),
        ),
        joint_velocities_deg_s=tuple(0.0 for _ in range(7)),
        joint_currents_amperes=tuple(index / 10.0 for index in range(7)),
        end_effector_wrench=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
    )


if __name__ == "__main__":
    unittest.main()

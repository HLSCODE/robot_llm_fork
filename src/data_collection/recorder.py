from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from ..device_runtime import (
    ArmId,
    ArmTelemetry,
    ArmTelemetryReader,
    DepthCameraFrame,
    DepthCameraSource,
)
from .config import DataCollectionConfig
from .episode_writer import (
    DataCollectionEpisodeWriter,
    EpisodeAlreadyExistsError,
    EpisodeFormatUnavailableError,
    EpisodeIntegrityError,
    EpisodeWriteError,
    InsufficientStorageError,
)

logger = logging.getLogger(__name__)


class RecorderStopTimeoutError(TimeoutError):
    pass


@dataclass(slots=True)
class ArmFrameData:
    sampled_at_utc_ns: int
    sampled_at_monotonic_ns: int
    joint_positions: np.ndarray
    gripper_open: float
    gripper_force_newtons: float
    gripper_raw_position: int
    gripper_pose: np.ndarray
    joint_velocities: np.ndarray | None = None
    joint_currents: np.ndarray | None = None
    end_effector_wrench: np.ndarray | None = None


@dataclass(slots=True)
class FrameData:
    timestamp_utc_ns: int
    camera_received_at_monotonic_ns: int
    front_rgb: np.ndarray
    front_depth: np.ndarray
    camera_intrinsics: np.ndarray
    camera_distortion_coefficients: np.ndarray
    depth_scale_metres: float
    color_hardware_timestamp_ms: float
    depth_hardware_timestamp_ms: float
    color_frame_number: int
    depth_frame_number: int
    sample_sync_skew_ms: float
    camera_name: str
    camera_serial: str
    camera_distortion_model: str
    camera_hardware_timestamp_domain: str
    depth_aligned_to_color: bool
    arms: dict[str, ArmFrameData] = field(default_factory=dict)


class DemonstrationRecorder:
    """Capture bounded-skew camera and arm telemetry frames."""

    def __init__(
        self,
        robot_telemetry_reader: ArmTelemetryReader,
        camera_source: DepthCameraSource,
        config: DataCollectionConfig,
        *,
        writer: DataCollectionEpisodeWriter | None = None,
    ) -> None:
        self._robot_telemetry_reader = robot_telemetry_reader
        self._camera_source = camera_source
        self._config = config
        self._writer = writer or DataCollectionEpisodeWriter(
            config.save_path,
            format_variant=config.format_variant,
            storage_policy=config.storage_policy,
            random_seed=config.random_seed,
            source_arms=tuple(arm.value for arm in config.arm_ids),
            maximum_sync_skew_ms=config.maximum_sync_skew_ms,
            camera_extrinsics=config.camera_extrinsics,
            camera_extrinsics_reference_frame=(
                config.camera_extrinsics_reference_frame
            ),
            calibration_id=config.calibration_id,
        )

        self._collect_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_frames: list[FrameData] = []
        self._frames_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._session_active = False
        self._task_name: str | None = None
        self._description = ""
        self._next_episode_id = 0
        self._capture_error_count = 0

    def start_session(self, task: str, description: str) -> dict[str, Any]:
        with self._state_lock:
            if self._session_active:
                return {"success": False, "message": "数据采集会话已经启动"}
        try:
            status = self._writer.prepare_session(task)
        except Exception as exc:  # noqa: BLE001 - application boundary normalization
            return {
                "success": False,
                "error_code": _persistence_error_code(exc),
                "message": f"数据采集存储预检失败: {exc}",
            }
        with self._state_lock:
            self._session_active = True
            self._task_name = status.task
            self._description = description
            self._next_episode_id = status.next_episode_id
        logger.info(
            "data collection session started: task=%s next_episode_id=%d "
            "format=%s arms=%s free_bytes=%d recovered=%d",
            status.task,
            status.next_episode_id,
            status.format_variant.value,
            ",".join(arm.value for arm in self._config.arm_ids),
            status.free_bytes,
            len(status.recovered_paths),
        )
        return {
            "success": True,
            "next_episode_id": status.next_episode_id,
            "message": f"会话已启动，下一个 episode 编号为 {status.next_episode_id}",
        }

    def end_session(self) -> dict[str, Any]:
        with self._state_lock:
            if self._collect_thread is not None:
                return {
                    "success": False,
                    "message": "episode 仍在记录，无法结束会话",
                }
            self._session_active = False
            self._task_name = None
            self._description = ""
        return {"success": True, "message": "会话已结束"}

    def start_recording(self) -> dict[str, Any]:
        with self._state_lock:
            if not self._session_active or self._task_name is None:
                return {
                    "success": False,
                    "message": "会话未启动，请先启动数据采集会话",
                }
            if self._collect_thread is not None:
                return {"success": False, "message": "episode 已在记录"}
            episode_id = self._next_episode_id
            with self._frames_lock:
                self._current_frames.clear()
            self._capture_error_count = 0
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._collect_loop,
                daemon=True,
                name="DataCollectionRecorder",
            )
            self._collect_thread = thread
            thread.start()
        logger.info("started recording episode %d", episode_id)
        return {
            "success": True,
            "episode_id": episode_id,
            "message": f"episode {episode_id} 开始记录",
        }

    def stop_recording(self) -> dict[str, Any]:
        with self._state_lock:
            thread = self._collect_thread
            episode_id = self._next_episode_id
            task_name = self._task_name
            description = self._description
            if thread is None or task_name is None:
                return {"success": False, "message": "当前未在记录"}
            self._stop_event.set()

        thread.join(timeout=self._config.recording_stop_timeout_seconds)
        if thread.is_alive():
            raise RecorderStopTimeoutError(
                "data collection thread did not stop within "
                f"{self._config.recording_stop_timeout_seconds:g} seconds"
            )
        with self._state_lock:
            self._collect_thread = None
        with self._frames_lock:
            frames = copy.deepcopy(self._current_frames)
        frame_count = len(frames)

        try:
            result = self._writer.save_episode(
                task=task_name,
                episode_id=episode_id,
                frames=frames,
                description=description,
                capture_error_count=self._capture_error_count,
            )
        except Exception as exc:  # noqa: BLE001 - persistence boundary normalization
            logger.error("failed to save episode %d: %s", episode_id, exc)
            return {
                "success": False,
                "error_code": _persistence_error_code(exc),
                "episode_id": episode_id,
                "frames": frame_count,
                "message": f"保存失败: {exc}",
            }

        with self._state_lock:
            self._next_episode_id = episode_id + 1
        logger.info(
            "saved episode %d with %d frames to %s",
            episode_id,
            frame_count,
            result.episode_path,
        )
        return {
            "success": True,
            "episode_id": episode_id,
            "frames": frame_count,
            "message": (
                f"episode {episode_id} 已保存，共 {frame_count} 帧，"
                f"格式 {result.metadata.format_variant.value}"
            ),
        }

    def _collect_loop(self) -> None:
        interval_seconds = 1.0 / self._config.fps
        logger.info("data collection thread started at %d Hz", self._config.fps)
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            frame = self._get_current_frame()
            if frame is not None:
                with self._frames_lock:
                    self._current_frames.append(frame)
            elapsed_seconds = time.monotonic() - started_at
            self._stop_event.wait(max(0.0, interval_seconds - elapsed_seconds))
        logger.info("data collection thread stopped")

    def _get_current_frame(self) -> FrameData | None:
        try:
            camera = self._capture_camera()
            arm_samples = {
                arm.value: self._capture_arm(arm) for arm in self._config.arm_ids
            }
            monotonic_timestamps = [
                camera.received_at_monotonic_ns,
                *(sample.sampled_at_monotonic_ns for sample in arm_samples.values()),
            ]
            sync_skew_ms = (
                max(monotonic_timestamps) - min(monotonic_timestamps)
            ) / 1_000_000.0
            if sync_skew_ms > self._config.maximum_sync_skew_ms:
                raise RuntimeError(
                    f"sample synchronization skew {sync_skew_ms:.3f} ms exceeds "
                    f"{self._config.maximum_sync_skew_ms:.3f} ms"
                )
            return _frame_from_samples(camera, arm_samples, sync_skew_ms)
        except Exception as exc:  # noqa: BLE001 - one bad hardware sample must not kill loop
            with self._state_lock:
                self._capture_error_count += 1
            logger.warning("failed to capture data-collection frame: %s", exc)
            return None

    def _capture_camera(self) -> DepthCameraFrame:
        if not self._camera_source.is_running or self._camera_source.camera_count <= 0:
            raise RuntimeError("depth camera is not running")
        cameras = self._camera_source.get_cameras_info()
        if not cameras:
            raise RuntimeError("no online camera is available")
        camera_index = min(self._config.camera_index, len(cameras) - 1)
        camera_info = cameras[camera_index]
        camera_key = camera_info.get("name") or camera_info.get("serial")
        if not camera_key:
            raise RuntimeError("selected camera has no name or serial")
        frame = self._camera_source.get_latest_depth_frame(camera_key)
        if frame is None:
            raise RuntimeError(f"camera {camera_key} has no available frame")
        if not frame.depth_aligned_to_color:
            raise RuntimeError(
                "data collection requires depth aligned to the color stream"
            )
        return frame

    def _capture_arm(self, arm: ArmId) -> ArmFrameData:
        telemetry = self._robot_telemetry_reader.try_read_arm_telemetry(arm)
        if telemetry is None:
            raise RuntimeError(f"{arm.value} arm telemetry is unavailable")
        if telemetry.state.joints is None:
            raise RuntimeError(f"{arm.value} arm joint state is unavailable")
        return _arm_frame_from_telemetry(telemetry)


def _arm_frame_from_telemetry(telemetry: ArmTelemetry) -> ArmFrameData:
    pose = telemetry.state.pose
    quaternion_xyzw = Rotation.from_euler(
        "xyz",
        [pose.rx_rad, pose.ry_rad, pose.rz_rad],
    ).as_quat()
    return ArmFrameData(
        sampled_at_utc_ns=telemetry.sampled_at_utc_ns,
        sampled_at_monotonic_ns=telemetry.sampled_at_monotonic_ns,
        joint_positions=np.asarray(
            telemetry.state.joints.positions_deg,
            dtype=np.float64,
        ),
        joint_velocities=_optional_array(telemetry.joint_velocities_deg_s),
        joint_currents=_optional_array(telemetry.joint_currents_amperes),
        gripper_open=telemetry.gripper.position_normalized,
        gripper_force_newtons=telemetry.gripper.force_newtons,
        gripper_raw_position=telemetry.gripper.raw_position,
        gripper_pose=np.asarray(
            [pose.x_m, pose.y_m, pose.z_m, *quaternion_xyzw],
            dtype=np.float64,
        ),
        end_effector_wrench=_optional_array(telemetry.end_effector_wrench),
    )


def _frame_from_samples(
    camera: DepthCameraFrame,
    arms: dict[str, ArmFrameData],
    sync_skew_ms: float,
) -> FrameData:
    return FrameData(
        timestamp_utc_ns=camera.received_at_utc_ns,
        camera_received_at_monotonic_ns=camera.received_at_monotonic_ns,
        front_rgb=camera.color_bgr,
        front_depth=camera.depth_uint16,
        camera_intrinsics=camera.intrinsics,
        camera_distortion_coefficients=camera.distortion_coefficients,
        depth_scale_metres=camera.depth_scale_metres,
        color_hardware_timestamp_ms=camera.color_hardware_timestamp_ms,
        depth_hardware_timestamp_ms=camera.depth_hardware_timestamp_ms,
        color_frame_number=camera.color_frame_number,
        depth_frame_number=camera.depth_frame_number,
        sample_sync_skew_ms=sync_skew_ms,
        camera_name=camera.camera_name,
        camera_serial=camera.camera_serial,
        camera_distortion_model=camera.distortion_model,
        camera_hardware_timestamp_domain=camera.hardware_timestamp_domain,
        depth_aligned_to_color=camera.depth_aligned_to_color,
        arms=arms,
    )


def _optional_array(values: tuple[float, ...] | None) -> np.ndarray | None:
    if values is None:
        return None
    return np.asarray(values, dtype=np.float64)


def _persistence_error_code(error: Exception) -> str:
    if isinstance(error, InsufficientStorageError):
        return "insufficient_storage"
    if isinstance(error, EpisodeAlreadyExistsError):
        return "episode_conflict"
    if isinstance(error, EpisodeIntegrityError):
        return "data_integrity_failed"
    if isinstance(error, EpisodeFormatUnavailableError):
        return "format_unavailable"
    if isinstance(error, EpisodeWriteError):
        return "persistence_failed"
    return "persistence_failed"

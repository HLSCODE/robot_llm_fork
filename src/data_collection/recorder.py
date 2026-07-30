from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from ..device_runtime import ArmStateReader, DepthCameraSource
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
class FrameData:
    timestamp: float
    front_rgb: np.ndarray | None = None
    front_depth: np.ndarray | None = None
    camera_intrinsics: np.ndarray | None = None
    joint_positions: np.ndarray | None = None
    joint_velocities: np.ndarray | None = None
    gripper_open: float = 0.0
    gripper_pose: np.ndarray | None = None
    joint_forces: np.ndarray | None = None
    gripper_matrix: np.ndarray | None = None
    gripper_joint_positions: np.ndarray | None = None


class DemonstrationRecorder:
    """Capture robot/camera frames and persist versioned episodes."""

    def __init__(
        self,
        robot_state_reader: ArmStateReader,
        camera_source: DepthCameraSource,
        config: DataCollectionConfig,
        *,
        writer: DataCollectionEpisodeWriter | None = None,
    ) -> None:
        self._robot_state_reader = robot_state_reader
        self._camera_source = camera_source
        self._config = config
        self._writer = writer or DataCollectionEpisodeWriter(
            config.save_path,
            format_variant=config.format_variant,
            storage_policy=config.storage_policy,
            random_seed=config.random_seed,
            source_arm=config.arm_id.value,
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

    def start_session(
        self,
        task: str,
        description: str,
    ) -> dict[str, Any]:
        with self._state_lock:
            if self._session_active:
                return {
                    "success": False,
                    "message": "数据采集会话已经启动",
                }
        try:
            status = self._writer.prepare_session(task)
        except Exception as exc:
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
        recovered = len(status.recovered_paths)
        recovery_message = f"，已清理 {recovered} 个过期临时目录" if recovered else ""
        logger.info(
            "data collection session started: task=%s next_episode_id=%d "
            "format=%s free_bytes=%d recovered=%d",
            status.task,
            status.next_episode_id,
            status.format_variant.value,
            status.free_bytes,
            recovered,
        )
        return {
            "success": True,
            "next_episode_id": status.next_episode_id,
            "message": (
                f"会话已启动，下一个 episode 编号为 "
                f"{status.next_episode_id}{recovery_message}"
            ),
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
        return {
            "success": True,
            "message": "会话已结束",
        }

    def start_recording(self) -> dict[str, Any]:
        with self._state_lock:
            if not self._session_active or self._task_name is None:
                return {
                    "success": False,
                    "message": "会话未启动，请先启动数据采集会话",
                }
            if self._collect_thread is not None:
                return {
                    "success": False,
                    "message": "episode 已在记录",
                }
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
                return {
                    "success": False,
                    "message": "未在记录状态",
                }
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
        except Exception as exc:
            logger.error(
                "failed to save episode %d: %s",
                episode_id,
                exc,
            )
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
        logger.info(
            "data collection thread started at %d Hz",
            self._config.fps,
        )
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
        frame = FrameData(timestamp=time.time())
        try:
            if not self._capture_camera(frame):
                return None
            state = self._robot_state_reader.try_read_arm_state(self._config.arm_id)
            if state is None or state.joints is None:
                raise RuntimeError(
                    f"{self._config.arm_id.value} arm state is unavailable"
                )
            frame.joint_positions = np.asarray(
                state.joints.positions_deg,
                dtype=np.float64,
            )
            quaternion_xyzw = Rotation.from_euler(
                "xyz",
                [
                    state.pose.rx_rad,
                    state.pose.ry_rad,
                    state.pose.rz_rad,
                ],
            ).as_quat()
            frame.gripper_pose = np.asarray(
                [
                    state.pose.x_m,
                    state.pose.y_m,
                    state.pose.z_m,
                    *quaternion_xyzw,
                ],
                dtype=np.float64,
            )
            return frame
        except Exception as exc:
            with self._state_lock:
                self._capture_error_count += 1
            logger.warning("failed to capture data-collection frame: %s", exc)
            return None

    def _capture_camera(self, frame: FrameData) -> bool:
        if not self._camera_source.is_running or self._camera_source.camera_count <= 0:
            raise RuntimeError("depth camera is not running")
        cameras = self._camera_source.get_cameras_info()
        if not cameras:
            raise RuntimeError("no online camera is available")
        camera_index = min(
            self._config.camera_index,
            len(cameras) - 1,
        )
        camera = cameras[camera_index]
        camera_key = camera.get("name") or camera.get("serial")
        if not camera_key:
            raise RuntimeError("selected camera has no name or serial")
        raw_frames = self._camera_source.get_latest_raw_frames(camera_key)
        if raw_frames is None:
            raise RuntimeError(f"camera {camera_key} has no available frame")
        color_bgr, depth_uint16, intrinsics = raw_frames
        if color_bgr is None or depth_uint16 is None or not intrinsics:
            raise RuntimeError(f"camera {camera_key} returned an incomplete frame")
        frame.front_rgb = color_bgr
        frame.front_depth = depth_uint16
        frame.camera_intrinsics = np.asarray(
            [
                [intrinsics.get("fx", 0), 0, intrinsics.get("ppx", 0)],
                [0, intrinsics.get("fy", 0), intrinsics.get("ppy", 0)],
                [0, 0, 1],
            ],
            dtype=np.float64,
        )
        return True


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

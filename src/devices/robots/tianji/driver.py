from __future__ import annotations

import math
import logging
from collections.abc import Sequence
from pathlib import Path
from threading import RLock
from typing import Protocol

logger = logging.getLogger(__name__)


class TianjiSdkRuntime(Protocol):
    """Small project-owned boundary around the public Tianji SDK API."""

    def initialize(self) -> None: ...

    def move_linear(
        self,
        arm: str,
        pose: Sequence[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None: ...

    def read_state(self, arm: str) -> dict[str, object]: ...

    def move_joint_pose(
        self, arm: str, pose: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def move_joints(
        self, arm: str, joints: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def set_drag_mode(self, arm: str, *, enabled: bool) -> None: ...

    def quick_stop(self, arm: str) -> None: ...

    def emergency_stop(self) -> None: ...

    def collect_data(self) -> bool: ...

    def save_recording(self, directory: str) -> bool: ...

    def run_trajectory(self, arm: str, path: str, *, blocking: bool) -> None: ...

    def move_linear_step(
        self, arm: str, pose: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def close(self) -> None: ...


class TianjiRobotDriver:
    """Own one Tianji SDK 0.2 client and expose stable project primitives."""

    def __init__(
        self,
        controller_ip: str,
        *,
        subscription_interval_seconds: float,
        left_base_transform: Sequence[Sequence[float]],
        right_base_transform: Sequence[Sequence[float]],
        left_tool_transform: Sequence[Sequence[float]],
        right_tool_transform: Sequence[Sequence[float]],
        joint_limits_rad: Sequence[Sequence[float]],
        trajectory_directory: str | Path | None = None,
        sdk_runtime: TianjiSdkRuntime | None = None,
    ) -> None:
        if not controller_ip.strip():
            raise ValueError("Tianji controller IP must not be empty")
        if subscription_interval_seconds <= 0:
            raise ValueError("Tianji subscription interval must be positive")

        self._lock = RLock()
        self._closed = False
        self._joint_limits = tuple(tuple(pair) for pair in joint_limits_rad)
        if len(self._joint_limits) != 7 or any(
            len(pair) != 2
            or not all(math.isfinite(value) for value in pair)
            or pair[0] >= pair[1]
            for pair in self._joint_limits
        ):
            raise ValueError("Tianji joint limits must contain seven finite min/max pairs")
        self._runtime = sdk_runtime or _OfficialTianjiSdkRuntime(
            controller_ip=controller_ip,
            subscription_interval_seconds=subscription_interval_seconds,
            left_base_transform=left_base_transform,
            right_base_transform=right_base_transform,
            left_tool_transform=left_tool_transform,
            right_tool_transform=right_tool_transform,
            joint_limits_rad=joint_limits_rad,
            trajectory_directory=trajectory_directory,
        )
        try:
            self._runtime.initialize()
            self.read_state("A")
            self.read_state("B")
        except Exception:
            try:
                self._runtime.close()
            except Exception:
                logger.exception("Failed to close Tianji SDK after initialization failure")
            self._closed = True
            raise

    def move_to_pose(
        self,
        arm: str,
        pose: list[float],
        *,
        linear: bool,
        velocity_percent: int,
        blocking: bool,
    ) -> bool:
        sdk_arm = _validate_arm(arm)
        target = _numeric_values(pose, 6, "pose")
        if not 1 <= velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        with self._lock:
            self._require_open()
            move = self._runtime.move_linear if linear else self._runtime.move_joint_pose
            move(
                sdk_arm,
                target,
                velocity_percent=velocity_percent,
                blocking=blocking,
            )
        return True

    def move_to_joints(
        self,
        arm: str,
        joints: list[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        target = _numeric_values(joints, 7, "joints")
        if not 1 <= velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        for index, (value, limits) in enumerate(zip(target, self._joint_limits, strict=True)):
            if not limits[0] <= math.radians(value) <= limits[1]:
                raise ValueError(f"Tianji joint {index + 1} target exceeds configured limits")
        with self._lock:
            self._require_open()
            self._runtime.move_joints(
                _validate_arm(arm),
                target,
                velocity_percent=velocity_percent,
                blocking=blocking,
            )

    def quick_stop(self, arm: str) -> None:
        # Stops must remain callable while a blocking motion owns the command lock.
        self._require_open()
        self._runtime.quick_stop(_validate_arm(arm))

    def move_joint_increment(
        self,
        arm: str,
        delta_deg: list[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        delta = _numeric_values(delta_deg, 7, "joint increment")
        with self._lock:
            current = _numeric_values(self.read_state(arm)["joints"], 7, "joints")
            self.move_to_joints(
                arm,
                [a + b for a, b in zip(current, delta, strict=True)],
                velocity_percent=velocity_percent,
                blocking=blocking,
            )

    def move_pose_increment(
        self,
        arm: str,
        delta_pose: list[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        delta = _numeric_values(delta_pose, 6, "pose increment")
        if not 1 <= velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        with self._lock:
            self._require_open()
            self._runtime.move_linear_step(
                _validate_arm(arm),
                delta,
                velocity_percent=velocity_percent,
                blocking=blocking,
            )

    def emergency_stop(self) -> None:
        self._require_open()
        self._runtime.emergency_stop()

    def set_drag_mode(self, arm: str, *, enabled: bool) -> None:
        with self._lock:
            self._require_open()
            self._runtime.set_drag_mode(_validate_arm(arm), enabled=enabled)

    def start_recording(self) -> None:
        with self._lock:
            self._require_open()
            if not self._runtime.collect_data():
                raise RuntimeError("Tianji recording not started; already active or rejected")

    def save_recording(self, directory: str | Path) -> None:
        with self._lock:
            self._require_open()
            if not self._runtime.save_recording(str(Path(directory).expanduser().resolve())):
                raise RuntimeError("Tianji recording was not saved")

    def run_trajectory(self, arm: str, path: str | Path, *, blocking: bool = True) -> None:
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != ".fmv" or not target.is_file():
            raise ValueError("Tianji playback requires an existing .fmv file")
        with self._lock:
            self._require_open()
            self._runtime.run_trajectory(_validate_arm(arm), str(target), blocking=blocking)

    def read_state(self, arm: str) -> dict[str, object]:
        sdk_arm = _validate_arm(arm)
        with self._lock:
            self._require_open()
            payload = self._runtime.read_state(sdk_arm)
        return {
            **payload,
            "pose": _numeric_values(payload.get("pose"), 6, "pose"),
            "joints": _numeric_values(payload.get("joints"), 7, "joints"),
            "error_code": _error_code(payload.get("error_code", 0)),
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._runtime.close()
            self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Tianji driver is closed")


class _OfficialTianjiSdkRuntime:
    """Translate the official SDK's public value objects at one boundary."""

    def __init__(
        self,
        *,
        controller_ip: str,
        subscription_interval_seconds: float,
        left_base_transform: Sequence[Sequence[float]],
        right_base_transform: Sequence[Sequence[float]],
        left_tool_transform: Sequence[Sequence[float]],
        right_tool_transform: Sequence[Sequence[float]],
        joint_limits_rad: Sequence[Sequence[float]],
        trajectory_directory: str | Path | None,
    ) -> None:
        try:
            from tj_robot_proj import (
                Arm,
                ArmConfig,
                ArmCollectionDataOption,
                JointsLimit,
                RobotClient,
                RobotConfig,
                SE3,
                SO3,
                TransformationConfig,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Tianji SDK unavailable; install the platform-specific tj-robot-proj 0.2 wheel"
            ) from exc

        limits = JointsLimit.from_rad(
            tuple(tuple(pair) for pair in joint_limits_rad),
            num_joints=7,
        )
        left_config = ArmConfig(
            arm=Arm.LEFT,
            trans_config=TransformationConfig(
                T_base_ref_world=SE3.from_matrix(left_base_transform),
                T_tool_ref_end=SE3.from_matrix(left_tool_transform),
            ),
            joints_limit=limits,
        )
        right_config = ArmConfig(
            arm=Arm.RIGHT,
            trans_config=TransformationConfig(
                T_base_ref_world=SE3.from_matrix(right_base_transform),
                T_tool_ref_end=SE3.from_matrix(right_tool_transform),
            ),
            joints_limit=limits,
        )
        self._arm_type = {"A": Arm.LEFT, "B": Arm.RIGHT}
        self._pose_factory = lambda pose: SE3(
            SO3.from_xyz_euler(pose[3], pose[4], pose[5]),
            pose[:3],
        )
        self._client = RobotClient(
            RobotConfig(
                controller_ip=controller_ip,
                left_arm_config=left_config,
                right_arm_config=right_config,
                left_arm_collection_data_option=ArmCollectionDataOption(
                    arm=Arm.LEFT,
                    data_types=tuple(range(7)),
                ),
                right_arm_collection_data_option=ArmCollectionDataOption(
                    arm=Arm.RIGHT,
                    data_types=tuple(range(7)),
                ),
                traj_data_dir=str(trajectory_directory or _default_trajectory_directory()),
                subscription_interval_seconds=subscription_interval_seconds,
            )
        )

    def initialize(self) -> None:
        self._client.initialize()

    def move_linear(
        self,
        arm: str,
        pose: Sequence[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        self._client.movel(
            self._arm_type[arm],
            self._pose_factory(pose),
            vel=velocity_percent,
            is_block=blocking,
        )

    def read_state(self, arm: str) -> dict[str, object]:
        if not self._client.connected or self._client.closed:
            raise RuntimeError("Tianji controller is disconnected")
        state = self._client.get_arm_data(self._arm_type[arm])
        translation = tuple(float(value) for value in state.T_end_ref_world.translation)
        rotation = tuple(float(value) for value in state.T_end_ref_world.rotation.xyz_euler)
        return {
            "pose": [*translation, *rotation],
            "joints": list(state.joints_pos.pos_deg),
            "joint_velocities_deg_s": list(state.joints_vel.vel_deg_s),
            "button_pressed": state.button_pressed,
            "error_code": 0,
        }

    def move_joint_pose(
        self, arm: str, pose: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None:
        self._client.movej_p(
            self._arm_type[arm], self._pose_factory(pose),
            vel=velocity_percent, is_block=blocking,
        )

    def move_joints(
        self, arm: str, joints: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None:
        self._client.movej(
            self._arm_type[arm],
            joints_deg=joints,
            vel=velocity_percent,
            is_block=blocking,
        )

    def move_linear_step(
        self, arm: str, pose: Sequence[float], *, velocity_percent: int, blocking: bool
    ) -> None:
        self._client.movel_step(
            self._arm_type[arm],
            self._pose_factory(pose),
            vel=velocity_percent,
            is_block=blocking,
        )

    def set_drag_mode(self, arm: str, *, enabled: bool) -> None:
        if enabled:
            self._client.change_to_drag_mode(self._arm_type[arm])
        else:
            self._client.change_to_norm_mode(self._arm_type[arm])

    def quick_stop(self, arm: str) -> None:
        self._client.quick_stop(self._arm_type[arm])

    def emergency_stop(self) -> None:
        self._client.soft_emergency_stop()

    def collect_data(self) -> bool:
        return self._client.collect_data()

    def save_recording(self, directory: str) -> bool:
        return self._client.stop_and_save_data(directory)

    def run_trajectory(self, arm: str, path: str, *, blocking: bool) -> None:
        self._client.run_trajectory(
            self._arm_type[arm],
            fmv_trajectory_path=path,
            is_block=blocking,
        )

    def close(self) -> None:
        self._client.close()


def _validate_arm(arm: str) -> str:
    normalized = str(arm).strip().upper()
    if normalized not in {"A", "B"}:
        raise ValueError(f"Tianji arm must be A or B, got {arm!r}")
    return normalized


def _default_trajectory_directory() -> Path:
    from ....configuration.data_paths import ApplicationDataPaths
    from ....configuration.settings import DataSettings

    return ApplicationDataPaths.from_settings(
        DataSettings(),
        "tianji-tianji-dual",
    ).trajectories_directory


def _numeric_values(value: object, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise RuntimeError(f"Tianji {label} must contain {length} values")
    if any(isinstance(item, bool) for item in value):
        raise RuntimeError(f"Tianji {label} must not contain boolean values")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Tianji {label} must be numeric") from exc
    if not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"Tianji {label} contains non-finite values")
    return result


def _error_code(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RuntimeError("Tianji error_code must be numeric")
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError("Tianji error_code must be numeric") from exc

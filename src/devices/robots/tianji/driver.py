from __future__ import annotations

import math
from collections.abc import Sequence
from threading import RLock
from typing import Protocol


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
        sdk_runtime: TianjiSdkRuntime | None = None,
    ) -> None:
        if not controller_ip.strip():
            raise ValueError("Tianji controller IP must not be empty")
        if subscription_interval_seconds <= 0:
            raise ValueError("Tianji subscription interval must be positive")

        self._lock = RLock()
        self._closed = False
        self._runtime = sdk_runtime or _OfficialTianjiSdkRuntime(
            controller_ip=controller_ip,
            subscription_interval_seconds=subscription_interval_seconds,
            left_base_transform=left_base_transform,
            right_base_transform=right_base_transform,
            left_tool_transform=left_tool_transform,
            right_tool_transform=right_tool_transform,
            joint_limits_rad=joint_limits_rad,
        )
        try:
            self._runtime.initialize()
            self.read_state("A")
            self.read_state("B")
        except Exception:
            try:
                self._runtime.close()
            except Exception:
                pass
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
        if not linear:
            raise NotImplementedError(
                "Tianji SDK 0.2 does not expose Cartesian-target joint motion"
            )
        if not 1 <= velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        with self._lock:
            self._require_open()
            self._runtime.move_linear(
                sdk_arm,
                target,
                velocity_percent=velocity_percent,
                blocking=blocking,
            )
        return True

    def read_state(self, arm: str) -> dict[str, object]:
        sdk_arm = _validate_arm(arm)
        with self._lock:
            self._require_open()
            payload = self._runtime.read_state(sdk_arm)
        return {
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
    ) -> None:
        try:
            from tj_robot_proj import (
                Arm,
                ArmConfig,
                JointsLimit,
                RobotClient,
                RobotConfig,
                SE3,
                SO3,
                TransformationConfig,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Tianji SDK unavailable; install the platform-specific "
                "tj-robot-proj 0.2 wheel"
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
        state = self._client.get_arm_data(self._arm_type[arm])
        translation = tuple(float(value) for value in state.T_end_ref_world.translation)
        rotation = tuple(float(value) for value in state.T_end_ref_world.rotation.xyz_euler)
        return {
            "pose": [*translation, *rotation],
            "joints": list(state.joints_pos.pos_deg),
            "error_code": 0,
        }

    def close(self) -> None:
        self._client.close()


def _validate_arm(arm: str) -> str:
    normalized = str(arm).strip().upper()
    if normalized not in {"A", "B"}:
        raise ValueError(f"Tianji arm must be A or B, got {arm!r}")
    return normalized


def _numeric_values(value: object, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise RuntimeError(f"Tianji {label} must contain {length} values")
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

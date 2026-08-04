from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ArmId(str, Enum):
    LEFT = "left"
    RIGHT = "right"

    @classmethod
    def parse(cls, value: object) -> ArmId:
        normalized = str(value).strip().lower()
        aliases = {
            "left": cls.LEFT,
            "左": cls.LEFT,
            "robot1": cls.LEFT,
            "r1": cls.LEFT,
            "right": cls.RIGHT,
            "右": cls.RIGHT,
            "robot2": cls.RIGHT,
            "r2": cls.RIGHT,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown arm: {value}") from exc


class MotionMode(str, Enum):
    JOINT = "joint"
    LINEAR = "linear"

    @classmethod
    def parse(cls, value: object) -> MotionMode:
        normalized = str(value).strip().lower()
        aliases = {
            "joint": cls.JOINT,
            "move_j": cls.JOINT,
            "linear": cls.LINEAR,
            "move_l": cls.LINEAR,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown motion mode: {value}") from exc


@dataclass(frozen=True, slots=True)
class CartesianPose:
    """Vendor-neutral tool pose in metres and radians."""

    x_m: float
    y_m: float
    z_m: float
    rx_rad: float
    ry_rad: float
    rz_rad: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in self.to_list()):
            raise ValueError("cartesian pose values must be finite")

    @classmethod
    def from_iterable(cls, values: Iterable[float]) -> CartesianPose:
        normalized = tuple(float(value) for value in values)
        if len(normalized) != 6:
            raise ValueError(f"cartesian pose requires 6 values, got {len(normalized)}")
        return cls(*normalized)

    def to_list(self) -> list[float]:
        return [
            self.x_m,
            self.y_m,
            self.z_m,
            self.rx_rad,
            self.ry_rad,
            self.rz_rad,
        ]


@dataclass(frozen=True, slots=True)
class JointVector:
    """Joint positions in degrees, independent of robot joint count."""

    positions_deg: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.positions_deg:
            raise ValueError("joint vector must not be empty")
        if not all(math.isfinite(value) for value in self.positions_deg):
            raise ValueError("joint positions must be finite")

    @classmethod
    def from_iterable(cls, values: Iterable[float]) -> JointVector:
        positions = tuple(float(value) for value in values)
        return cls(positions_deg=positions)

    def to_list(self) -> list[float]:
        return list(self.positions_deg)


@dataclass(frozen=True, slots=True)
class MotionOptions:
    """Normalized motion options consumed by every robot adapter."""

    velocity_percent: int = 10
    blend_radius: int = 0
    connected: bool = False
    blocking: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        if self.blend_radius < 0:
            raise ValueError("blend_radius must be non-negative")


@dataclass(frozen=True, slots=True)
class ArmState:
    arm: ArmId
    pose: CartesianPose
    joints: JointVector | None = None
    device_error_code: int = 0


@dataclass(frozen=True, slots=True)
class GripperTelemetry:
    """Normalized gripper state independent of provider position ranges."""

    position_normalized: float
    force_newtons: float
    raw_position: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.position_normalized <= 1.0:
            raise ValueError("gripper position_normalized must be in range 0..1")
        if not math.isfinite(self.force_newtons) or self.force_newtons < 0:
            raise ValueError("gripper force_newtons must be finite and non-negative")
        if self.raw_position < 0:
            raise ValueError("gripper raw_position must not be negative")


@dataclass(frozen=True, slots=True)
class ArmTelemetry:
    """One timestamped arm sample with truthful optional sensor fields."""

    state: ArmState
    sampled_at_utc_ns: int
    sampled_at_monotonic_ns: int
    gripper: GripperTelemetry
    joint_velocities_deg_s: tuple[float, ...] | None = None
    joint_currents_amperes: tuple[float, ...] | None = None
    end_effector_wrench: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.sampled_at_utc_ns <= 0:
            raise ValueError("sampled_at_utc_ns must be positive")
        if self.sampled_at_monotonic_ns <= 0:
            raise ValueError("sampled_at_monotonic_ns must be positive")
        joint_count = (
            len(self.state.joints.positions_deg)
            if self.state.joints is not None
            else None
        )
        _validate_optional_joint_values(
            self.joint_velocities_deg_s,
            joint_count,
            "joint_velocities_deg_s",
        )
        _validate_optional_joint_values(
            self.joint_currents_amperes,
            joint_count,
            "joint_currents_amperes",
        )
        if self.end_effector_wrench is not None:
            if len(self.end_effector_wrench) != 6:
                raise ValueError("end_effector_wrench must contain 6 values")
            if not all(math.isfinite(value) for value in self.end_effector_wrench):
                raise ValueError("end_effector_wrench values must be finite")


@dataclass(frozen=True, slots=True)
class TrajectorySaveResult:
    path: Path
    point_count: int


class RobotOperationError(RuntimeError):
    def __init__(
        self,
        operation: str,
        arm: ArmId,
        *,
        code: int | None = None,
        detail: str = "",
    ) -> None:
        message = f"robot operation '{operation}' failed for arm '{arm.value}'"
        if code is not None:
            message += f" with code {code}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.operation = operation
        self.arm = arm
        self.code = code
        self.detail = detail


def _validate_optional_joint_values(
    values: tuple[float, ...] | None,
    joint_count: int | None,
    field_name: str,
) -> None:
    if values is None:
        return
    if joint_count is None or len(values) != joint_count:
        raise ValueError(f"{field_name} must match the arm joint count")
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{field_name} values must be finite")

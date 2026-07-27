from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class ArmId(str, Enum):
    LEFT = "left"
    RIGHT = "right"

    @classmethod
    def parse(cls, value: object) -> "ArmId":
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
    def parse(cls, value: object) -> "MotionMode":
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
    def from_iterable(cls, values: Iterable[float]) -> "CartesianPose":
        normalized = tuple(float(value) for value in values)
        if len(normalized) != 6:
            raise ValueError(
                f"cartesian pose requires 6 values, got {len(normalized)}"
            )
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
    def from_iterable(cls, values: Iterable[float]) -> "JointVector":
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

"""Stepper motor register definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import TypeAlias


class StepperSeries(Enum):
    M = "M"
    B = "B"


StepperSeriesLike: TypeAlias = StepperSeries | str


class MotorStatus(IntEnum):
    IDLE_OR_ARRIVED = 0
    RUNNING = 1
    COLLISION_STOP = 2
    POSITIVE_PHOTOELECTRIC_STOP = 3
    NEGATIVE_PHOTOELECTRIC_STOP = 4
    BLOCKED = 2
    HOMING = 3
    ERROR = 4


class MSeriesRegister(IntEnum):
    STATUS = 0x00
    ACTUAL_STEPS_HIGH = 0x01
    ACTUAL_STEPS_LOW = 0x02
    ACTUAL_SPEED = 0x03
    EMERGENCY_STOP = 0x04
    CURRENT = 0x05
    ENABLE = 0x06
    PWM_OUTPUT = 0x07
    LIMIT_SWITCH_ENABLE = 0x08
    POINT_STEPS_HIGH = 0x10
    POINT_STEPS_LOW = 0x11
    POINT_INITIAL_SPEED = 0x12
    POINT_SPEED = 0x13
    POINT_ACCELERATION_MS = 0x14
    POINT_TOLERANCE_STEPS = 0x15
    ABSOLUTE_STEPS_HIGH = 0x22
    ABSOLUTE_STEPS_LOW = 0x23
    ABSOLUTE_INITIAL_SPEED = 0x24
    ABSOLUTE_SPEED = 0x25
    ABSOLUTE_ACCELERATION_MS = 0x60
    ABSOLUTE_TOLERANCE_STEPS = 0x61

    ACTUAL_POSITION_HIGH = ACTUAL_STEPS_HIGH
    ACTUAL_POSITION_LOW = ACTUAL_STEPS_LOW
    TARGET_POSITION_HIGH = POINT_STEPS_HIGH
    TARGET_POSITION_LOW = POINT_STEPS_LOW
    TARGET_SPEED = POINT_SPEED
    ACCELERATION = POINT_ACCELERATION_MS


class BSeriesRegister(IntEnum):
    STATUS = 0x00
    ENABLE = 0x06


Register = MSeriesRegister | BSeriesRegister | int
StepperRegister = Register


@dataclass(frozen=True)
class StepperSeriesSpec:
    series: StepperSeries
    position_high: int = MSeriesRegister.POINT_STEPS_HIGH
    position_low: int = MSeriesRegister.POINT_STEPS_LOW
    initial_speed: int = MSeriesRegister.POINT_INITIAL_SPEED
    speed: int = MSeriesRegister.POINT_SPEED
    acceleration: int = MSeriesRegister.POINT_ACCELERATION_MS
    tolerance: int = MSeriesRegister.POINT_TOLERANCE_STEPS


M_SERIES_SPEC = StepperSeriesSpec(StepperSeries.M)
B_SERIES_SPEC = StepperSeriesSpec(StepperSeries.B)
STEPPER_SPECS = {StepperSeries.M: M_SERIES_SPEC, StepperSeries.B: B_SERIES_SPEC}
DEFAULT_STEPPER_SERIES_BY_ADDRESS = {}


def clamp_u16(value: int) -> int:
    return max(0, min(0xFFFF, int(value)))


def int16_to_register(value: int) -> int:
    value = int(value)
    if value < 0:
        value = (1 << 16) + value
    return value & 0xFFFF


def register_to_int16(value: int) -> int:
    value = int(value) & 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def int32_to_registers(value: int) -> tuple[int, int]:
    value = int(value)
    if value < 0:
        value = (1 << 32) + value
    value &= 0xFFFFFFFF
    return (value >> 16) & 0xFFFF, value & 0xFFFF


def registers_to_int32(high: int, low: int) -> int:
    value = ((int(high) & 0xFFFF) << 16) | (int(low) & 0xFFFF)
    return value - 0x100000000 if value & 0x80000000 else value


def normalize_stepper_series(series: StepperSeriesLike | None = None) -> StepperSeries:
    if series is None:
        return StepperSeries.M
    if isinstance(series, StepperSeries):
        return series
    return StepperSeries(str(series).upper())


def get_stepper_spec(series: StepperSeriesLike | None = None) -> StepperSeriesSpec:
    return STEPPER_SPECS[normalize_stepper_series(series)]


def speed_to_register(rpm: float, scale: int = 10) -> int:
    return int16_to_register(round(float(rpm) * scale))


def register_to_speed(value: int, scale: int = 10) -> float:
    return register_to_int16(value) / scale


rpm_to_register = speed_to_register
register_to_rpm = register_to_speed

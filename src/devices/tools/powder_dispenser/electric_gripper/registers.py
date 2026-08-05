"""Electric gripper register definitions."""

from __future__ import annotations

from enum import IntEnum

DEFAULT_GRIPPER_ADDRESS = 9


class GripperRegister(IntEnum):
    INITIALIZE = 0x0100
    EMERGENCY_STOP = 0x0102
    FORCE = 0x0103
    SPEED = 0x0104
    TARGET_POS = 0x0105
    GRIP_RELEASE = 0x0109
    INIT_STATUS = 0x0200
    MOTION_STATUS = 0x0202
    CURRENT_POS = 0x0204
    HOME_DIR = 0x0300
    SAVE = 0x0302
    DEVICE_ID = 0x0303
    EXCITATION = 0x0304


class InitializationStatus(IntEnum):
    NOT_INITIALIZED = 0
    INITIALIZING = 1
    INITIALIZED = 2


class MotionStatus(IntEnum):
    MOVING = 0
    REACHED_TARGET = 1
    GRIPPING = 2
    ERROR = 3


class EmergencyStopStatus(IntEnum):
    NORMAL = 0
    STOPPED = 1


class HomingDirection(IntEnum):
    OPEN = 0
    CLOSE = 1


class SaveStatus(IntEnum):
    IDLE = 0
    SAVING = 1
    SAVED = 2


class ExcitationState(IntEnum):
    DISABLED = 0
    ENABLED = 1


class GripAction(IntEnum):
    RELEASE = 0
    GRIP = 1


def clamp_percent(value: int) -> int:
    return max(0, min(100, int(value)))


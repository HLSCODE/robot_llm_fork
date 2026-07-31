from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeviceState(str, Enum):
    REGISTERED = "registered"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class DeviceCapability(str, Enum):
    MOTION = "motion"
    QUICK_STOP = "quick_stop"
    EMERGENCY_STOP = "emergency_stop"
    ARM_MOTION = "arm_motion"
    ARM_STATE = "arm_state"
    ARM_TELEMETRY = "arm_telemetry"
    GRIPPER = "gripper"
    ROBOT_TELEOPERATION = "robot_teleoperation"
    TRAJECTORY = "trajectory"
    TOOL_RACK = "tool_rack"
    BODY_AXIS = "body_axis"
    MOBILE_BASE = "mobile_base"
    NECK_MOTION = "neck_motion"
    DIGITAL_OUTPUT = "digital_output"
    TOOL_CHANGER = "tool_changer"
    PIPETTE = "pipette"
    POWDER_DISPENSER = "powder_dispenser"
    CAMERA = "camera"
    EXPRESSION_DISPLAY = "expression_display"
    SAFE_STATE = "safe_state"


class StopMode(str, Enum):
    CONTROLLED = "controlled"
    QUICK = "quick"
    EMERGENCY = "emergency"


class DeviceStopStatus(str, Enum):
    STOPPED = "stopped"
    NOT_READY = "not_ready"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class DeviceSafeStateStatus(str, Enum):
    APPLIED = "applied"
    NOT_READY = "not_ready"
    FAILED = "failed"


class DeviceErrorCategory(str, Enum):
    UNAVAILABLE = "unavailable"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    REJECTED = "rejected"
    IO = "io"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class DeviceStopResult:
    device_id: str
    mode: StopMode
    status: DeviceStopStatus
    error: str = ""
    error_category: str = ""
    raw_error_code: str = ""

    @property
    def successful(self) -> bool:
        return self.status in {
            DeviceStopStatus.STOPPED,
            DeviceStopStatus.NOT_READY,
        }


@dataclass(frozen=True, slots=True)
class DeviceSafeStateResult:
    device_id: str
    status: DeviceSafeStateStatus
    error: str = ""
    error_category: str = ""
    raw_error_code: str = ""

    @property
    def successful(self) -> bool:
        return self.status in {
            DeviceSafeStateStatus.APPLIED,
            DeviceSafeStateStatus.NOT_READY,
        }


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    device_id: str
    state: DeviceState
    capabilities: tuple[DeviceCapability, ...]
    error: str = ""
    error_category: str = ""
    raw_error_code: str = ""

    @property
    def ready(self) -> bool:
        return self.state is DeviceState.READY


class DeviceRuntimeError(RuntimeError):
    """Base error for device lifecycle and capability access."""


class DeviceOperationError(DeviceRuntimeError):
    """Stable device failure with safe user text and internal diagnostics."""

    def __init__(
        self,
        *,
        device_id: str,
        operation: str,
        category: DeviceErrorCategory,
        user_message: str,
        diagnostic_message: str,
        raw_error_code: str = "",
    ) -> None:
        super().__init__(user_message)
        self.device_id = device_id
        self.operation = operation
        self.category = category
        self.user_message = user_message
        self.diagnostic_message = diagnostic_message
        self.raw_error_code = raw_error_code


class DeviceAlreadyRegisteredError(DeviceRuntimeError):
    pass


class DeviceNotRegisteredError(DeviceRuntimeError):
    pass


class DeviceInitializationError(DeviceRuntimeError):
    pass


class DeviceContractError(DeviceRuntimeError):
    pass


class ResourceBusyError(DeviceRuntimeError):
    def __init__(self, resource_id: str, owner_id: str) -> None:
        super().__init__(f"resource '{resource_id}' is owned by '{owner_id}'")
        self.resource_id = resource_id
        self.owner_id = owner_id

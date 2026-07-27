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
    ARM_MOTION = "arm_motion"
    ARM_STATE = "arm_state"
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


class StopMode(str, Enum):
    CONTROLLED = "controlled"
    QUICK = "quick"
    EMERGENCY = "emergency"


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    device_id: str
    state: DeviceState
    capabilities: tuple[DeviceCapability, ...]
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.state is DeviceState.READY


class DeviceRuntimeError(RuntimeError):
    """Base error for device lifecycle and capability access."""


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

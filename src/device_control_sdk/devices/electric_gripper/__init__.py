"""Electric gripper exports."""

from .client import ElectricGripper
from .registers import (
    DEFAULT_GRIPPER_ADDRESS,
    EmergencyStopStatus,
    ExcitationState,
    GripAction,
    GripperRegister,
    HomingDirection,
    InitializationStatus,
    MotionStatus,
    SaveStatus,
)

StatefulElectricGripper = ElectricGripper
ElectricGripperSnapshot = object


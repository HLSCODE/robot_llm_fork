from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..core.settings import DeviceSettings, RobotSettings
from .adapters import (
    RealManGripperOptions,
    RealManRobotAdapter,
    RealManToolRackOptions,
    RealManToolRackSlot,
)
from .arm_models import ArmId, CartesianPose, MotionOptions
from .contracts import RobotSystem
from .models import DeviceCapability, DeviceInitializationError


@dataclass(frozen=True, slots=True)
class RobotProviderDefinition:
    """One production robot provider and its truthful capabilities."""

    name: str
    capabilities: frozenset[DeviceCapability]
    create: Callable[[RobotSettings, DeviceSettings], RobotSystem]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("robot provider name must not be empty")
        required = {
            DeviceCapability.MOTION,
            DeviceCapability.ARM_MOTION,
            DeviceCapability.ARM_STATE,
            DeviceCapability.GRIPPER,
        }
        missing = required - self.capabilities
        if missing:
            values = ", ".join(
                sorted(capability.value for capability in missing)
            )
            raise ValueError(
                f"robot provider '{self.name}' lacks core capabilities: "
                f"{values}"
            )


@dataclass(frozen=True, slots=True)
class RealManArmConnection:
    ip: str
    port: int
    initial_pose: CartesianPose

    def __post_init__(self) -> None:
        if not self.ip.strip():
            raise ValueError("RealMan arm IP must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("RealMan arm port must be in range 1..65535")

    def to_controller_config(self) -> dict[str, object]:
        return {
            "ip": self.ip,
            "port": self.port,
            "initial_pose": self.initial_pose.to_list(),
        }


@dataclass(frozen=True, slots=True)
class RealManProviderSettings:
    """Validated model-specific settings consumed only by the RealMan provider."""

    model: str
    left_arm: RealManArmConnection
    right_arm: RealManArmConnection
    motion: MotionOptions
    gripper: RealManGripperOptions
    tool_rack: RealManToolRackOptions

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("RealMan model must not be empty")

    @classmethod
    def from_settings(cls, settings: RobotSettings) -> RealManProviderSettings:
        try:
            return cls(
                model=settings.robot_model.strip(),
                left_arm=RealManArmConnection(
                    ip=settings.robot1_ip,
                    port=settings.robot1_port,
                    initial_pose=CartesianPose.from_iterable(
                        settings.robot1_initial_pose
                    ),
                ),
                right_arm=RealManArmConnection(
                    ip=settings.robot2_ip,
                    port=settings.robot2_port,
                    initial_pose=CartesianPose.from_iterable(
                        settings.robot2_initial_pose
                    ),
                ),
                motion=MotionOptions(
                    velocity_percent=settings.move_velocity,
                    blend_radius=settings.move_radius,
                    connected=_binary_flag(
                        settings.move_connect,
                        "MOVE_CONNECT",
                    ),
                    blocking=_binary_flag(
                        settings.move_block,
                        "MOVE_BLOCK",
                    ),
                ),
                gripper=RealManGripperOptions(
                    pick_speed=settings.gripper_pick_speed,
                    pick_force=settings.gripper_pick_force,
                    pick_timeout_s=settings.gripper_pick_timeout,
                    release_speed=settings.gripper_release_speed,
                    release_timeout_s=settings.gripper_release_timeout,
                    max_attempts=settings.max_attempts,
                ),
                tool_rack=_tool_rack_options(settings),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeviceInitializationError(
                f"invalid RealMan provider configuration: {exc}"
            ) from exc


def resolve_robot_provider(settings: RobotSettings) -> RobotProviderDefinition:
    provider_name = settings.robot_provider.strip().lower()
    try:
        return ROBOT_PROVIDERS[provider_name]
    except KeyError as exc:
        supported = ", ".join(sorted(ROBOT_PROVIDERS))
        raise DeviceInitializationError(
            f"unsupported robot provider: {provider_name}; "
            f"supported providers: {supported}"
        ) from exc


def _create_realman_robot(
    robot_settings: RobotSettings,
    device_settings: DeviceSettings,
) -> RobotSystem:
    from ..arm_sdk import RobotController
    from ..devices import yiyeqiang_out

    if RobotController is None:
        raise DeviceInitializationError("RobotController SDK unavailable")

    settings = RealManProviderSettings.from_settings(robot_settings)
    controller = RobotController(
        settings.left_arm.to_controller_config(),
        settings.right_arm.to_controller_config(),
    )
    adapter = RealManRobotAdapter(
        controller,
        default_motion=settings.motion,
        gripper_options=settings.gripper,
        tool_rack_options=settings.tool_rack,
        eject_tool=lambda: yiyeqiang_out.eject_tip(
            port=device_settings.kuaihuanshou_serial_port
        ),
    )
    try:
        adapter.read_arm_state(ArmId.LEFT)
        adapter.read_arm_state(ArmId.RIGHT)
        return adapter
    except Exception:
        adapter.close()
        raise


def _binary_flag(value: int, field_name: str) -> bool:
    if value not in (0, 1):
        raise ValueError(f"{field_name} must be 0 or 1")
    return bool(value)


def _tool_rack_options(settings: RobotSettings) -> RealManToolRackOptions:
    slots = (
        RealManToolRackSlot(
            slot_id=1,
            approach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_1_approach_pose
            ),
            attach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_1_attach_pose
            ),
            detach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_1_detach_pose
            ),
            attach_dwell_seconds=(
                settings.robot_tool_rack_slot_1_attach_dwell_seconds
            ),
            detach_dwell_seconds=(
                settings.robot_tool_rack_slot_1_detach_dwell_seconds
            ),
        ),
        RealManToolRackSlot(
            slot_id=2,
            approach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_2_approach_pose
            ),
            attach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_2_attach_pose
            ),
            detach_pose=CartesianPose.from_iterable(
                settings.robot_tool_rack_slot_2_detach_pose
            ),
            attach_dwell_seconds=(
                settings.robot_tool_rack_slot_2_attach_dwell_seconds
            ),
            detach_dwell_seconds=(
                settings.robot_tool_rack_slot_2_detach_dwell_seconds
            ),
        ),
    )
    return RealManToolRackOptions(
        arm=ArmId.parse(settings.robot_tool_rack_arm),
        slots=slots,
    )


_REALMAN_CAPABILITIES = frozenset({
    DeviceCapability.MOTION,
    DeviceCapability.QUICK_STOP,
    DeviceCapability.EMERGENCY_STOP,
    DeviceCapability.ARM_MOTION,
    DeviceCapability.ARM_STATE,
    DeviceCapability.ARM_TELEMETRY,
    DeviceCapability.GRIPPER,
    DeviceCapability.ROBOT_TELEOPERATION,
    DeviceCapability.TRAJECTORY,
    DeviceCapability.TOOL_RACK,
})

ROBOT_PROVIDERS: Mapping[str, RobotProviderDefinition] = MappingProxyType({
    "realman": RobotProviderDefinition(
        name="realman",
        capabilities=_REALMAN_CAPABILITIES,
        create=_create_realman_robot,
    ),
})

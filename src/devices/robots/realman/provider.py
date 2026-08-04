"""RealMan-specific configuration and adapter composition."""

from __future__ import annotations

from dataclasses import dataclass

from ....configuration.settings import RobotSettings
from ...runtime.arm_models import ArmId, CartesianPose, MotionOptions
from ...runtime.contracts import RobotSystem
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import RobotProviderDefinition
from .adapter import (
    RealManGripperOptions,
    RealManRobotAdapter,
    RealManToolRackOptions,
    RealManToolRackSlot,
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
    """Validated model-specific settings consumed only by this provider."""

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
                    connected=_binary_flag(settings.move_connect, "MOVE_CONNECT"),
                    blocking=_binary_flag(settings.move_block, "MOVE_BLOCK"),
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


def _create_realman_robot(settings: RobotSettings) -> RobotSystem:
    try:
        from .driver import RobotController
    except ImportError as exc:
        raise DeviceInitializationError(
            "RealMan RobotController SDK unavailable"
        ) from exc

    provider_settings = RealManProviderSettings.from_settings(settings)
    controller = RobotController(
        provider_settings.left_arm.to_controller_config(),
        provider_settings.right_arm.to_controller_config(),
    )
    adapter = RealManRobotAdapter(
        controller,
        default_motion=provider_settings.motion,
        gripper_options=provider_settings.gripper,
        tool_rack_options=provider_settings.tool_rack,
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
    slots = tuple(
        RealManToolRackSlot(
            slot_id=slot_id,
            approach_pose=CartesianPose.from_iterable(approach_pose),
            attach_pose=CartesianPose.from_iterable(attach_pose),
            detach_pose=CartesianPose.from_iterable(detach_pose),
            attach_dwell_seconds=attach_dwell_seconds,
            detach_dwell_seconds=detach_dwell_seconds,
        )
        for (
            slot_id,
            approach_pose,
            attach_pose,
            detach_pose,
            attach_dwell_seconds,
            detach_dwell_seconds,
        ) in (
            (
                1,
                settings.robot_tool_rack_slot_1_approach_pose,
                settings.robot_tool_rack_slot_1_attach_pose,
                settings.robot_tool_rack_slot_1_detach_pose,
                settings.robot_tool_rack_slot_1_attach_dwell_seconds,
                settings.robot_tool_rack_slot_1_detach_dwell_seconds,
            ),
            (
                2,
                settings.robot_tool_rack_slot_2_approach_pose,
                settings.robot_tool_rack_slot_2_attach_pose,
                settings.robot_tool_rack_slot_2_detach_pose,
                settings.robot_tool_rack_slot_2_attach_dwell_seconds,
                settings.robot_tool_rack_slot_2_detach_dwell_seconds,
            ),
        )
    )
    return RealManToolRackOptions(
        arm=ArmId.parse(settings.robot_tool_rack_arm),
        slots=slots,
    )


REALMAN_PROVIDER = RobotProviderDefinition(
    name="realman",
    capabilities=frozenset({
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
    }),
    create=_create_realman_robot,
)


__all__ = [
    "REALMAN_PROVIDER",
    "RealManArmConnection",
    "RealManProviderSettings",
]

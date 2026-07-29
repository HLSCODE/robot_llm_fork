from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

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
    create: Callable[[Any], RobotSystem]

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
    def from_config(cls, config: Any) -> "RealManProviderSettings":
        try:
            return cls(
                model=str(config.ROBOT_MODEL).strip(),
                left_arm=_arm_connection(config, "ROBOT1"),
                right_arm=_arm_connection(config, "ROBOT2"),
                motion=MotionOptions(
                    velocity_percent=int(config.MOVE_VELOCITY),
                    blend_radius=int(config.MOVE_RADIUS),
                    connected=_config_bool(config, "MOVE_CONNECT"),
                    blocking=_config_bool(config, "MOVE_BLOCK"),
                ),
                gripper=RealManGripperOptions(
                    pick_speed=int(config.GRIPPER_PICK_SPEED),
                    pick_force=int(config.GRIPPER_PICK_FORCE),
                    pick_timeout_s=int(config.GRIPPER_PICK_TIMEOUT),
                    release_speed=int(config.GRIPPER_RELEASE_SPEED),
                    release_timeout_s=int(config.GRIPPER_RELEASE_TIMEOUT),
                    max_attempts=int(config.MAX_ATTEMPTS),
                ),
                tool_rack=_tool_rack_options(config),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeviceInitializationError(
                f"invalid RealMan provider configuration: {exc}"
            ) from exc


def resolve_robot_provider(config: Any) -> RobotProviderDefinition:
    provider_name = str(
        getattr(config, "ROBOT_PROVIDER", "realman")
    ).strip().lower()
    try:
        return ROBOT_PROVIDERS[provider_name]
    except KeyError as exc:
        supported = ", ".join(sorted(ROBOT_PROVIDERS))
        raise DeviceInitializationError(
            f"unsupported robot provider: {provider_name}; "
            f"supported providers: {supported}"
        ) from exc


def _create_realman_robot(config: Any) -> RobotSystem:
    from ..arm_sdk import RobotController
    from ..devices import yiyeqiang_out

    if RobotController is None:
        raise DeviceInitializationError("RobotController SDK unavailable")

    settings = RealManProviderSettings.from_config(config)
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
            port=str(config.KUAIHUANSHOU_SERIAL_PORT)
        ),
    )
    try:
        adapter.read_arm_state(ArmId.LEFT)
        adapter.read_arm_state(ArmId.RIGHT)
        return adapter
    except Exception:
        adapter.close()
        raise


def _arm_connection(config: Any, prefix: str) -> RealManArmConnection:
    return RealManArmConnection(
        ip=str(getattr(config, f"{prefix}_IP")),
        port=int(getattr(config, f"{prefix}_PORT")),
        initial_pose=CartesianPose.from_iterable(
            getattr(config, f"{prefix}_INITIAL_POSE")
        ),
    )


def _config_bool(config: Any, field_name: str) -> bool:
    value = getattr(config, field_name)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{field_name} must be a boolean or one of 0/1, got {value!r}"
    )


def _tool_rack_options(config: Any) -> RealManToolRackOptions:
    slots = tuple(
        RealManToolRackSlot(
            slot_id=slot_id,
            approach_pose=_tool_rack_pose(
                config,
                slot_id,
                "APPROACH",
            ),
            attach_pose=_tool_rack_pose(
                config,
                slot_id,
                "ATTACH",
            ),
            detach_pose=_tool_rack_pose(
                config,
                slot_id,
                "DETACH",
            ),
            attach_dwell_seconds=float(
                getattr(
                    config,
                    f"ROBOT_TOOL_RACK_SLOT_{slot_id}_ATTACH_DWELL_SECONDS",
                )
            ),
            detach_dwell_seconds=float(
                getattr(
                    config,
                    f"ROBOT_TOOL_RACK_SLOT_{slot_id}_DETACH_DWELL_SECONDS",
                )
            ),
        )
        for slot_id in (1, 2)
    )
    return RealManToolRackOptions(
        arm=ArmId.parse(config.ROBOT_TOOL_RACK_ARM),
        slots=slots,
    )


def _tool_rack_pose(
    config: Any,
    slot_id: int,
    pose_name: str,
) -> CartesianPose:
    return CartesianPose.from_iterable(
        getattr(
            config,
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_{pose_name}_POSE",
        )
    )


_REALMAN_CAPABILITIES = frozenset({
    DeviceCapability.MOTION,
    DeviceCapability.QUICK_STOP,
    DeviceCapability.EMERGENCY_STOP,
    DeviceCapability.ARM_MOTION,
    DeviceCapability.ARM_STATE,
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

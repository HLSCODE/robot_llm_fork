"""Tianji-specific configuration and adapter composition."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

from ....configuration.settings import RobotConfiguration
from ...runtime.arm_models import ArmId, MotionOptions
from ...runtime.contracts import RobotSystem
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import RobotProviderDefinition
from .adapter import TianjiRobotAdapter


@dataclass(frozen=True, slots=True)
class TianjiProviderSettings:
    model: str
    controller_ip: str
    motion: MotionOptions
    subscription_interval_seconds: float
    left_base_transform: tuple[tuple[float, ...], ...]
    right_base_transform: tuple[tuple[float, ...], ...]
    left_tool_transform: tuple[tuple[float, ...], ...]
    right_tool_transform: tuple[tuple[float, ...], ...]
    joint_limits_rad: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Tianji model must not be empty")
        if not self.controller_ip.strip():
            raise ValueError("Tianji controller IP must not be empty")
        if ip_address(self.controller_ip).version != 4:
            raise ValueError("Tianji controller IP must be an IPv4 address")
        if self.subscription_interval_seconds <= 0:
            raise ValueError("Tianji subscription interval must be positive")
        for name, matrix in (
            ("left base transform", self.left_base_transform),
            ("right base transform", self.right_base_transform),
            ("left tool transform", self.left_tool_transform),
            ("right tool transform", self.right_tool_transform),
        ):
            if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
                raise ValueError(f"Tianji {name} must be a 4x4 matrix")
        if len(self.joint_limits_rad) != 7 or any(
            len(pair) != 2 for pair in self.joint_limits_rad
        ):
            raise ValueError("Tianji joint limits must contain seven min/max pairs")

    @classmethod
    def from_settings(cls, settings: RobotConfiguration) -> TianjiProviderSettings:
        common = settings.common
        provider = settings.tianji
        try:
            return cls(
                model=provider.model.strip(),
                controller_ip=provider.controller_ip.strip(),
                motion=MotionOptions(
                    velocity_percent=common.move_velocity,
                    blend_radius=common.move_radius,
                    connected=_binary_flag(common.move_connect, "MOVE_CONNECT"),
                    blocking=_binary_flag(common.move_block, "MOVE_BLOCK"),
                ),
                subscription_interval_seconds=provider.subscription_interval_seconds,
                left_base_transform=provider.left_base_transform,
                right_base_transform=provider.right_base_transform,
                left_tool_transform=provider.left_tool_transform,
                right_tool_transform=provider.right_tool_transform,
                joint_limits_rad=provider.joint_limits_rad,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeviceInitializationError(
                f"invalid Tianji provider configuration: {exc}"
            ) from exc


def _create_tianji_robot(settings: RobotConfiguration) -> RobotSystem:
    try:
        from .driver import TianjiRobotDriver
    except ImportError as exc:
        raise DeviceInitializationError("Tianji driver unavailable") from exc
    provider_settings = TianjiProviderSettings.from_settings(settings)
    try:
        driver = TianjiRobotDriver(
            provider_settings.controller_ip,
            subscription_interval_seconds=(
                provider_settings.subscription_interval_seconds
            ),
            left_base_transform=provider_settings.left_base_transform,
            right_base_transform=provider_settings.right_base_transform,
            left_tool_transform=provider_settings.left_tool_transform,
            right_tool_transform=provider_settings.right_tool_transform,
            joint_limits_rad=provider_settings.joint_limits_rad,
        )
        adapter = TianjiRobotAdapter(driver, default_motion=provider_settings.motion)
        adapter.read_arm_state(ArmId.LEFT)
        adapter.read_arm_state(ArmId.RIGHT)
        return adapter
    except Exception as exc:
        raise DeviceInitializationError(
            f"Tianji robot initialization failed: {exc}"
        ) from exc


TIANJI_PROVIDER = RobotProviderDefinition(
    name="tianji",
    capabilities=frozenset({
        DeviceCapability.MOTION,
        DeviceCapability.ARM_MOTION,
        DeviceCapability.ARM_STATE,
    }),
    create=_create_tianji_robot,
)


def _binary_flag(value: int, field_name: str) -> bool:
    if value not in (0, 1):
        raise ValueError(f"{field_name} must be 0 or 1")
    return bool(value)


__all__ = ["TIANJI_PROVIDER", "TianjiProviderSettings"]

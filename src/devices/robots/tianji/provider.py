"""Tianji-specific configuration and adapter composition."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

from ....configuration.settings import RobotSettings
from ...runtime.arm_models import ArmId, MotionOptions
from ...runtime.contracts import RobotSystem
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import RobotProviderDefinition
from .adapter import TianjiRobotAdapter


@dataclass(frozen=True, slots=True)
class TianjiProviderSettings:
    model: str
    controller_ip: str
    kinematics_config: str
    motion: MotionOptions
    acceleration_percent: int
    linear_acceleration_m_s2: float

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Tianji model must not be empty")
        if not self.controller_ip.strip():
            raise ValueError("Tianji controller IP must not be empty")
        if not self.kinematics_config.strip():
            raise ValueError("Tianji kinematics config must not be empty")
        if ip_address(self.controller_ip).version != 4:
            raise ValueError("Tianji controller IP must be an IPv4 address")
        if not 1 <= self.acceleration_percent <= 100:
            raise ValueError("Tianji acceleration percent must be in range 1..100")
        if not 0.0001 <= self.linear_acceleration_m_s2 <= 0.5:
            raise ValueError(
                "Tianji linear acceleration must be in range 0.0001..0.5 m/s²"
            )

    @classmethod
    def from_settings(cls, settings: RobotSettings) -> TianjiProviderSettings:
        try:
            return cls(
                model=settings.robot_model.strip(),
                controller_ip=settings.tianji_controller_ip.strip(),
                kinematics_config=settings.tianji_kinematics_config.strip(),
                motion=MotionOptions(
                    velocity_percent=settings.move_velocity,
                    blend_radius=settings.move_radius,
                    connected=_binary_flag(settings.move_connect, "MOVE_CONNECT"),
                    blocking=_binary_flag(settings.move_block, "MOVE_BLOCK"),
                ),
                acceleration_percent=settings.tianji_acceleration_percent,
                linear_acceleration_m_s2=settings.tianji_linear_acceleration_m_s2,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeviceInitializationError(
                f"invalid Tianji provider configuration: {exc}"
            ) from exc


def _create_tianji_robot(settings: RobotSettings) -> RobotSystem:
    try:
        from .driver import TianjiRobotDriver
    except ImportError as exc:
        raise DeviceInitializationError("Tianji driver unavailable") from exc
    provider_settings = TianjiProviderSettings.from_settings(settings)
    try:
        driver = TianjiRobotDriver(
            provider_settings.controller_ip,
            kinematics_config=provider_settings.kinematics_config,
            acceleration_percent=provider_settings.acceleration_percent,
            linear_acceleration_m_s2=provider_settings.linear_acceleration_m_s2,
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
        DeviceCapability.QUICK_STOP,
        DeviceCapability.EMERGENCY_STOP,
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

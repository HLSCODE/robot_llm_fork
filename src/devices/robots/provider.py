"""Robot provider registry independent of any vendor implementation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...core.settings import RobotSettings
from ..runtime.contracts import RobotSystem
from ..runtime.models import DeviceCapability


@dataclass(frozen=True, slots=True)
class RobotProviderDefinition:
    """One production robot provider and its truthful capabilities."""

    name: str
    capabilities: frozenset[DeviceCapability]
    create: Callable[[RobotSettings], RobotSystem]

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

__all__ = ["RobotProviderDefinition"]

"""Camera provider definitions shared by every camera implementation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...configuration.settings import VisionSettings
from ..runtime.contracts import CameraSource
from ..runtime.models import DeviceCapability


@dataclass(frozen=True, slots=True)
class CameraProviderDefinition:
    """One camera implementation and the capabilities it actually provides."""

    name: str
    capabilities: frozenset[DeviceCapability]
    create: Callable[[VisionSettings], CameraSource]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("camera provider name must not be empty")
        if DeviceCapability.CAMERA not in self.capabilities:
            raise ValueError(
                f"camera provider '{self.name}' lacks camera capability"
            )


__all__ = ["CameraProviderDefinition"]

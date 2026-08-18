"""Configured camera-provider registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ...configuration.settings import VisionSettings
from ..runtime.models import DeviceInitializationError
from .provider import CameraProviderDefinition
from .providers import OPENCV_CAMERA_PROVIDER, REALSENSE_CAMERA_PROVIDER


CAMERA_PROVIDERS: Mapping[str, CameraProviderDefinition] = MappingProxyType({
    provider.name: provider
    for provider in (REALSENSE_CAMERA_PROVIDER, OPENCV_CAMERA_PROVIDER)
})


def resolve_camera_provider(settings: VisionSettings) -> CameraProviderDefinition:
    try:
        provider_name = settings.camera_provider_name()
    except ValueError as exc:
        raise DeviceInitializationError(str(exc)) from exc
    try:
        return CAMERA_PROVIDERS[provider_name]
    except KeyError as exc:
        supported = ", ".join(sorted(CAMERA_PROVIDERS))
        raise DeviceInitializationError(
            f"unsupported camera provider: {provider_name}; "
            f"supported providers: {supported}"
        ) from exc


__all__ = ["CAMERA_PROVIDERS", "resolve_camera_provider"]

"""Configured robot-provider registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ...core.settings import RobotSettings
from ..runtime.models import DeviceInitializationError
from .provider import RobotProviderDefinition
from .realman.provider import REALMAN_PROVIDER


ROBOT_PROVIDERS: Mapping[str, RobotProviderDefinition] = MappingProxyType({
    REALMAN_PROVIDER.name: REALMAN_PROVIDER,
})


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


__all__ = ["ROBOT_PROVIDERS", "resolve_robot_provider"]

"""Composition root for the current TCP mobile-base product."""

from __future__ import annotations

from dataclasses import dataclass

from .....configuration.settings import RobotSettings
from ....runtime.contracts import MobileBase
from .adapter import TcpMobileBaseAdapter
from .client import TcpMobileBaseClient


@dataclass(frozen=True, slots=True)
class TcpMobileBaseProvider:
    name: str = "tcp"

    def create(self, settings: RobotSettings) -> MobileBase:
        adapter = TcpMobileBaseAdapter(
            TcpMobileBaseClient(
                host=settings.move_controller_host,
                port=settings.move_controller_port,
                bind_port=settings.move_controller_client_bind_port,
                timeout_seconds=settings.move_controller_timeout_seconds,
            )
        )
        adapter.connect()
        return adapter


TCP_MOBILE_BASE_PROVIDER = TcpMobileBaseProvider()

__all__ = ["TCP_MOBILE_BASE_PROVIDER", "TcpMobileBaseProvider"]

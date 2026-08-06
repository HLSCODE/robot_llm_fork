"""Minimal provider contract consumed by the application service."""

from __future__ import annotations

from typing import Protocol

from .models import ExternalLocalizationReading


class ExternalLocalizationProvider(Protocol):
    def start(self) -> None: ...
    def snapshot(self) -> ExternalLocalizationReading | None: ...
    @property
    def last_error(self) -> str | None: ...
    def close(self) -> None: ...


__all__ = ["ExternalLocalizationProvider"]

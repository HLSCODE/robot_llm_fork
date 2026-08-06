"""Application use cases for fresh external-localization readings."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from ..localization import ExternalLocalizationProvider


class ExternalLocalizationService:
    """Apply freshness and validity policy without owning transport resources."""

    def __init__(
        self,
        provider: ExternalLocalizationProvider,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep

    def latest(
        self,
        *,
        max_age: float,
        valid_only: bool = True,
        wait_timeout: float = 0.0,
    ) -> dict[str, Any] | None:
        if max_age <= 0:
            raise ValueError("external localization max age must be positive")
        if wait_timeout < 0:
            raise ValueError(
                "external localization wait timeout must not be negative"
            )
        self._provider.start()
        deadline = self._monotonic_clock() + wait_timeout
        while True:
            reading = self._provider.snapshot()
            if reading is not None:
                age = self._wall_clock() - reading.received_at
                if age <= max_age and (not valid_only or reading.valid):
                    return reading.to_mapping()
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                return None
            self._sleep(min(0.05, remaining))

    @property
    def last_error(self) -> str | None:
        return self._provider.last_error

    def close(self) -> None:
        self._provider.close()


__all__ = ["ExternalLocalizationService"]

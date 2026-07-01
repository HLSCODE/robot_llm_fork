from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExpressionSpec:
    name: str
    icon_lib: int
    icon_start: int = 0
    icon_end: int = 63


class ExpressionDisplayBackend(Protocol):
    """Uniform strategy interface implemented by each display provider."""

    @property
    def enabled(self) -> bool:
        ...

    def list_configured_expressions(self) -> list[ExpressionSpec]:
        ...

    def switch(self, expression: str | int) -> Any:
        ...

    def run_test(self) -> None:
        ...

    def close(self) -> None:
        ...


class ExpressionDisplayFactory(Protocol):
    def __call__(self, settings: Any) -> ExpressionDisplayBackend:
        ...

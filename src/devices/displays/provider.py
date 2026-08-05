"""Expression-display provider definition."""

from __future__ import annotations

from dataclasses import dataclass

from .base import ExpressionDisplayFactory


@dataclass(frozen=True, slots=True)
class ExpressionDisplayProviderDefinition:
    name: str
    create: ExpressionDisplayFactory

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("expression display provider name must not be empty")


__all__ = ["ExpressionDisplayProviderDefinition"]

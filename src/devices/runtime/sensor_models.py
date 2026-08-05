from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class BalanceReading:
    """One normalized balance observation in grams."""

    weight_g: float
    captured_at: float
    provider: str
    raw_text: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.weight_g):
            raise ValueError("balance weight must be finite")
        if self.captured_at < 0:
            raise ValueError("balance capture time must not be negative")
        if not self.provider.strip():
            raise ValueError("balance provider must not be empty")

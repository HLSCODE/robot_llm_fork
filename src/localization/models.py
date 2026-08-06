"""Typed readings for the external localization system."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


INVALID_LOCALIZATION_ID = -99


@dataclass(frozen=True, slots=True)
class ExternalLocalizationReading:
    tag_id: int
    x_cm: float
    y_cm: float
    angle_degrees: float
    received_at: float
    raw: Mapping[str, Any]

    @property
    def valid(self) -> bool:
        return self.tag_id != INVALID_LOCALIZATION_ID

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.tag_id,
            "x": self.x_cm,
            "y": self.y_cm,
            "angle": self.angle_degrees,
            "timestamp": self.received_at,
            "raw": dict(self.raw),
        }


def parse_external_localization_payload(
    payload: Mapping[str, Any],
    *,
    received_at: float,
) -> ExternalLocalizationReading:
    """Validate and normalize one UDP protocol payload."""
    return ExternalLocalizationReading(
        tag_id=int(payload.get("id", INVALID_LOCALIZATION_ID)),
        x_cm=float(payload.get("x", payload.get("X", 0.0))),
        y_cm=float(payload.get("y", payload.get("Y", 0.0))),
        angle_degrees=float(
            payload.get(
                "angle",
                payload.get(
                    "Angle",
                    payload.get("angel", payload.get("Angel", 0.0)),
                ),
            )
        ),
        received_at=received_at,
        raw=MappingProxyType(dict(payload)),
    )


__all__ = [
    "ExternalLocalizationReading",
    "INVALID_LOCALIZATION_ID",
    "parse_external_localization_payload",
]

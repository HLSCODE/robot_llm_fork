from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ..core.settings import VisionSettings


class VisionOperation(StrEnum):
    CAPTURE = "capture"
    RELOCALIZATION = "relocalization"


class VisionResultCode(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class VisionPipelineResult:
    successful: bool
    frames_processed: int
    inference_count: int

    def __post_init__(self) -> None:
        if self.frames_processed < 0:
            raise ValueError("vision frames_processed must be non-negative")
        if self.inference_count < 0:
            raise ValueError("vision inference_count must be non-negative")


@dataclass(frozen=True, slots=True)
class VisionConfigurationVersion:
    schema_version: int
    model_version: str
    calibration_version: str

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError("vision schema_version must be positive")
        if not self.model_version.strip():
            raise ValueError("vision model_version must not be empty")
        if not self.calibration_version.strip():
            raise ValueError("vision calibration_version must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "calibration_version": self.calibration_version,
        }


@dataclass(frozen=True, slots=True)
class VisionArtifact:
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class VisionResult:
    operation: VisionOperation
    code: VisionResultCode
    message: str
    artifacts: tuple[VisionArtifact, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def successful(self) -> bool:
        return self.code is VisionResultCode.SUCCEEDED


def vision_configuration(settings: VisionSettings) -> VisionConfigurationVersion:
    return VisionConfigurationVersion(
        schema_version=settings.vision_schema_version,
        model_version=settings.vision_model_version,
        calibration_version=settings.vision_calibration_version,
    )

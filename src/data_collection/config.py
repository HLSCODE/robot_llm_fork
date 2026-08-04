from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..core.settings import DataCollectionSettings
from ..devices import ArmId
from .episode_writer import DataCollectionStoragePolicy
from .schema import DataCollectionFormat


@dataclass(frozen=True, slots=True)
class DataCollectionConfig:
    fps: int
    camera_index: int
    arm_ids: tuple[ArmId, ...]
    save_path: Path
    format_variant: DataCollectionFormat
    storage_policy: DataCollectionStoragePolicy
    random_seed: int
    recording_stop_timeout_seconds: float
    maximum_sync_skew_ms: float
    camera_extrinsics: tuple[float, ...] | None
    camera_extrinsics_reference_frame: str | None
    calibration_id: str | None

    def __post_init__(self) -> None:
        if not 1 <= self.fps <= 240:
            raise ValueError("data collection fps must be in range 1..240")
        if self.camera_index < 0:
            raise ValueError("data collection camera index must not be negative")
        if not self.arm_ids or len(self.arm_ids) != len(set(self.arm_ids)):
            raise ValueError("data collection arm_ids must be non-empty and unique")
        if self.format_variant is DataCollectionFormat.RLBENCH_NATIVE and len(self.arm_ids) != 1:
            raise ValueError("rlbench_native data collection requires exactly one arm")
        if self.save_path == Path():
            raise ValueError("data collection save path must not be the current directory")
        if (
            not math.isfinite(self.recording_stop_timeout_seconds)
            or self.recording_stop_timeout_seconds <= 0
        ):
            raise ValueError("data collection recording stop timeout must be positive")
        if not math.isfinite(self.maximum_sync_skew_ms) or self.maximum_sync_skew_ms <= 0:
            raise ValueError("data collection maximum sync skew must be positive")
        has_extrinsics = self.camera_extrinsics is not None
        if has_extrinsics:
            if len(self.camera_extrinsics or ()) != 16:
                raise ValueError("data collection camera extrinsics must contain 16 values")
            if not all(math.isfinite(value) for value in self.camera_extrinsics or ()):
                raise ValueError("data collection camera extrinsics must contain finite values")
            if not _is_homogeneous_transform(self.camera_extrinsics or ()):
                raise ValueError(
                    "data collection camera extrinsics must be a homogeneous 4x4 transform"
                )
            if not self.camera_extrinsics_reference_frame:
                raise ValueError("camera extrinsics reference frame is required with extrinsics")
            if not self.calibration_id:
                raise ValueError("calibration id is required with extrinsics")
        elif self.camera_extrinsics_reference_frame or self.calibration_id:
            raise ValueError("camera calibration metadata requires camera extrinsics")

    @classmethod
    def from_settings(
        cls,
        settings: DataCollectionSettings,
    ) -> DataCollectionConfig:
        return cls(
            fps=settings.fps,
            camera_index=settings.camera_index,
            arm_ids=tuple(ArmId.parse(arm_id) for arm_id in settings.arm_ids),
            save_path=_path(settings.save_path),
            format_variant=DataCollectionFormat.parse(settings.format_variant),
            storage_policy=DataCollectionStoragePolicy(
                minimum_free_bytes=settings.minimum_free_bytes,
                overhead_factor=settings.storage_overhead_factor,
                stale_write_seconds=settings.stale_write_seconds,
            ),
            random_seed=settings.random_seed,
            recording_stop_timeout_seconds=(settings.recording_stop_timeout_seconds),
            maximum_sync_skew_ms=settings.maximum_sync_skew_ms,
            camera_extrinsics=(settings.camera_extrinsics or None),
            camera_extrinsics_reference_frame=(
                settings.camera_extrinsics_reference_frame.strip() or None
            ),
            calibration_id=(settings.calibration_id.strip() or None),
        )


def _path(raw: str) -> Path:
    normalized = raw.strip()
    if not normalized:
        raise ValueError("DATA_COLLECTION_SAVE_PATH must not be empty")
    return Path(normalized)


def _is_homogeneous_transform(values: tuple[float, ...]) -> bool:
    return all(
        math.isclose(actual, expected, abs_tol=1e-9)
        for actual, expected in zip(values[12:], (0.0, 0.0, 0.0, 1.0))
    )

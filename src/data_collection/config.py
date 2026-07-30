from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..device_runtime import ArmId
from .episode_writer import DataCollectionStoragePolicy
from .schema import DataCollectionFormat

_PROJECT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.env"


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
        if (
            self.format_variant is DataCollectionFormat.RLBENCH_NATIVE
            and len(self.arm_ids) != 1
        ):
            raise ValueError("rlbench_native data collection requires exactly one arm")
        if self.save_path == Path():
            raise ValueError(
                "data collection save path must not be the current directory"
            )
        if (
            not math.isfinite(self.recording_stop_timeout_seconds)
            or self.recording_stop_timeout_seconds <= 0
        ):
            raise ValueError("data collection recording stop timeout must be positive")
        if (
            not math.isfinite(self.maximum_sync_skew_ms)
            or self.maximum_sync_skew_ms <= 0
        ):
            raise ValueError("data collection maximum sync skew must be positive")
        has_extrinsics = self.camera_extrinsics is not None
        if has_extrinsics:
            if len(self.camera_extrinsics or ()) != 16:
                raise ValueError(
                    "data collection camera extrinsics must contain 16 values"
                )
            if not all(math.isfinite(value) for value in self.camera_extrinsics or ()):
                raise ValueError(
                    "data collection camera extrinsics must contain finite values"
                )
            if not _is_homogeneous_transform(self.camera_extrinsics or ()):
                raise ValueError(
                    "data collection camera extrinsics must be a homogeneous "
                    "4x4 transform"
                )
            if not self.camera_extrinsics_reference_frame:
                raise ValueError(
                    "camera extrinsics reference frame is required with extrinsics"
                )
            if not self.calibration_id:
                raise ValueError("calibration id is required with extrinsics")
        elif self.camera_extrinsics_reference_frame or self.calibration_id:
            raise ValueError("camera calibration metadata requires camera extrinsics")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_project_dotenv: bool = True,
    ) -> DataCollectionConfig:
        if load_project_dotenv:
            load_dotenv(_PROJECT_CONFIG_PATH)
        source = os.environ if environ is None else environ
        return cls(
            fps=_integer(source, "DATA_COLLECTION_FPS", 30),
            camera_index=_integer(source, "DATA_COLLECTION_CAMERA_INDEX", 0),
            arm_ids=_arm_ids(source.get("DATA_COLLECTION_ARMS", "left,right")),
            save_path=_path(
                source,
                "DATA_COLLECTION_SAVE_PATH",
                Path("data/demos"),
            ),
            format_variant=DataCollectionFormat.parse(
                source.get(
                    "DATA_COLLECTION_FORMAT_VARIANT",
                    DataCollectionFormat.PORTABLE_SIMPLIFIED.value,
                )
            ),
            storage_policy=DataCollectionStoragePolicy(
                minimum_free_bytes=_integer(
                    source,
                    "DATA_COLLECTION_MIN_FREE_BYTES",
                    1_073_741_824,
                ),
                overhead_factor=_floating_point(
                    source,
                    "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR",
                    1.25,
                ),
                stale_write_seconds=_floating_point(
                    source,
                    "DATA_COLLECTION_STALE_WRITE_SECONDS",
                    3600.0,
                ),
            ),
            random_seed=_integer(source, "DATA_COLLECTION_RANDOM_SEED", 42),
            recording_stop_timeout_seconds=_floating_point(
                source,
                "DATA_COLLECTION_STOP_TIMEOUT_SECONDS",
                5.0,
            ),
            maximum_sync_skew_ms=_floating_point(
                source,
                "DATA_COLLECTION_MAX_SYNC_SKEW_MS",
                100.0,
            ),
            camera_extrinsics=_optional_float_tuple(
                source,
                "DATA_COLLECTION_CAMERA_EXTRINSICS",
                expected_length=16,
            ),
            camera_extrinsics_reference_frame=_optional_string(
                source,
                "DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME",
            ),
            calibration_id=_optional_string(
                source,
                "DATA_COLLECTION_CALIBRATION_ID",
            ),
        )


def _arm_ids(raw: str) -> tuple[ArmId, ...]:
    values = tuple(ArmId.parse(item) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("DATA_COLLECTION_ARMS must contain at least one arm")
    return values


def _integer(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _floating_point(
    source: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _path(source: Mapping[str, str], name: str, default: Path) -> Path:
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return Path(normalized)


def _optional_string(source: Mapping[str, str], name: str) -> str | None:
    raw = source.get(name)
    if raw is None:
        return None
    normalized = raw.strip()
    return normalized or None


def _optional_float_tuple(
    source: Mapping[str, str],
    name: str,
    *,
    expected_length: int,
) -> tuple[float, ...] | None:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        values = tuple(float(item.strip()) for item in raw.split(","))
    except ValueError as exc:
        raise ValueError(f"{name} must contain comma-separated numbers") from exc
    if len(values) != expected_length:
        raise ValueError(f"{name} must contain exactly {expected_length} values")
    return values


def _is_homogeneous_transform(values: tuple[float, ...]) -> bool:
    return all(
        math.isclose(actual, expected, abs_tol=1e-9)
        for actual, expected in zip(values[12:], (0.0, 0.0, 0.0, 1.0))
    )

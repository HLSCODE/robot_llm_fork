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
    arm_id: ArmId
    save_path: Path
    format_variant: DataCollectionFormat
    storage_policy: DataCollectionStoragePolicy
    random_seed: int
    recording_stop_timeout_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.fps <= 240:
            raise ValueError("data collection fps must be in range 1..240")
        if self.camera_index < 0:
            raise ValueError("data collection camera index must not be negative")
        if self.save_path == Path():
            raise ValueError(
                "data collection save path must not be the current directory"
            )
        if (
            not math.isfinite(self.recording_stop_timeout_seconds)
            or self.recording_stop_timeout_seconds <= 0
        ):
            raise ValueError("data collection recording stop timeout must be positive")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_project_dotenv: bool = True,
    ) -> "DataCollectionConfig":
        if load_project_dotenv:
            load_dotenv(_PROJECT_CONFIG_PATH)
        source = os.environ if environ is None else environ
        return cls(
            fps=_integer(source, "DATA_COLLECTION_FPS", 30),
            camera_index=_integer(
                source,
                "DATA_COLLECTION_CAMERA_INDEX",
                0,
            ),
            arm_id=ArmId.parse(
                source.get(
                    "DATA_COLLECTION_ARM",
                    ArmId.LEFT.value,
                ),
            ),
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
            random_seed=_integer(
                source,
                "DATA_COLLECTION_RANDOM_SEED",
                42,
            ),
            recording_stop_timeout_seconds=_floating_point(
                source,
                "DATA_COLLECTION_STOP_TIMEOUT_SECONDS",
                5.0,
            ),
        )


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
) -> int:
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


def _path(
    source: Mapping[str, str],
    name: str,
    default: Path,
) -> Path:
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return Path(normalized)

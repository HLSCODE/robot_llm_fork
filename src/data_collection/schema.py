from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Protocol

import numpy as np

DATA_COLLECTION_SCHEMA_NAME = "robot-llm.data-collection.episode"
DATA_COLLECTION_SCHEMA_VERSION = 1
EPISODE_FORMAT_VERSION = 1
EPISODE_METADATA_FILENAME = "episode.json"
PORTABLE_LOW_DIM_FILENAME = "low_dim_obs.npz"
NATIVE_LOW_DIM_FILENAME = "low_dim_obs.pkl"
VARIATION_NUMBER_FILENAME = "variation_number.pkl"
VARIATION_DESCRIPTIONS_FILENAME = "variation_descriptions.pkl"

_TASK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DataCollectionFormat(str, Enum):
    """Supported on-disk episode representations."""

    PORTABLE_SIMPLIFIED = "portable_simplified"
    RLBENCH_NATIVE = "rlbench_native"

    @classmethod
    def parse(cls, value: str) -> "DataCollectionFormat":
        try:
            return cls(value.strip().lower())
        except (AttributeError, ValueError) as exc:
            supported = ", ".join(item.value for item in cls)
            raise ValueError(
                f"unsupported data collection format {value!r}; "
                f"expected one of: {supported}"
            ) from exc


class FrameRecord(Protocol):
    timestamp: float
    front_rgb: np.ndarray | None
    front_depth: np.ndarray | None
    camera_intrinsics: np.ndarray | None
    joint_positions: np.ndarray | None
    joint_velocities: np.ndarray | None
    gripper_open: float
    gripper_pose: np.ndarray | None
    joint_forces: np.ndarray | None
    gripper_matrix: np.ndarray | None
    gripper_joint_positions: np.ndarray | None


@dataclass(frozen=True, slots=True)
class EpisodeFile:
    path: str
    role: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "role": self.role,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EpisodeFile":
        path = _required_string(raw, "path")
        role = _required_string(raw, "role")
        size_bytes = _required_nonnegative_int(raw, "size_bytes")
        sha256 = _required_string(raw, "sha256")
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise ValueError("episode file sha256 must be 64 lowercase hex characters")
        return cls(
            path=path,
            role=role,
            size_bytes=size_bytes,
            sha256=sha256,
        )


@dataclass(frozen=True, slots=True)
class EpisodeMetadata:
    format_variant: DataCollectionFormat
    task: str
    source_arm: str
    episode_id: int
    variation_id: int
    descriptions: tuple[str, ...]
    frame_count: int
    created_at_utc: str
    fields: Mapping[str, str]
    units: Mapping[str, str]
    dimensions: Mapping[str, tuple[int, ...]]
    files: tuple[EpisodeFile, ...]
    capture_error_count: int = 0
    schema_name: str = DATA_COLLECTION_SCHEMA_NAME
    schema_version: int = DATA_COLLECTION_SCHEMA_VERSION
    format_version: int = EPISODE_FORMAT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "format_variant": self.format_variant.value,
            "format_version": self.format_version,
            "task": self.task,
            "source_arm": self.source_arm,
            "episode_id": self.episode_id,
            "variation_id": self.variation_id,
            "descriptions": list(self.descriptions),
            "frame_count": self.frame_count,
            "capture_error_count": self.capture_error_count,
            "created_at_utc": self.created_at_utc,
            "fields": dict(sorted(self.fields.items())),
            "units": dict(sorted(self.units.items())),
            "dimensions": {
                name: list(shape) for name, shape in sorted(self.dimensions.items())
            },
            "files": [item.to_dict() for item in self.files],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EpisodeMetadata":
        schema_name = _required_string(raw, "schema_name")
        if schema_name != DATA_COLLECTION_SCHEMA_NAME:
            raise ValueError(f"unsupported schema name: {schema_name}")
        schema_version = _required_nonnegative_int(raw, "schema_version")
        if schema_version != DATA_COLLECTION_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema version: {schema_version}; "
                f"expected {DATA_COLLECTION_SCHEMA_VERSION}"
            )
        format_version = _required_nonnegative_int(raw, "format_version")
        if format_version != EPISODE_FORMAT_VERSION:
            raise ValueError(
                f"unsupported format version: {format_version}; "
                f"expected {EPISODE_FORMAT_VERSION}"
            )

        descriptions_raw = raw.get("descriptions")
        if not isinstance(descriptions_raw, list) or not all(
            isinstance(item, str) for item in descriptions_raw
        ):
            raise ValueError("episode descriptions must be a list of strings")

        fields = _string_mapping(raw, "fields")
        units = _string_mapping(raw, "units")
        dimensions_raw = raw.get("dimensions")
        if not isinstance(dimensions_raw, Mapping):
            raise ValueError("episode dimensions must be an object")
        dimensions = {
            str(name): _shape(value, str(name))
            for name, value in dimensions_raw.items()
        }

        files_raw = raw.get("files")
        if not isinstance(files_raw, list):
            raise ValueError("episode files must be a list")
        files = tuple(
            EpisodeFile.from_dict(item)
            if isinstance(item, Mapping)
            else _raise_invalid_file_entry()
            for item in files_raw
        )
        paths = [item.path for item in files]
        if len(paths) != len(set(paths)):
            raise ValueError("episode file paths must be unique")

        return cls(
            schema_name=schema_name,
            schema_version=schema_version,
            format_variant=DataCollectionFormat.parse(
                _required_string(raw, "format_variant")
            ),
            format_version=format_version,
            task=validate_task_name(_required_string(raw, "task")),
            source_arm=validate_source_arm(_required_string(raw, "source_arm")),
            episode_id=_required_nonnegative_int(raw, "episode_id"),
            variation_id=_required_nonnegative_int(raw, "variation_id"),
            descriptions=tuple(descriptions_raw),
            frame_count=_required_nonnegative_int(raw, "frame_count"),
            capture_error_count=_required_nonnegative_int(
                raw,
                "capture_error_count",
            ),
            created_at_utc=_utc_timestamp(raw),
            fields=fields,
            units=units,
            dimensions=dimensions,
            files=files,
        )


def validate_task_name(task: str) -> str:
    normalized = task.strip()
    if not _TASK_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "task must be 1-128 ASCII letters, digits, dot, underscore or "
            "hyphen, and must start with a letter or digit"
        )
    if normalized in {".", ".."}:
        raise ValueError("task must not be a relative path segment")
    return normalized


def validate_source_arm(source_arm: str) -> str:
    normalized = source_arm.strip().lower()
    if normalized not in {"left", "right"}:
        raise ValueError("source_arm must be either 'left' or 'right'")
    return normalized


def normalize_descriptions(description: str) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in description.split(",") if item.strip())
    return normalized


def _required_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"episode field '{field}' must be a non-empty string")
    return value


def _required_nonnegative_int(raw: Mapping[str, Any], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"episode field '{field}' must be a non-negative integer")
    return value


def _string_mapping(
    raw: Mapping[str, Any],
    field: str,
) -> dict[str, str]:
    value = raw.get(field)
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"episode field '{field}' must be a string mapping")
    return dict(value)


def _utc_timestamp(raw: Mapping[str, Any]) -> str:
    value = _required_string(raw, "created_at_utc")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("created_at_utc must be an ISO-8601 timestamp") from exc
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError("created_at_utc must include an explicit UTC offset")
    return value


def _shape(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"dimension '{name}' must be an integer list")
    shape: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"dimension '{name}' must contain non-negative integers")
        shape.append(item)
    return tuple(shape)


def _raise_invalid_file_entry() -> EpisodeFile:
    raise ValueError("episode file entries must be objects")

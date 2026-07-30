from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Protocol

import numpy as np

DATA_COLLECTION_SCHEMA_NAME = "robot-llm.data-collection.episode"
DATA_COLLECTION_SCHEMA_VERSION = 2
EPISODE_FORMAT_VERSION = 2
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
    def parse(cls, value: str) -> DataCollectionFormat:
        try:
            return cls(value.strip().lower())
        except (AttributeError, ValueError) as exc:
            supported = ", ".join(item.value for item in cls)
            raise ValueError(
                f"unsupported data collection format {value!r}; "
                f"expected one of: {supported}"
            ) from exc


class ArmFrameRecord(Protocol):
    sampled_at_utc_ns: int
    sampled_at_monotonic_ns: int
    joint_positions: np.ndarray
    joint_velocities: np.ndarray | None
    joint_currents: np.ndarray | None
    gripper_open: float
    gripper_force_newtons: float
    gripper_raw_position: int
    gripper_pose: np.ndarray
    end_effector_wrench: np.ndarray | None


class FrameRecord(Protocol):
    timestamp_utc_ns: int
    camera_received_at_monotonic_ns: int
    front_rgb: np.ndarray
    front_depth: np.ndarray
    camera_intrinsics: np.ndarray
    camera_distortion_coefficients: np.ndarray
    depth_scale_metres: float
    color_hardware_timestamp_ms: float
    depth_hardware_timestamp_ms: float
    color_frame_number: int
    depth_frame_number: int
    sample_sync_skew_ms: float
    camera_name: str
    camera_serial: str
    camera_distortion_model: str
    camera_hardware_timestamp_domain: str
    depth_aligned_to_color: bool
    arms: Mapping[str, ArmFrameRecord]


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
    def from_dict(cls, raw: Mapping[str, Any]) -> EpisodeFile:
        path = _required_string(raw, "path")
        role = _required_string(raw, "role")
        size_bytes = _required_nonnegative_int(raw, "size_bytes")
        sha256 = _required_string(raw, "sha256")
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise ValueError("episode file sha256 must be 64 lowercase hex characters")
        return cls(path=path, role=role, size_bytes=size_bytes, sha256=sha256)


@dataclass(frozen=True, slots=True)
class EpisodeMetadata:
    format_variant: DataCollectionFormat
    task: str
    source_arms: tuple[str, ...]
    episode_id: int
    variation_id: int
    descriptions: tuple[str, ...]
    frame_count: int
    created_at_utc: str
    fields: Mapping[str, str]
    units: Mapping[str, str]
    dimensions: Mapping[str, tuple[int, ...]]
    files: tuple[EpisodeFile, ...]
    camera_name: str
    camera_serial: str
    camera_distortion_model: str
    camera_hardware_timestamp_domain: str
    depth_aligned_to_color: bool
    maximum_sync_skew_ms: float
    observed_maximum_sync_skew_ms: float
    camera_extrinsics: tuple[float, ...] | None = None
    camera_extrinsics_reference_frame: str | None = None
    calibration_id: str | None = None
    capture_error_count: int = 0
    schema_name: str = DATA_COLLECTION_SCHEMA_NAME
    schema_version: int = DATA_COLLECTION_SCHEMA_VERSION
    format_version: int = EPISODE_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_source_arms(self.source_arms)
        _validate_finite_nonnegative(
            self.maximum_sync_skew_ms,
            "maximum_sync_skew_ms",
        )
        if self.maximum_sync_skew_ms == 0:
            raise ValueError("maximum_sync_skew_ms must be positive")
        _validate_finite_nonnegative(
            self.observed_maximum_sync_skew_ms,
            "observed_maximum_sync_skew_ms",
        )
        if self.observed_maximum_sync_skew_ms > self.maximum_sync_skew_ms:
            raise ValueError("observed sync skew exceeds configured maximum")
        has_extrinsics = self.camera_extrinsics is not None
        if has_extrinsics:
            if len(self.camera_extrinsics or ()) != 16:
                raise ValueError("camera_extrinsics must contain 16 values")
            if not all(math.isfinite(value) for value in self.camera_extrinsics or ()):
                raise ValueError("camera_extrinsics must contain finite values")
            if not all(
                math.isclose(actual, expected, abs_tol=1e-9)
                for actual, expected in zip(
                    (self.camera_extrinsics or ())[12:],
                    (0.0, 0.0, 0.0, 1.0),
                )
            ):
                raise ValueError(
                    "camera_extrinsics must be a homogeneous 4x4 transform"
                )
            if not self.camera_extrinsics_reference_frame:
                raise ValueError(
                    "camera_extrinsics_reference_frame is required with extrinsics"
                )
            if not self.calibration_id:
                raise ValueError("calibration_id is required with extrinsics")
        elif self.camera_extrinsics_reference_frame or self.calibration_id:
            raise ValueError("camera calibration metadata requires camera_extrinsics")

    def to_dict(self) -> dict[str, object]:
        calibration: dict[str, object] | None = None
        if self.camera_extrinsics is not None:
            calibration = {
                "camera_extrinsics": [
                    list(self.camera_extrinsics[index : index + 4])
                    for index in range(0, 16, 4)
                ],
                "reference_frame": self.camera_extrinsics_reference_frame,
                "calibration_id": self.calibration_id,
            }
        return {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "format_variant": self.format_variant.value,
            "format_version": self.format_version,
            "task": self.task,
            "source_arms": list(self.source_arms),
            "episode_id": self.episode_id,
            "variation_id": self.variation_id,
            "descriptions": list(self.descriptions),
            "frame_count": self.frame_count,
            "capture_error_count": self.capture_error_count,
            "created_at_utc": self.created_at_utc,
            "camera": {
                "name": self.camera_name,
                "serial": self.camera_serial,
                "distortion_model": self.camera_distortion_model,
                "hardware_timestamp_domain": (self.camera_hardware_timestamp_domain),
                "depth_aligned_to_color": self.depth_aligned_to_color,
            },
            "synchronization": {
                "maximum_skew_ms": self.maximum_sync_skew_ms,
                "observed_maximum_skew_ms": self.observed_maximum_sync_skew_ms,
            },
            "calibration": calibration,
            "fields": dict(sorted(self.fields.items())),
            "units": dict(sorted(self.units.items())),
            "dimensions": {
                name: list(shape) for name, shape in sorted(self.dimensions.items())
            },
            "files": [item.to_dict() for item in self.files],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EpisodeMetadata:
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

        source_arms_raw = raw.get("source_arms")
        if not isinstance(source_arms_raw, list):
            raise TypeError("source_arms must be a list")
        source_arms = validate_source_arms(source_arms_raw)
        descriptions_raw = raw.get("descriptions")
        if not isinstance(descriptions_raw, list) or not all(
            isinstance(item, str) for item in descriptions_raw
        ):
            raise ValueError("episode descriptions must be a list of strings")
        camera = _required_mapping(raw, "camera")
        synchronization = _required_mapping(raw, "synchronization")
        calibration_raw = raw.get("calibration")
        extrinsics: tuple[float, ...] | None = None
        reference_frame: str | None = None
        calibration_id: str | None = None
        if calibration_raw is not None:
            if not isinstance(calibration_raw, Mapping):
                raise ValueError("calibration must be an object or null")
            matrix = np.asarray(
                calibration_raw.get("camera_extrinsics"),
                dtype=np.float64,
            )
            if matrix.shape != (4, 4):
                raise ValueError("camera_extrinsics must have shape 4x4")
            extrinsics = tuple(float(value) for value in matrix.reshape(-1))
            reference_frame = _required_string(calibration_raw, "reference_frame")
            calibration_id = _required_string(calibration_raw, "calibration_id")

        dimensions_raw = _required_mapping(raw, "dimensions")
        files_raw = raw.get("files")
        if not isinstance(files_raw, list):
            raise TypeError("episode files must be a list")
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
            source_arms=source_arms,
            episode_id=_required_nonnegative_int(raw, "episode_id"),
            variation_id=_required_nonnegative_int(raw, "variation_id"),
            descriptions=tuple(descriptions_raw),
            frame_count=_required_nonnegative_int(raw, "frame_count"),
            capture_error_count=_required_nonnegative_int(
                raw,
                "capture_error_count",
            ),
            created_at_utc=_utc_timestamp(raw),
            fields=_string_mapping(raw, "fields"),
            units=_string_mapping(raw, "units"),
            dimensions={
                str(name): _shape(value, str(name))
                for name, value in dimensions_raw.items()
            },
            files=files,
            camera_name=_required_string(camera, "name"),
            camera_serial=_string(camera, "serial"),
            camera_distortion_model=_required_string(camera, "distortion_model"),
            camera_hardware_timestamp_domain=_required_string(
                camera,
                "hardware_timestamp_domain",
            ),
            depth_aligned_to_color=_required_bool(camera, "depth_aligned_to_color"),
            maximum_sync_skew_ms=_required_number(
                synchronization,
                "maximum_skew_ms",
            ),
            observed_maximum_sync_skew_ms=_required_number(
                synchronization,
                "observed_maximum_skew_ms",
            ),
            camera_extrinsics=extrinsics,
            camera_extrinsics_reference_frame=reference_frame,
            calibration_id=calibration_id,
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
        raise ValueError("source arm must be either 'left' or 'right'")
    return normalized


def validate_source_arms(source_arms: Sequence[object]) -> tuple[str, ...]:
    normalized = tuple(validate_source_arm(str(item)) for item in source_arms)
    if not normalized:
        raise ValueError("source_arms must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError("source_arms must not contain duplicates")
    return normalized


def normalize_descriptions(description: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in description.split(",") if item.strip())


def _required_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"episode field '{field}' must be a non-empty string")
    return value


def _string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise TypeError(f"episode field '{field}' must be a string")
    return value


def _required_nonnegative_int(raw: Mapping[str, Any], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"episode field '{field}' must be a non-negative integer")
    return value


def _required_number(raw: Mapping[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"episode field '{field}' must be a number")
    number = float(value)
    _validate_finite_nonnegative(number, field)
    return number


def _required_bool(raw: Mapping[str, Any], field: str) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        raise TypeError(f"episode field '{field}' must be a boolean")
    return value


def _required_mapping(
    raw: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    value = raw.get(field)
    if not isinstance(value, Mapping):
        raise TypeError(f"episode field '{field}' must be an object")
    return value


def _string_mapping(raw: Mapping[str, Any], field: str) -> dict[str, str]:
    value = _required_mapping(raw, field)
    if not all(
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
        raise TypeError(f"dimension '{name}' must be an integer list")
    shape: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"dimension '{name}' must contain non-negative integers")
        shape.append(item)
    return tuple(shape)


def _validate_finite_nonnegative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _raise_invalid_file_entry() -> EpisodeFile:
    raise ValueError("episode file entries must be objects")

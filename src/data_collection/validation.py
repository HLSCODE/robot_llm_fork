from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np

from .schema import (
    EPISODE_METADATA_FILENAME,
    NATIVE_LOW_DIM_FILENAME,
    PORTABLE_LOW_DIM_FILENAME,
    VARIATION_DESCRIPTIONS_FILENAME,
    VARIATION_NUMBER_FILENAME,
    DataCollectionFormat,
    EpisodeMetadata,
    validate_task_name,
)

_EPISODE_DIRECTORY_PATTERN = re.compile(r"^episode([0-9]+)$")
_INCOMPLETE_DIRECTORY_PATTERN = re.compile(r"^\.episode([0-9]+)\.tmp-[A-Fa-f0-9]+$")


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class EpisodeValidationReport:
    episode_path: Path
    metadata: EpisodeMetadata | None
    checked_files: int
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_path": str(self.episode_path),
            "valid": self.valid,
            "checked_files": self.checked_files,
            "metadata": (
                self.metadata.to_dict() if self.metadata is not None else None
            ),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class DatasetValidationReport:
    dataset_path: Path
    episodes: tuple[EpisodeValidationReport, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return all(episode.valid for episode in self.episodes) and not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_path": str(self.dataset_path),
            "valid": self.valid,
            "episode_count": len(self.episodes),
            "issues": [issue.to_dict() for issue in self.issues],
            "episodes": [episode.to_dict() for episode in self.episodes],
        }


def validate_episode(
    episode_path: str | Path,
    *,
    verify_checksums: bool = True,
    require_canonical_name: bool = True,
    trusted_native: bool = False,
) -> EpisodeValidationReport:
    path = Path(episode_path)
    issues: list[ValidationIssue] = []
    if not path.is_dir():
        return EpisodeValidationReport(
            episode_path=path,
            metadata=None,
            checked_files=0,
            issues=(
                _error("episode_not_found", "episode path is not a directory", path),
            ),
        )

    metadata_path = path / EPISODE_METADATA_FILENAME
    try:
        raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(raw_metadata, dict):
            raise TypeError("episode metadata root must be an object")
        metadata = EpisodeMetadata.from_dict(raw_metadata)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        return EpisodeValidationReport(
            episode_path=path,
            metadata=None,
            checked_files=0,
            issues=(
                _error(
                    "metadata_invalid",
                    f"unable to load episode metadata: {exc}",
                    metadata_path,
                ),
            ),
        )

    if require_canonical_name:
        match = _EPISODE_DIRECTORY_PATTERN.fullmatch(path.name)
        if match is None or int(match.group(1)) != metadata.episode_id:
            issues.append(
                _error(
                    "episode_directory_mismatch",
                    "episode directory name does not match metadata episode_id",
                    path,
                )
            )

    checked_files = _validate_manifest_files(
        path,
        metadata,
        issues,
        verify_checksums=verify_checksums,
    )
    _validate_required_images(path, metadata, issues)
    _validate_metadata_contract(path, metadata, issues)
    _validate_format_payload(
        path,
        metadata,
        issues,
        trusted_native=trusted_native,
    )
    if metadata.camera_extrinsics is None:
        issues.append(
            _warning(
                "camera_extrinsics_absent",
                "camera extrinsics were not supplied; robot-frame projection is unavailable",
                metadata_path,
            )
        )
    if metadata.capture_error_count:
        issues.append(
            _warning(
                "capture_errors_recorded",
                f"recorder reported {metadata.capture_error_count} capture errors",
                path,
            )
        )
    return EpisodeValidationReport(
        episode_path=path,
        metadata=metadata,
        checked_files=checked_files,
        issues=tuple(issues),
    )


def validate_dataset(
    dataset_path: str | Path,
    *,
    task: str | None = None,
    verify_checksums: bool = True,
    trusted_native: bool = False,
) -> DatasetValidationReport:
    root = Path(dataset_path)
    issues: list[ValidationIssue] = []
    episodes: list[EpisodeValidationReport] = []
    if not root.is_dir():
        return DatasetValidationReport(
            dataset_path=root,
            episodes=(),
            issues=(
                _error("dataset_not_found", "dataset path is not a directory", root),
            ),
        )
    if task is not None:
        task_path = root / validate_task_name(task)
        if not task_path.is_dir():
            return DatasetValidationReport(
                dataset_path=root,
                episodes=(),
                issues=(
                    _error(
                        "task_not_found",
                        f"requested task does not exist: {task}",
                        task_path,
                    ),
                ),
            )
        task_directories = (task_path,)
    else:
        task_directories = tuple(
            path for path in sorted(root.iterdir()) if path.is_dir()
        )

    for task_path in task_directories:
        episodes_path = task_path / "all_variations" / "episodes"
        if not episodes_path.is_dir():
            issues.append(
                _warning(
                    "episodes_directory_missing",
                    "task has no all_variations/episodes directory",
                    episodes_path,
                )
            )
            continue
        for candidate in sorted(episodes_path.iterdir()):
            if not candidate.is_dir():
                continue
            if _EPISODE_DIRECTORY_PATTERN.fullmatch(candidate.name):
                episodes.append(
                    validate_episode(
                        candidate,
                        verify_checksums=verify_checksums,
                        trusted_native=trusted_native,
                    )
                )
            elif _INCOMPLETE_DIRECTORY_PATTERN.fullmatch(candidate.name):
                issues.append(
                    _warning(
                        "incomplete_write_present",
                        "temporary episode directory has not been published",
                        candidate,
                    )
                )
    if not episodes:
        issues.append(
            _error(
                "no_episodes",
                "dataset does not contain any published episodes",
                root,
            )
        )
    return DatasetValidationReport(
        dataset_path=root,
        episodes=tuple(episodes),
        issues=tuple(issues),
    )


def _validate_manifest_files(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
    *,
    verify_checksums: bool,
) -> int:
    checked_files = 0
    listed_paths: set[str] = set()
    for item in metadata.files:
        listed_paths.add(item.path)
        file_path = _resolve_episode_file(path, item.path, issues)
        if file_path is None:
            continue
        if not file_path.is_file():
            issues.append(_error("file_missing", "manifest file is missing", file_path))
            continue
        checked_files += 1
        if file_path.stat().st_size != item.size_bytes:
            issues.append(
                _error(
                    "file_size_mismatch",
                    f"expected {item.size_bytes} bytes",
                    file_path,
                )
            )
        if verify_checksums and _sha256(file_path) != item.sha256:
            issues.append(
                _error(
                    "checksum_mismatch",
                    "file SHA-256 does not match episode metadata",
                    file_path,
                )
            )
    actual_paths = {
        file_path.relative_to(path).as_posix()
        for file_path in path.rglob("*")
        if file_path.is_file() and file_path.name != EPISODE_METADATA_FILENAME
    }
    for unlisted in sorted(actual_paths - listed_paths):
        issues.append(
            _error(
                "unlisted_file",
                "episode contains a file not declared in metadata",
                path / unlisted,
            )
        )
    return checked_files


def _validate_required_images(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    if metadata.frame_count <= 0:
        issues.append(_error("empty_episode", "episode must contain frames", path))
        return
    for index in range(metadata.frame_count):
        _validate_image(
            path / "front_rgb" / f"{index}.png",
            metadata.dimensions.get("front_rgb"),
            "front_rgb",
            issues,
        )
        _validate_image(
            path / "front_depth" / f"{index}.png",
            metadata.dimensions.get("front_depth"),
            "front_depth",
            issues,
        )


def _validate_metadata_contract(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    required = {
        "timestamp_utc_ns",
        "front_rgb",
        "front_depth",
        "camera_intrinsics",
        "camera_distortion_coefficients",
        "depth_scale_metres",
        "camera_hardware_timestamps",
        "camera_frame_numbers",
        "sample_sync_skew_ms",
    }
    for arm_name in metadata.source_arms:
        prefix = f"{arm_name}_"
        required.update(
            {
                f"{prefix}sampled_at_utc_ns",
                f"{prefix}sampled_at_monotonic_ns",
                f"{prefix}joint_positions",
                f"{prefix}gripper_open",
                f"{prefix}gripper_force_newtons",
                f"{prefix}gripper_raw_position",
                f"{prefix}gripper_pose",
            }
        )
    for field in sorted(required):
        if metadata.fields.get(field) != "required":
            issues.append(
                _error(
                    "required_field_not_declared",
                    f"metadata must declare {field} as required",
                    path / EPISODE_METADATA_FILENAME,
                )
            )
    for field, state in metadata.fields.items():
        if state not in {"required", "present", "absent"}:
            issues.append(
                _error(
                    "field_state_invalid",
                    f"metadata field {field} has invalid state {state!r}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )
        if field not in metadata.units:
            issues.append(
                _error(
                    "field_unit_missing",
                    f"metadata does not declare units for {field}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )
    dimension_fields = {
        "front_rgb",
        "front_depth",
        "camera_intrinsics",
        "camera_distortion_coefficients",
    }
    for arm_name in metadata.source_arms:
        dimension_fields.update(
            {
                f"{arm_name}_joint_positions",
                f"{arm_name}_gripper_pose",
            }
        )
    for field in sorted(dimension_fields):
        if not metadata.dimensions.get(field):
            issues.append(
                _error(
                    "field_dimensions_missing",
                    f"metadata does not declare dimensions for {field}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )


def _validate_format_payload(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
    *,
    trusted_native: bool,
) -> None:
    if metadata.format_variant is DataCollectionFormat.PORTABLE_SIMPLIFIED:
        _validate_portable_payload(path, metadata, issues)
        for filename in (
            NATIVE_LOW_DIM_FILENAME,
            VARIATION_NUMBER_FILENAME,
            VARIATION_DESCRIPTIONS_FILENAME,
        ):
            if (path / filename).exists():
                issues.append(
                    _error(
                        "format_payload_mixed",
                        "portable episode contains native payload",
                        path / filename,
                    )
                )
        return

    if len(metadata.source_arms) != 1:
        issues.append(
            _error(
                "native_arm_count_invalid",
                "native RLBench episodes require exactly one source arm",
                path,
            )
        )
    for filename in (
        NATIVE_LOW_DIM_FILENAME,
        VARIATION_NUMBER_FILENAME,
        VARIATION_DESCRIPTIONS_FILENAME,
    ):
        if not (path / filename).is_file():
            issues.append(
                _error(
                    "native_payload_missing",
                    "native RLBench payload file is missing",
                    path / filename,
                )
            )
    if (path / PORTABLE_LOW_DIM_FILENAME).exists():
        issues.append(
            _error(
                "format_payload_mixed",
                "native RLBench episode contains portable payload",
                path / PORTABLE_LOW_DIM_FILENAME,
            )
        )
    if trusted_native:
        _validate_trusted_native_payload(path, metadata, issues)
    else:
        issues.append(
            _warning(
                "native_pickle_not_inspected",
                "native pickle was not loaded; use --trusted-native only for trusted data",
                path,
            )
        )


def _validate_portable_payload(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    low_dim_path = path / PORTABLE_LOW_DIM_FILENAME
    if not low_dim_path.is_file():
        issues.append(
            _error(
                "portable_payload_missing",
                "portable low-dimensional NPZ payload is missing",
                low_dim_path,
            )
        )
        return
    required_shapes: dict[str, tuple[int, ...]] = {
        "timestamps_utc_ns": (metadata.frame_count,),
        "camera_received_at_monotonic_ns": (metadata.frame_count,),
        "color_hardware_timestamps_ms": (metadata.frame_count,),
        "depth_hardware_timestamps_ms": (metadata.frame_count,),
        "color_frame_numbers": (metadata.frame_count,),
        "depth_frame_numbers": (metadata.frame_count,),
        "sample_sync_skew_ms": (metadata.frame_count,),
        "camera_intrinsics": (
            metadata.frame_count,
            *metadata.dimensions.get("camera_intrinsics", ()),
        ),
        "camera_distortion_coefficients": (
            metadata.frame_count,
            *metadata.dimensions.get("camera_distortion_coefficients", ()),
        ),
        "depth_scale_metres": (metadata.frame_count,),
    }
    for arm_name in metadata.source_arms:
        prefix = f"{arm_name}_"
        for name in (
            "sampled_at_utc_ns",
            "sampled_at_monotonic_ns",
            "gripper_open",
            "gripper_force_newtons",
            "gripper_raw_position",
        ):
            required_shapes[f"{prefix}{name}"] = (metadata.frame_count,)
        for name in ("joint_positions", "gripper_pose"):
            required_shapes[f"{prefix}{name}"] = (
                metadata.frame_count,
                *metadata.dimensions.get(f"{prefix}{name}", ()),
            )
    try:
        with np.load(low_dim_path, allow_pickle=False) as arrays:
            for name, expected_shape in required_shapes.items():
                if name not in arrays.files:
                    issues.append(
                        _error(
                            "portable_field_missing",
                            f"missing NPZ array: {name}",
                            low_dim_path,
                        )
                    )
                elif arrays[name].shape != expected_shape:
                    issues.append(
                        _error(
                            "portable_field_shape_mismatch",
                            f"{name} has shape {arrays[name].shape}; expected {expected_shape}",
                            low_dim_path,
                        )
                    )
            _validate_portable_optional_fields(arrays, metadata, low_dim_path, issues)
            _validate_portable_calibration(arrays, metadata, low_dim_path, issues)
            for name in arrays.files:
                value = arrays[name]
                if not np.issubdtype(value.dtype, np.number):
                    issues.append(
                        _error(
                            "portable_field_dtype_invalid",
                            f"array {name} must use a numeric dtype",
                            low_dim_path,
                        )
                    )
                elif not np.all(np.isfinite(value)):
                    issues.append(
                        _error(
                            "portable_field_non_finite",
                            f"array {name} contains non-finite values",
                            low_dim_path,
                        )
                    )
            _validate_portable_timing(arrays, metadata, low_dim_path, issues)
    except (OSError, ValueError, KeyError) as exc:
        issues.append(
            _error(
                "portable_payload_invalid",
                f"unable to validate portable NPZ payload: {exc}",
                low_dim_path,
            )
        )


def _validate_portable_optional_fields(
    arrays: Any,
    metadata: EpisodeMetadata,
    path: Path,
    issues: list[ValidationIssue],
) -> None:
    for arm_name in metadata.source_arms:
        for field in (
            "joint_velocities",
            "joint_currents",
            "end_effector_wrench",
        ):
            name = f"{arm_name}_{field}"
            mask_name = f"{name}_valid"
            state = metadata.fields.get(name)
            included = name in arrays.files
            mask_included = mask_name in arrays.files
            if state == "present" and (not included or not mask_included):
                issues.append(
                    _error(
                        "portable_optional_field_missing",
                        f"{name} requires both data and validity mask",
                        path,
                    )
                )
                continue
            if state == "absent" and (included or mask_included):
                issues.append(
                    _error(
                        "portable_optional_field_unexpected",
                        f"metadata declares {name} absent",
                        path,
                    )
                )
                continue
            if not included:
                continue
            expected_shape = (
                metadata.frame_count,
                *metadata.dimensions.get(name, ()),
            )
            if arrays[name].shape != expected_shape:
                issues.append(
                    _error(
                        "portable_field_shape_mismatch",
                        f"{name} has shape {arrays[name].shape}; expected {expected_shape}",
                        path,
                    )
                )
            mask = arrays[mask_name]
            if mask.shape != (metadata.frame_count,) or not np.all(
                np.isin(mask, (0, 1))
            ):
                issues.append(
                    _error(
                        "portable_validity_mask_invalid",
                        f"{mask_name} must be a binary frame mask",
                        path,
                    )
                )


def _validate_portable_calibration(
    arrays: Any,
    metadata: EpisodeMetadata,
    path: Path,
    issues: list[ValidationIssue],
) -> None:
    included = "camera_extrinsics" in arrays.files
    expected = metadata.camera_extrinsics is not None
    if included != expected:
        issues.append(
            _error(
                "portable_calibration_mismatch",
                "NPZ camera_extrinsics does not match metadata calibration",
                path,
            )
        )
    elif included and arrays["camera_extrinsics"].shape != (4, 4):
        issues.append(
            _error(
                "portable_field_shape_mismatch",
                "camera_extrinsics must have shape (4, 4)",
                path,
            )
        )


def _validate_portable_timing(
    arrays: Any,
    metadata: EpisodeMetadata,
    path: Path,
    issues: list[ValidationIssue],
) -> None:
    if "timestamps_utc_ns" in arrays.files and np.any(
        np.diff(arrays["timestamps_utc_ns"]) < 0
    ):
        issues.append(
            _error(
                "timestamp_order_invalid",
                "timestamps are not monotonically increasing",
                path,
            )
        )
    if "sample_sync_skew_ms" in arrays.files and np.any(
        arrays["sample_sync_skew_ms"] > metadata.maximum_sync_skew_ms
    ):
        issues.append(
            _error(
                "sample_sync_skew_exceeded",
                "captured sample exceeds configured synchronization bound",
                path,
            )
        )


def _validate_trusted_native_payload(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    try:
        observation_type, demo_type = _native_rlbench_types()
        with (path / NATIVE_LOW_DIM_FILENAME).open("rb") as file:
            demo = pickle.load(file)
        with (path / VARIATION_NUMBER_FILENAME).open("rb") as file:
            variation_number = pickle.load(file)
        with (path / VARIATION_DESCRIPTIONS_FILENAME).open("rb") as file:
            descriptions = pickle.load(file)
        if not isinstance(demo, demo_type):
            raise TypeError(f"low_dim_obs.pkl is not {demo_type.__name__}")
        observations = list(
            demo.observations if hasattr(demo, "observations") else demo
        )
        if len(observations) != metadata.frame_count:
            raise ValueError("RLBench Demo frame count does not match metadata")
        if not all(isinstance(item, observation_type) for item in observations):
            raise TypeError("RLBench Demo contains a non-Observation value")
        if variation_number != metadata.variation_id:
            raise ValueError("variation_number.pkl does not match metadata")
        if (
            not isinstance(descriptions, list)
            or not all(isinstance(item, str) for item in descriptions)
            or tuple(descriptions) != metadata.descriptions
        ):
            raise ValueError("variation_descriptions.pkl does not match metadata")
    except Exception as exc:  # noqa: BLE001 - unpickling failures are not standardized
        issues.append(
            _error(
                "native_payload_invalid",
                f"trusted native RLBench validation failed: {exc}",
                path,
            )
        )


def _native_rlbench_types():
    from rlbench.backend.observation import Observation
    from rlbench.demo import Demo

    return Observation, Demo


def _validate_image(
    path: Path,
    expected_shape: tuple[int, ...] | None,
    modality: str,
    issues: list[ValidationIssue],
) -> None:
    if not path.is_file():
        issues.append(_error("image_missing", f"{modality} image is missing", path))
        return
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        issues.append(
            _error("image_decode_failed", f"unable to decode {modality}", path)
        )
    elif expected_shape is not None and tuple(image.shape) != expected_shape:
        issues.append(
            _error(
                "image_shape_mismatch",
                f"expected {expected_shape}, found {tuple(image.shape)}",
                path,
            )
        )


def _resolve_episode_file(
    episode_path: Path,
    relative_path: str,
    issues: list[ValidationIssue],
) -> Path | None:
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        issues.append(
            _error(
                "unsafe_manifest_path",
                f"unsafe manifest path: {relative_path}",
                episode_path,
            )
        )
        return None
    resolved_root = episode_path.resolve()
    resolved = (episode_path / Path(*pure_path.parts)).resolve()
    if not resolved.is_relative_to(resolved_root):
        issues.append(
            _error(
                "unsafe_manifest_path",
                f"manifest path escapes episode directory: {relative_path}",
                episode_path,
            )
        )
        return None
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error(code: str, message: str, path: Path) -> ValidationIssue:
    return ValidationIssue(ValidationSeverity.ERROR, code, message, str(path))


def _warning(code: str, message: str, path: Path) -> ValidationIssue:
    return ValidationIssue(ValidationSeverity.WARNING, code, message, str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate robot-llm data-collection episode manifests and files."
    )
    parser.add_argument("dataset_path", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--no-checksums", action="store_true")
    parser.add_argument(
        "--trusted-native",
        action="store_true",
        help=(
            "deserialize and type-check native RLBench pickle files; "
            "never use this for untrusted datasets"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = validate_dataset(
            args.dataset_path,
            task=args.task,
            verify_checksums=not args.no_checksums,
            trusted_native=args.trusted_native,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            f"{'VALID' if report.valid else 'INVALID'}: "
            f"{len(report.episodes)} episode(s)"
        )
        for issue in report.issues:
            print(f"[{issue.severity.value}] {issue.code}: {issue.message}")
        for episode in report.episodes:
            print(f"{'VALID' if episode.valid else 'INVALID'}: {episode.episode_path}")
            for issue in episode.issues:
                print(f"  [{issue.severity.value}] {issue.code}: {issue.message}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

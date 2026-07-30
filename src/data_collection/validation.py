from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

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
) -> EpisodeValidationReport:
    path = Path(episode_path)
    issues: list[ValidationIssue] = []
    metadata: EpisodeMetadata | None = None
    checked_files = 0

    if not path.is_dir():
        return EpisodeValidationReport(
            episode_path=path,
            metadata=None,
            checked_files=0,
            issues=(
                _error(
                    "episode_not_found",
                    "episode path is not a directory",
                    path,
                ),
            ),
        )

    metadata_path = path / EPISODE_METADATA_FILENAME
    try:
        raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(raw_metadata, dict):
            raise ValueError("episode metadata root must be an object")
        metadata = EpisodeMetadata.from_dict(raw_metadata)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        issues.append(
            _error(
                "metadata_invalid",
                f"unable to load episode metadata: {exc}",
                metadata_path,
            )
        )
        return EpisodeValidationReport(
            episode_path=path,
            metadata=None,
            checked_files=0,
            issues=tuple(issues),
        )

    if require_canonical_name:
        match = _EPISODE_DIRECTORY_PATTERN.fullmatch(path.name)
        if match is None or int(match.group(1)) != metadata.episode_id:
            issues.append(
                _error(
                    "episode_directory_mismatch",
                    (
                        "episode directory name does not match metadata "
                        f"episode_id {metadata.episode_id}"
                    ),
                    path,
                )
            )

    listed_paths: set[str] = set()
    for item in metadata.files:
        listed_paths.add(item.path)
        file_path = _resolve_episode_file(path, item.path, issues)
        if file_path is None:
            continue
        if not file_path.is_file():
            issues.append(
                _error(
                    "file_missing",
                    f"required episode file is missing: {item.path}",
                    file_path,
                )
            )
            continue
        checked_files += 1
        size_bytes = file_path.stat().st_size
        if size_bytes != item.size_bytes:
            issues.append(
                _error(
                    "file_size_mismatch",
                    (f"expected {item.size_bytes} bytes, found {size_bytes}"),
                    file_path,
                )
            )
        if verify_checksums:
            digest = _sha256(file_path)
            if digest != item.sha256:
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

    _validate_required_images(path, metadata, issues)
    _validate_metadata_contract(path, metadata, issues)
    _validate_format_payload(path, metadata, issues)
    if metadata.capture_error_count:
        issues.append(
            _warning(
                "capture_errors_recorded",
                (
                    f"recorder reported {metadata.capture_error_count} "
                    "capture errors during this episode"
                ),
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
) -> DatasetValidationReport:
    root = Path(dataset_path)
    issues: list[ValidationIssue] = []
    episodes: list[EpisodeValidationReport] = []
    if not root.is_dir():
        return DatasetValidationReport(
            dataset_path=root,
            episodes=(),
            issues=(
                _error(
                    "dataset_not_found",
                    "dataset path is not a directory",
                    root,
                ),
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


def _validate_required_images(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    if metadata.frame_count <= 0:
        issues.append(
            _error(
                "empty_episode",
                "episode must contain at least one frame",
                path,
            )
        )
        return

    expected_rgb_shape = metadata.dimensions.get("front_rgb")
    expected_depth_shape = metadata.dimensions.get("front_depth")
    for index in range(metadata.frame_count):
        _validate_image(
            path / "front_rgb" / f"{index}.png",
            expected_rgb_shape,
            "front_rgb",
            issues,
        )
        _validate_image(
            path / "front_depth" / f"{index}.png",
            expected_depth_shape,
            "front_depth",
            issues,
        )


def _validate_metadata_contract(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    required_fields = {
        "timestamp",
        "front_rgb",
        "front_depth",
        "camera_intrinsics",
        "joint_positions",
        "gripper_open",
        "gripper_pose",
    }
    for field in sorted(required_fields):
        if metadata.fields.get(field) != "required":
            issues.append(
                _error(
                    "required_field_not_declared",
                    f"metadata must declare {field} as required",
                    path / EPISODE_METADATA_FILENAME,
                )
            )

    valid_field_states = {"required", "present", "absent"}
    for field, state in sorted(metadata.fields.items()):
        if state not in valid_field_states:
            issues.append(
                _error(
                    "field_state_invalid",
                    f"metadata field {field} has invalid state {state!r}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )

    required_units = required_fields
    for field in sorted(required_units):
        if not metadata.units.get(field):
            issues.append(
                _error(
                    "field_unit_missing",
                    f"metadata does not declare units for {field}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )

    required_dimensions = {
        "front_rgb",
        "front_depth",
        "camera_intrinsics",
        "joint_positions",
        "gripper_pose",
    }
    for field in sorted(required_dimensions):
        if not metadata.dimensions.get(field):
            issues.append(
                _error(
                    "field_dimensions_missing",
                    f"metadata does not declare dimensions for {field}",
                    path / EPISODE_METADATA_FILENAME,
                )
            )


def _validate_image(
    path: Path,
    expected_shape: tuple[int, ...] | None,
    modality: str,
    issues: list[ValidationIssue],
) -> None:
    if not path.is_file():
        issues.append(
            _error(
                "image_missing",
                f"required {modality} frame is missing",
                path,
            )
        )
        return
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        issues.append(
            _error(
                "image_decode_failed",
                f"unable to decode {modality} image",
                path,
            )
        )
        return
    if expected_shape is not None and tuple(image.shape) != expected_shape:
        issues.append(
            _error(
                "image_shape_mismatch",
                (
                    f"expected {modality} shape {expected_shape}, "
                    f"found {tuple(image.shape)}"
                ),
                path,
            )
        )


def _validate_format_payload(
    path: Path,
    metadata: EpisodeMetadata,
    issues: list[ValidationIssue],
) -> None:
    if metadata.format_variant is DataCollectionFormat.PORTABLE_SIMPLIFIED:
        _validate_portable_payload(path, metadata, issues)
        forbidden = (
            NATIVE_LOW_DIM_FILENAME,
            VARIATION_NUMBER_FILENAME,
            VARIATION_DESCRIPTIONS_FILENAME,
        )
        for filename in forbidden:
            if (path / filename).exists():
                issues.append(
                    _error(
                        "format_payload_mixed",
                        "portable episode contains native RLBench payload",
                        path / filename,
                    )
                )
        return

    required = (
        NATIVE_LOW_DIM_FILENAME,
        VARIATION_NUMBER_FILENAME,
        VARIATION_DESCRIPTIONS_FILENAME,
    )
    for filename in required:
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
    issues.append(
        _warning(
            "native_pickle_not_inspected",
            (
                "native RLBench pickle contents were not deserialized; "
                "only manifest integrity was checked"
            ),
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
    try:
        with np.load(low_dim_path, allow_pickle=False) as arrays:
            required = {
                "timestamps",
                "joint_positions",
                "gripper_open",
                "gripper_pose",
                "camera_intrinsics",
            }
            missing = sorted(required - set(arrays.files))
            if missing:
                issues.append(
                    _error(
                        "portable_field_missing",
                        "missing NPZ arrays: " + ", ".join(missing),
                        low_dim_path,
                    )
                )
                return
            for name in required:
                if arrays[name].shape[0] != metadata.frame_count:
                    issues.append(
                        _error(
                            "portable_frame_count_mismatch",
                            (
                                f"array {name} has {arrays[name].shape[0]} "
                                f"rows; expected {metadata.frame_count}"
                            ),
                            low_dim_path,
                        )
                    )
            expected_shapes = {
                "timestamps": (metadata.frame_count,),
                "gripper_open": (metadata.frame_count,),
                "joint_positions": (
                    metadata.frame_count,
                    *metadata.dimensions.get("joint_positions", ()),
                ),
                "gripper_pose": (
                    metadata.frame_count,
                    *metadata.dimensions.get("gripper_pose", ()),
                ),
                "camera_intrinsics": (
                    metadata.frame_count,
                    *metadata.dimensions.get("camera_intrinsics", ()),
                ),
            }
            for name, expected_shape in expected_shapes.items():
                if arrays[name].shape != expected_shape:
                    issues.append(
                        _error(
                            "portable_field_shape_mismatch",
                            (
                                f"array {name} has shape {arrays[name].shape}; "
                                f"expected {expected_shape}"
                            ),
                            low_dim_path,
                        )
                    )
            optional_fields = {
                "joint_velocities",
                "joint_forces",
                "gripper_matrix",
                "gripper_joint_positions",
            }
            for name in optional_fields:
                state = metadata.fields.get(name)
                included = name in arrays.files
                if state == "present" and not included:
                    issues.append(
                        _error(
                            "portable_optional_field_missing",
                            f"metadata declares {name}, but NPZ does not contain it",
                            low_dim_path,
                        )
                    )
                elif state == "absent" and included:
                    issues.append(
                        _error(
                            "portable_optional_field_unexpected",
                            f"metadata declares {name} absent, but NPZ contains it",
                            low_dim_path,
                        )
                    )
                if included:
                    expected_shape = (
                        metadata.frame_count,
                        *metadata.dimensions.get(name, ()),
                    )
                    if arrays[name].shape != expected_shape:
                        issues.append(
                            _error(
                                "portable_field_shape_mismatch",
                                (
                                    f"array {name} has shape "
                                    f"{arrays[name].shape}; expected "
                                    f"{expected_shape}"
                                ),
                                low_dim_path,
                            )
                        )
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
            timestamps = arrays["timestamps"]
            if np.all(np.isfinite(timestamps)) and np.any(np.diff(timestamps) < 0):
                issues.append(
                    _error(
                        "timestamp_order_invalid",
                        "timestamps are not monotonically increasing",
                        low_dim_path,
                    )
                )
    except (OSError, ValueError, KeyError) as exc:
        issues.append(
            _error(
                "portable_payload_invalid",
                f"unable to validate portable NPZ payload: {exc}",
                low_dim_path,
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
    return ValidationIssue(
        severity=ValidationSeverity.ERROR,
        code=code,
        message=message,
        path=str(path),
    )


def _warning(code: str, message: str, path: Path) -> ValidationIssue:
    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        code=code,
        message=message,
        path=str(path),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate robot-llm data-collection episode manifests and files "
            "without deserializing pickle payloads."
        )
    )
    parser.add_argument("dataset_path", type=Path)
    parser.add_argument("--task")
    parser.add_argument(
        "--no-checksums",
        action="store_true",
        help="skip SHA-256 verification",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete report as JSON",
    )
    args = parser.parse_args(argv)
    try:
        report = validate_dataset(
            args.dataset_path,
            task=args.task,
            verify_checksums=not args.no_checksums,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        print(
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        status = "VALID" if report.valid else "INVALID"
        print(
            f"{status}: {len(report.episodes)} episode(s), "
            f"{len(report.issues)} dataset issue(s)"
        )
        for issue in report.issues:
            print(
                f"[{issue.severity.value}] {issue.code}: {issue.message} ({issue.path})"
            )
        for episode in report.episodes:
            episode_status = "VALID" if episode.valid else "INVALID"
            print(
                f"{episode_status}: {episode.episode_path} "
                f"({len(episode.issues)} issue(s))"
            )
            for issue in episode.issues:
                print(
                    f"  [{issue.severity.value}] {issue.code}: "
                    f"{issue.message} ({issue.path})"
                )
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

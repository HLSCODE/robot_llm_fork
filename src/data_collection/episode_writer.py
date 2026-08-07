from __future__ import annotations

import io
import json
import logging
import math
import os
import pickle
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from .schema import (
    EPISODE_METADATA_FILENAME,
    NATIVE_LOW_DIM_FILENAME,
    PORTABLE_LOW_DIM_FILENAME,
    VARIATION_DESCRIPTIONS_FILENAME,
    VARIATION_NUMBER_FILENAME,
    DataCollectionFormat,
    EpisodeFile,
    EpisodeMetadata,
    ArmFrameRecord,
    FrameRecord,
    normalize_descriptions,
    validate_source_arms,
    validate_task_name,
)
from .validation import validate_episode

logger = logging.getLogger(__name__)

_TEMP_DIRECTORY_PREFIX = ".episode"
_TEMP_DIRECTORY_MARKER = ".tmp-"
_EPISODE_FRAME_OVERHEAD_BYTES = 4096
_TEMP_IDENTIFIER_CHARACTERS = frozenset("0123456789abcdefABCDEF")


class EpisodeWriteError(RuntimeError):
    """Base error for deterministic episode persistence failures."""


class EpisodeAlreadyExistsError(EpisodeWriteError):
    pass


class InsufficientStorageError(EpisodeWriteError):
    def __init__(
        self,
        *,
        required_bytes: int,
        free_bytes: int,
    ) -> None:
        super().__init__(
            "insufficient data-collection storage: "
            f"required {required_bytes} bytes, free {free_bytes} bytes"
        )
        self.required_bytes = required_bytes
        self.free_bytes = free_bytes


class EpisodeFormatUnavailableError(EpisodeWriteError):
    pass


class EpisodeIntegrityError(EpisodeWriteError):
    pass


@dataclass(frozen=True, slots=True)
class DataCollectionStoragePolicy:
    minimum_free_bytes: int
    overhead_factor: float
    stale_write_seconds: float

    def __post_init__(self) -> None:
        if self.minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must not be negative")
        if not math.isfinite(self.overhead_factor) or self.overhead_factor < 1:
            raise ValueError("overhead_factor must be finite and at least 1")
        if not math.isfinite(self.stale_write_seconds) or self.stale_write_seconds <= 0:
            raise ValueError("stale_write_seconds must be positive and finite")


@dataclass(frozen=True, slots=True)
class SessionStorageStatus:
    task: str
    next_episode_id: int
    free_bytes: int
    recovered_paths: tuple[Path, ...]
    format_variant: DataCollectionFormat


@dataclass(frozen=True, slots=True)
class EpisodeSaveResult:
    episode_path: Path
    metadata: EpisodeMetadata
    estimated_bytes: int


class DataCollectionEpisodeWriter:
    """Validate, stage, verify and atomically publish one episode."""

    def __init__(
        self,
        save_path: str | Path,
        *,
        format_variant: DataCollectionFormat,
        storage_policy: DataCollectionStoragePolicy,
        random_seed: int,
        source_arms: Sequence[str],
        maximum_sync_skew_ms: float,
        camera_extrinsics: tuple[float, ...] | None = None,
        camera_extrinsics_reference_frame: str | None = None,
        calibration_id: str | None = None,
        clock: Callable[[], float] = time.time,
        identifier_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._save_path = Path(save_path)
        self._format_variant = format_variant
        self._storage_policy = storage_policy
        self._random_seed = random_seed
        self._source_arms = validate_source_arms(source_arms)
        if (
            self._format_variant is DataCollectionFormat.RLBENCH_NATIVE
            and len(self._source_arms) != 1
        ):
            raise ValueError("rlbench_native format requires exactly one source arm")
        if not math.isfinite(maximum_sync_skew_ms) or maximum_sync_skew_ms <= 0:
            raise ValueError("maximum_sync_skew_ms must be positive and finite")
        self._maximum_sync_skew_ms = maximum_sync_skew_ms
        self._camera_extrinsics = camera_extrinsics
        self._camera_extrinsics_reference_frame = camera_extrinsics_reference_frame
        self._calibration_id = calibration_id
        self._clock = clock
        self._identifier_factory = identifier_factory

    @property
    def format_variant(self) -> DataCollectionFormat:
        return self._format_variant

    def prepare_session(self, task: str) -> SessionStorageStatus:
        normalized_task = validate_task_name(task)
        self._ensure_format_available()
        episodes_path = self._episodes_path(normalized_task)
        try:
            episodes_path.mkdir(parents=True, exist_ok=True)
            recovered = self.recover_stale_writes(normalized_task)
            free_bytes = self._ensure_capacity(
                episodes_path,
                estimated_bytes=0,
            )
        except EpisodeWriteError:
            raise
        except OSError as exc:
            raise EpisodeWriteError(
                f"data-collection session storage preflight failed: {exc}"
            ) from exc
        return SessionStorageStatus(
            task=normalized_task,
            next_episode_id=self._next_episode_id(episodes_path),
            free_bytes=free_bytes,
            recovered_paths=recovered,
            format_variant=self._format_variant,
        )

    def save_episode(
        self,
        *,
        task: str,
        episode_id: int,
        frames: Sequence[FrameRecord],
        description: str,
        variation_id: int = 0,
        capture_error_count: int = 0,
    ) -> EpisodeSaveResult:
        normalized_task = validate_task_name(task)
        _require_nonnegative_int(episode_id, "episode_id")
        _require_nonnegative_int(variation_id, "variation_id")
        _require_nonnegative_int(capture_error_count, "capture_error_count")
        self._ensure_format_available()
        fields, dimensions = _validate_frames(
            frames,
            source_arms=self._source_arms,
            maximum_sync_skew_ms=self._maximum_sync_skew_ms,
        )
        estimated_bytes = self.estimate_episode_bytes(frames)

        episodes_path = self._episodes_path(normalized_task)
        final_path = episodes_path / f"episode{episode_id}"
        identifier = self._identifier_factory()
        if (
            not isinstance(identifier, str)
            or not identifier
            or any(
                character not in _TEMP_IDENTIFIER_CHARACTERS for character in identifier
            )
        ):
            raise EpisodeWriteError(
                "temporary episode identifier must be non-empty hexadecimal text"
            )
        temp_path = episodes_path / (
            f"{_TEMP_DIRECTORY_PREFIX}{episode_id}{_TEMP_DIRECTORY_MARKER}{identifier}"
        )
        try:
            episodes_path.mkdir(parents=True, exist_ok=True)
            self._ensure_capacity(
                episodes_path,
                estimated_bytes=estimated_bytes,
            )
            if final_path.exists():
                raise EpisodeAlreadyExistsError(f"episode already exists: {final_path}")
            if temp_path.exists():
                raise EpisodeWriteError(
                    f"temporary episode path already exists: {temp_path}"
                )
            temp_path.mkdir()
        except EpisodeWriteError:
            raise
        except OSError as exc:
            raise EpisodeWriteError(
                f"unable to stage episode {episode_id}: {exc}"
            ) from exc

        published = False
        try:
            (temp_path / "front_rgb").mkdir()
            (temp_path / "front_depth").mkdir()
            self._write_visual_data(temp_path, frames)
            self._write_format_payload(
                temp_path,
                frames,
                description=description,
                variation_id=variation_id,
            )
            files = self._episode_files(temp_path)
            first_frame = frames[0]
            metadata = EpisodeMetadata(
                format_variant=self._format_variant,
                task=normalized_task,
                source_arms=self._source_arms,
                episode_id=episode_id,
                variation_id=variation_id,
                descriptions=normalize_descriptions(description),
                frame_count=len(frames),
                capture_error_count=capture_error_count,
                created_at_utc=datetime.fromtimestamp(
                    self._clock(),
                    UTC,
                ).isoformat(),
                fields=fields,
                units=_episode_units(
                    self._format_variant,
                    self._source_arms,
                ),
                dimensions=dimensions,
                files=files,
                camera_name=first_frame.camera_name,
                camera_serial=first_frame.camera_serial,
                camera_distortion_model=first_frame.camera_distortion_model,
                camera_hardware_timestamp_domain=(
                    first_frame.camera_hardware_timestamp_domain
                ),
                depth_aligned_to_color=first_frame.depth_aligned_to_color,
                maximum_sync_skew_ms=self._maximum_sync_skew_ms,
                observed_maximum_sync_skew_ms=max(
                    frame.sample_sync_skew_ms for frame in frames
                ),
                camera_extrinsics=self._camera_extrinsics,
                camera_extrinsics_reference_frame=(
                    self._camera_extrinsics_reference_frame
                ),
                calibration_id=self._calibration_id,
            )
            self._write_json(
                temp_path / EPISODE_METADATA_FILENAME,
                metadata.to_dict(),
            )
            report = validate_episode(
                temp_path,
                verify_checksums=True,
                require_canonical_name=False,
            )
            if not report.valid:
                details = "; ".join(
                    f"{issue.code}: {issue.message}"
                    for issue in report.issues
                    if issue.severity.value == "error"
                )
                raise EpisodeIntegrityError(
                    f"staged episode failed integrity validation: {details}"
                )
            if final_path.exists():
                raise EpisodeAlreadyExistsError(f"episode already exists: {final_path}")
            os.replace(temp_path, final_path)
            published = True
            _fsync_directory(episodes_path)
        except Exception as exc:
            if not published and temp_path.exists():
                try:
                    self._remove_temp_directory(temp_path, episodes_path)
                except OSError as cleanup_exc:
                    raise EpisodeWriteError(
                        f"{exc}; failed to remove staged episode: {cleanup_exc}"
                    ) from exc
            if isinstance(exc, (EpisodeWriteError, ValueError)):
                raise
            raise EpisodeWriteError(
                f"failed to persist episode {episode_id}: {exc}"
            ) from exc

        logger.info(
            "episode %d atomically published to %s (%s)",
            episode_id,
            final_path,
            self._format_variant.value,
        )
        return EpisodeSaveResult(
            episode_path=final_path,
            metadata=metadata,
            estimated_bytes=estimated_bytes,
        )

    def recover_stale_writes(self, task: str) -> tuple[Path, ...]:
        episodes_path = self._episodes_path(validate_task_name(task))
        if not episodes_path.is_dir():
            return ()
        cutoff = self._clock() - self._storage_policy.stale_write_seconds
        recovered: list[Path] = []
        for candidate in episodes_path.iterdir():
            if not candidate.is_dir() or not _is_temp_episode(candidate.name):
                continue
            if candidate.stat().st_mtime > cutoff:
                continue
            self._remove_temp_directory(candidate, episodes_path)
            recovered.append(candidate)
        if recovered:
            logger.warning(
                "removed %d stale staged data-collection episode(s) for %s",
                len(recovered),
                task,
            )
        return tuple(recovered)

    def estimate_episode_bytes(
        self,
        frames: Sequence[FrameRecord],
    ) -> int:
        raw_bytes = 0
        for frame in frames:
            raw_bytes += _EPISODE_FRAME_OVERHEAD_BYTES
            for value in (
                frame.front_rgb,
                frame.front_depth,
                frame.camera_intrinsics,
                frame.camera_distortion_coefficients,
            ):
                if isinstance(value, np.ndarray):
                    raw_bytes += value.nbytes
            for arm in frame.arms.values():
                arm_arrays: tuple[np.ndarray | None, ...] = (
                    arm.joint_positions,
                    arm.joint_velocities,
                    arm.joint_currents,
                    arm.gripper_pose,
                    arm.end_effector_wrench,
                )
                for arm_array in arm_arrays:
                    if arm_array is not None:
                        raw_bytes += arm_array.nbytes
        return math.ceil(raw_bytes * self._storage_policy.overhead_factor)

    def _ensure_capacity(
        self,
        path: Path,
        *,
        estimated_bytes: int,
    ) -> int:
        usage = shutil.disk_usage(path)
        required_bytes = self._storage_policy.minimum_free_bytes + estimated_bytes
        if usage.free < required_bytes:
            raise InsufficientStorageError(
                required_bytes=required_bytes,
                free_bytes=usage.free,
            )
        return usage.free

    def _ensure_format_available(self) -> None:
        if self._format_variant is not DataCollectionFormat.RLBENCH_NATIVE:
            return
        try:
            _native_rlbench_types()
        except ImportError as exc:
            raise EpisodeFormatUnavailableError(
                "rlbench_native format requires the optional 'rlbench' package"
            ) from exc

    def _write_visual_data(
        self,
        episode_path: Path,
        frames: Sequence[FrameRecord],
    ) -> None:
        for index, frame in enumerate(frames):
            self._write_png(
                episode_path / "front_rgb" / f"{index}.png",
                _required_array(frame.front_rgb, "front_rgb"),
            )
            self._write_png(
                episode_path / "front_depth" / f"{index}.png",
                _required_array(frame.front_depth, "front_depth"),
            )

    def _write_format_payload(
        self,
        episode_path: Path,
        frames: Sequence[FrameRecord],
        *,
        description: str,
        variation_id: int,
    ) -> None:
        if self._format_variant is DataCollectionFormat.PORTABLE_SIMPLIFIED:
            self._write_portable_payload(episode_path, frames)
            return
        self._write_native_payload(
            episode_path,
            frames,
            description=description,
            variation_id=variation_id,
        )

    def _write_portable_payload(
        self,
        episode_path: Path,
        frames: Sequence[FrameRecord],
    ) -> None:
        arrays: dict[str, np.ndarray] = {
            "timestamps_utc_ns": np.asarray(
                [frame.timestamp_utc_ns for frame in frames],
                dtype=np.int64,
            ),
            "camera_received_at_monotonic_ns": np.asarray(
                [frame.camera_received_at_monotonic_ns for frame in frames],
                dtype=np.int64,
            ),
            "color_hardware_timestamps_ms": np.asarray(
                [frame.color_hardware_timestamp_ms for frame in frames],
                dtype=np.float64,
            ),
            "depth_hardware_timestamps_ms": np.asarray(
                [frame.depth_hardware_timestamp_ms for frame in frames],
                dtype=np.float64,
            ),
            "color_frame_numbers": np.asarray(
                [frame.color_frame_number for frame in frames],
                dtype=np.int64,
            ),
            "depth_frame_numbers": np.asarray(
                [frame.depth_frame_number for frame in frames],
                dtype=np.int64,
            ),
            "sample_sync_skew_ms": np.asarray(
                [frame.sample_sync_skew_ms for frame in frames],
                dtype=np.float64,
            ),
            "camera_intrinsics": np.stack(
                [
                    _required_array(
                        frame.camera_intrinsics,
                        "camera_intrinsics",
                    )
                    for frame in frames
                ]
            ),
            "camera_distortion_coefficients": np.stack(
                [
                    _required_array(
                        frame.camera_distortion_coefficients,
                        "camera_distortion_coefficients",
                    )
                    for frame in frames
                ]
            ),
            "depth_scale_metres": np.asarray(
                [frame.depth_scale_metres for frame in frames],
                dtype=np.float64,
            ),
        }
        if self._camera_extrinsics is not None:
            arrays["camera_extrinsics"] = np.asarray(
                self._camera_extrinsics,
                dtype=np.float64,
            ).reshape(4, 4)

        for arm_name in self._source_arms:
            samples = [frame.arms[arm_name] for frame in frames]
            prefix = f"{arm_name}_"
            arrays.update(
                {
                    f"{prefix}sampled_at_utc_ns": np.asarray(
                        [sample.sampled_at_utc_ns for sample in samples],
                        dtype=np.int64,
                    ),
                    f"{prefix}sampled_at_monotonic_ns": np.asarray(
                        [sample.sampled_at_monotonic_ns for sample in samples],
                        dtype=np.int64,
                    ),
                    f"{prefix}joint_positions": np.stack(
                        [sample.joint_positions for sample in samples]
                    ),
                    f"{prefix}gripper_open": np.asarray(
                        [sample.gripper_open for sample in samples],
                        dtype=np.float64,
                    ),
                    f"{prefix}gripper_force_newtons": np.asarray(
                        [sample.gripper_force_newtons for sample in samples],
                        dtype=np.float64,
                    ),
                    f"{prefix}gripper_raw_position": np.asarray(
                        [sample.gripper_raw_position for sample in samples],
                        dtype=np.int64,
                    ),
                    f"{prefix}gripper_pose": np.stack(
                        [sample.gripper_pose for sample in samples]
                    ),
                }
            )
            for field_name in (
                "joint_velocities",
                "joint_currents",
                "end_effector_wrench",
            ):
                values, valid = _masked_optional_stack(samples, field_name)
                if values is not None:
                    arrays[f"{prefix}{field_name}"] = values
                    arrays[f"{prefix}{field_name}_valid"] = valid

        buffer = io.BytesIO()
        savez_compressed: Callable[..., None] = np.savez_compressed
        savez_compressed(buffer, **arrays)
        self._write_bytes(
            episode_path / PORTABLE_LOW_DIM_FILENAME,
            buffer.getvalue(),
        )

    def _write_native_payload(
        self,
        episode_path: Path,
        frames: Sequence[FrameRecord],
        *,
        description: str,
        variation_id: int,
    ) -> None:
        observation_type, demo_type = _native_rlbench_types()
        arm_name = self._source_arms[0]
        observations = [
            observation_type(
                left_shoulder_rgb=None,
                left_shoulder_depth=None,
                left_shoulder_point_cloud=None,
                right_shoulder_rgb=None,
                right_shoulder_depth=None,
                right_shoulder_point_cloud=None,
                overhead_rgb=None,
                overhead_depth=None,
                overhead_point_cloud=None,
                wrist_rgb=None,
                wrist_depth=None,
                wrist_point_cloud=None,
                front_rgb=None,
                front_depth=None,
                front_point_cloud=None,
                left_shoulder_mask=None,
                right_shoulder_mask=None,
                overhead_mask=None,
                wrist_mask=None,
                front_mask=None,
                joint_velocities=(
                    np.deg2rad(frame.arms[arm_name].joint_velocities)
                    if frame.arms[arm_name].joint_velocities is not None
                    else None
                ),
                joint_positions=np.deg2rad(frame.arms[arm_name].joint_positions),
                joint_forces=None,
                gripper_open=frame.arms[arm_name].gripper_open,
                gripper_pose=frame.arms[arm_name].gripper_pose,
                gripper_matrix=None,
                gripper_touch_forces=None,
                gripper_joint_positions=None,
                task_low_dim_state=None,
                ignore_collisions=True,
                misc=_native_misc(
                    frame,
                    arm_name=arm_name,
                    camera_extrinsics=self._camera_extrinsics,
                    camera_extrinsics_reference_frame=(
                        self._camera_extrinsics_reference_frame
                    ),
                    calibration_id=self._calibration_id,
                ),
            )
            for frame in frames
        ]
        demo = demo_type(observations, random_seed=self._random_seed)
        demo.variation_number = variation_id
        self._write_pickle(
            episode_path / NATIVE_LOW_DIM_FILENAME,
            demo,
        )
        self._write_pickle(
            episode_path / VARIATION_NUMBER_FILENAME,
            variation_id,
        )
        self._write_pickle(
            episode_path / VARIATION_DESCRIPTIONS_FILENAME,
            list(normalize_descriptions(description)),
        )

    def _episode_files(self, episode_path: Path) -> tuple[EpisodeFile, ...]:
        return tuple(
            EpisodeFile(
                path=file_path.relative_to(episode_path).as_posix(),
                role=_file_role(file_path.relative_to(episode_path)),
                size_bytes=file_path.stat().st_size,
                sha256=_sha256(file_path),
            )
            for file_path in sorted(episode_path.rglob("*"))
            if file_path.is_file() and file_path.name != EPISODE_METADATA_FILENAME
        )

    @staticmethod
    def _write_png(path: Path, image: np.ndarray) -> None:
        encoded, content = cv2.imencode(".png", image)
        if not encoded:
            raise EpisodeWriteError(f"failed to encode PNG image: {path}")
        DataCollectionEpisodeWriter._write_bytes(
            path,
            content.tobytes(),
        )

    @staticmethod
    def _write_pickle(path: Path, value: object) -> None:
        DataCollectionEpisodeWriter._write_bytes(
            path,
            pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL),
        )

    @staticmethod
    def _write_json(
        path: Path,
        value: Mapping[str, object],
    ) -> None:
        content = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        DataCollectionEpisodeWriter._write_bytes(path, content)

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        with path.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    def _episodes_path(self, task: str) -> Path:
        return self._save_path / task / "all_variations" / "episodes"

    @staticmethod
    def _next_episode_id(episodes_path: Path) -> int:
        episode_ids = [
            int(candidate.name.removeprefix("episode"))
            for candidate in episodes_path.iterdir()
            if candidate.is_dir()
            and candidate.name.removeprefix("episode").isdigit()
            and candidate.name.startswith("episode")
        ]
        return max(episode_ids, default=-1) + 1

    @staticmethod
    def _remove_temp_directory(
        candidate: Path,
        episodes_path: Path,
    ) -> None:
        resolved_parent = episodes_path.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_candidate.parent != resolved_parent or not _is_temp_episode(
            resolved_candidate.name
        ):
            raise OSError(f"refusing to remove unsafe staged path: {candidate}")
        shutil.rmtree(resolved_candidate)


def _validate_frames(
    frames: Sequence[FrameRecord],
    *,
    source_arms: tuple[str, ...],
    maximum_sync_skew_ms: float,
) -> tuple[dict[str, str], dict[str, tuple[int, ...]]]:
    if not frames:
        raise EpisodeIntegrityError("episode must contain at least one captured frame")
    dimensions: dict[str, tuple[int, ...]] = {}
    fields = {
        "timestamp_utc_ns": "required",
        "front_rgb": "required",
        "front_depth": "required",
        "camera_intrinsics": "required",
        "camera_distortion_coefficients": "required",
        "depth_scale_metres": "required",
        "camera_hardware_timestamps": "required",
        "camera_frame_numbers": "required",
        "sample_sync_skew_ms": "required",
    }
    previous_timestamp: int | None = None
    first_frame = frames[0]
    for index, frame in enumerate(frames):
        timestamp = frame.timestamp_utc_ns
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp <= 0
        ):
            raise EpisodeIntegrityError(
                f"frame {index} timestamp_utc_ns must be a positive integer"
            )
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise EpisodeIntegrityError(
                f"frame {index} timestamp is earlier than the previous frame"
            )
        previous_timestamp = timestamp
        if set(frame.arms) != set(source_arms):
            raise EpisodeIntegrityError(
                f"frame {index} arms do not match configured source_arms"
            )
        if not frame.depth_aligned_to_color:
            raise EpisodeIntegrityError(
                f"frame {index} depth must be aligned to the color stream"
            )

        rgb = _required_array(frame.front_rgb, "front_rgb")
        depth = _required_array(frame.front_depth, "front_depth")
        intrinsics = _required_array(
            frame.camera_intrinsics,
            "camera_intrinsics",
        )
        distortion = _required_array(
            frame.camera_distortion_coefficients,
            "camera_distortion_coefficients",
        )
        for name, array in (
            ("front_rgb", rgb),
            ("front_depth", depth),
            ("camera_intrinsics", intrinsics),
            ("camera_distortion_coefficients", distortion),
        ):
            _require_consistent_shape(dimensions, name, array, index)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise EpisodeIntegrityError(
                f"frame {index} front_rgb must be an HxWx3 uint8 BGR image"
            )
        if depth.ndim != 2 or depth.dtype != np.uint16:
            raise EpisodeIntegrityError(
                f"frame {index} front_depth must be an HxW uint16 image"
            )
        if rgb.shape[:2] != depth.shape:
            raise EpisodeIntegrityError(
                f"frame {index} aligned RGB/depth image dimensions do not match"
            )
        if intrinsics.shape != (3, 3):
            raise EpisodeIntegrityError(
                f"frame {index} camera_intrinsics must have shape (3, 3)"
            )
        if intrinsics[0, 0] <= 0 or intrinsics[1, 1] <= 0:
            raise EpisodeIntegrityError(
                f"frame {index} camera focal lengths must be positive"
            )
        if distortion.ndim != 1:
            raise EpisodeIntegrityError(
                f"frame {index} distortion coefficients must be one-dimensional"
            )
        _require_finite_positive(
            frame.depth_scale_metres,
            f"frame {index} depth_scale_metres",
        )
        for name, value in (
            ("color_hardware_timestamp_ms", frame.color_hardware_timestamp_ms),
            ("depth_hardware_timestamp_ms", frame.depth_hardware_timestamp_ms),
        ):
            if not math.isfinite(value) or value < 0:
                raise EpisodeIntegrityError(
                    f"frame {index} {name} must be finite and non-negative"
                )
        for name, value in (
            ("color_frame_number", frame.color_frame_number),
            ("depth_frame_number", frame.depth_frame_number),
            (
                "camera_received_at_monotonic_ns",
                frame.camera_received_at_monotonic_ns,
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EpisodeIntegrityError(
                    f"frame {index} {name} must be a non-negative integer"
                )
        if (
            not math.isfinite(frame.sample_sync_skew_ms)
            or frame.sample_sync_skew_ms < 0
            or frame.sample_sync_skew_ms > maximum_sync_skew_ms
        ):
            raise EpisodeIntegrityError(
                f"frame {index} sample synchronization skew is out of bounds"
            )
        for field_name in (
            "camera_name",
            "camera_serial",
            "camera_distortion_model",
            "camera_hardware_timestamp_domain",
            "depth_aligned_to_color",
        ):
            if getattr(frame, field_name) != getattr(first_frame, field_name):
                raise EpisodeIntegrityError(
                    f"frame {index} camera metadata changed for {field_name}"
                )
        for arm_name in source_arms:
            _validate_arm_sample(
                frame.arms[arm_name],
                arm_name=arm_name,
                frame_index=index,
                dimensions=dimensions,
            )

    for arm_name in source_arms:
        prefix = f"{arm_name}_"
        for name in (
            "sampled_at_utc_ns",
            "sampled_at_monotonic_ns",
            "joint_positions",
            "gripper_open",
            "gripper_force_newtons",
            "gripper_raw_position",
            "gripper_pose",
        ):
            fields[f"{prefix}{name}"] = "required"
        for name in (
            "joint_velocities",
            "joint_currents",
            "end_effector_wrench",
        ):
            values = [getattr(frame.arms[arm_name], name) for frame in frames]
            fields[f"{prefix}{name}"] = (
                "present" if any(value is not None for value in values) else "absent"
            )
            for index, value in enumerate(values):
                if value is None:
                    continue
                array = _required_array(value, f"{prefix}{name}")
                _require_consistent_shape(
                    dimensions,
                    f"{prefix}{name}",
                    array,
                    index,
                )
    return fields, dimensions


def _validate_arm_sample(
    sample: ArmFrameRecord,
    *,
    arm_name: str,
    frame_index: int,
    dimensions: dict[str, tuple[int, ...]],
) -> None:
    prefix = f"{arm_name}_"
    for name in ("sampled_at_utc_ns", "sampled_at_monotonic_ns"):
        value = getattr(sample, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise EpisodeIntegrityError(
                f"frame {frame_index} {prefix}{name} must be a positive integer"
            )
    joint_positions = _required_array(
        sample.joint_positions,
        f"{prefix}joint_positions",
    )
    gripper_pose = _required_array(
        sample.gripper_pose,
        f"{prefix}gripper_pose",
    )
    _require_consistent_shape(
        dimensions,
        f"{prefix}joint_positions",
        joint_positions,
        frame_index,
    )
    _require_consistent_shape(
        dimensions,
        f"{prefix}gripper_pose",
        gripper_pose,
        frame_index,
    )
    if joint_positions.ndim != 1:
        raise EpisodeIntegrityError(
            f"frame {frame_index} {prefix}joint_positions must be one-dimensional"
        )
    if gripper_pose.shape != (7,):
        raise EpisodeIntegrityError(
            f"frame {frame_index} {prefix}gripper_pose must contain xyz + quaternion"
        )
    gripper_open = float(sample.gripper_open)
    if not math.isfinite(gripper_open) or not 0.0 <= gripper_open <= 1.0:
        raise EpisodeIntegrityError(
            f"frame {frame_index} {prefix}gripper_open must be in range 0..1"
        )
    _require_finite_nonnegative(
        float(sample.gripper_force_newtons),
        f"frame {frame_index} {prefix}gripper_force_newtons",
    )
    raw_position = sample.gripper_raw_position
    if (
        isinstance(raw_position, bool)
        or not isinstance(raw_position, int)
        or raw_position < 0
    ):
        raise EpisodeIntegrityError(
            f"frame {frame_index} {prefix}gripper_raw_position is invalid"
        )
    quaternion_norm = float(np.linalg.norm(gripper_pose[3:]))
    if not math.isclose(quaternion_norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise EpisodeIntegrityError(
            f"frame {frame_index} {prefix}gripper pose quaternion is not normalized"
        )
    for name in (
        "joint_velocities",
        "joint_currents",
        "end_effector_wrench",
    ):
        value = getattr(sample, name)
        if value is None:
            continue
        array = _required_array(value, f"{prefix}{name}")
        if array.ndim != 1:
            raise EpisodeIntegrityError(
                f"frame {frame_index} {prefix}{name} must be one-dimensional"
            )
        if name in {"joint_velocities", "joint_currents"}:
            if array.shape != joint_positions.shape:
                raise EpisodeIntegrityError(
                    f"frame {frame_index} {prefix}{name} must match joint count"
                )
        elif array.shape != (6,):
            raise EpisodeIntegrityError(
                f"frame {frame_index} {prefix}{name} must contain 6 values"
            )


def _require_finite_positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise EpisodeIntegrityError(f"{name} must be positive and finite")


def _require_finite_nonnegative(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise EpisodeIntegrityError(f"{name} must be finite and non-negative")


def _require_consistent_shape(
    dimensions: dict[str, tuple[int, ...]],
    name: str,
    array: np.ndarray,
    frame_index: int,
) -> None:
    shape = tuple(array.shape)
    expected = dimensions.setdefault(name, shape)
    if shape != expected:
        raise EpisodeIntegrityError(
            f"frame {frame_index} field {name} has shape {shape}; expected {expected}"
        )


def _required_array(
    value: object,
    name: str,
) -> np.ndarray:
    if not isinstance(value, np.ndarray) or value.size == 0:
        raise EpisodeIntegrityError(
            f"frame field {name} must be a non-empty numpy array"
        )
    if not np.issubdtype(value.dtype, np.number):
        raise EpisodeIntegrityError(f"frame field {name} must use a numeric dtype")
    if not np.all(np.isfinite(value)):
        raise EpisodeIntegrityError(f"frame field {name} contains non-finite values")
    return value


def _masked_optional_stack(
    samples: Sequence[ArmFrameRecord],
    name: str,
) -> tuple[np.ndarray | None, np.ndarray]:
    values = [getattr(sample, name) for sample in samples]
    template = next(
        (value for value in values if isinstance(value, np.ndarray)),
        None,
    )
    valid = np.asarray(
        [isinstance(value, np.ndarray) for value in values],
        dtype=np.uint8,
    )
    if template is None:
        return None, valid
    stacked = np.stack(
        [
            value if isinstance(value, np.ndarray) else np.zeros_like(template)
            for value in values
        ]
    )
    return stacked, valid


def _native_misc(
    frame: FrameRecord,
    *,
    arm_name: str,
    camera_extrinsics: tuple[float, ...] | None,
    camera_extrinsics_reference_frame: str | None,
    calibration_id: str | None,
) -> dict[str, object]:
    arm = frame.arms[arm_name]
    misc: dict[str, object] = {
        "front_camera_intrinsics": frame.camera_intrinsics,
        "front_camera_distortion_coefficients": (frame.camera_distortion_coefficients),
        "front_depth_scale_metres": frame.depth_scale_metres,
        "front_color_hardware_timestamp_ms": (frame.color_hardware_timestamp_ms),
        "front_depth_hardware_timestamp_ms": (frame.depth_hardware_timestamp_ms),
        "front_color_frame_number": frame.color_frame_number,
        "front_depth_frame_number": frame.depth_frame_number,
        "host_timestamp_utc_ns": frame.timestamp_utc_ns,
        "host_camera_received_at_monotonic_ns": (frame.camera_received_at_monotonic_ns),
        "arm_sampled_at_utc_ns": arm.sampled_at_utc_ns,
        "arm_sampled_at_monotonic_ns": arm.sampled_at_monotonic_ns,
        "sample_sync_skew_ms": frame.sample_sync_skew_ms,
        "gripper_force_newtons": arm.gripper_force_newtons,
        "gripper_raw_position": arm.gripper_raw_position,
        "joint_currents_amperes": arm.joint_currents,
        "end_effector_wrench": arm.end_effector_wrench,
    }
    if camera_extrinsics is not None:
        misc["robot_llm_camera_extrinsics"] = np.asarray(
            camera_extrinsics,
            dtype=np.float64,
        ).reshape(4, 4)
        misc["robot_llm_camera_extrinsics_reference_frame"] = (
            camera_extrinsics_reference_frame
        )
        misc["robot_llm_calibration_id"] = calibration_id
    return misc


def _require_nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _file_role(path: Path) -> str:
    if path.parts[0] == "front_rgb":
        return "front_rgb"
    if path.parts[0] == "front_depth":
        return "front_depth"
    if path.name == PORTABLE_LOW_DIM_FILENAME:
        return "portable_low_dimensional_state"
    if path.name == NATIVE_LOW_DIM_FILENAME:
        return "rlbench_demo"
    if path.name == VARIATION_NUMBER_FILENAME:
        return "rlbench_variation_number"
    if path.name == VARIATION_DESCRIPTIONS_FILENAME:
        return "rlbench_variation_descriptions"
    return "episode_payload"


def _episode_units(
    format_variant: DataCollectionFormat,
    source_arms: tuple[str, ...],
) -> dict[str, str]:
    joint_angle_unit = (
        "radians"
        if format_variant is DataCollectionFormat.RLBENCH_NATIVE
        else "degrees"
    )
    joint_velocity_unit = (
        "radians_per_second"
        if format_variant is DataCollectionFormat.RLBENCH_NATIVE
        else "degrees_per_second"
    )
    units = {
        "timestamp_utc_ns": "unix_nanoseconds_utc",
        "front_rgb": "uint8_bgr",
        "front_depth": "camera_device_units_uint16",
        "camera_intrinsics": "pixels",
        "camera_distortion_coefficients": "camera_model_coefficients",
        "depth_scale_metres": "metres_per_depth_unit",
        "camera_hardware_timestamps": "milliseconds_camera_clock",
        "camera_frame_numbers": "camera_sequence_number",
        "sample_sync_skew_ms": "milliseconds_host_monotonic_clock",
    }
    for arm_name in source_arms:
        prefix = f"{arm_name}_"
        units.update(
            {
                f"{prefix}sampled_at_utc_ns": "unix_nanoseconds_utc",
                f"{prefix}sampled_at_monotonic_ns": "host_monotonic_nanoseconds",
                f"{prefix}joint_positions": joint_angle_unit,
                f"{prefix}joint_velocities": joint_velocity_unit,
                f"{prefix}joint_currents": "amperes",
                f"{prefix}gripper_open": "normalized_0_closed_1_open",
                f"{prefix}gripper_force_newtons": "newtons",
                f"{prefix}gripper_raw_position": "provider_position_units",
                f"{prefix}gripper_pose": "xyz_metres_quaternion_xyzw",
                f"{prefix}end_effector_wrench": "fx_fy_fz_newtons_mx_my_mz_newton_metres",
            }
        )
    return units


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_temp_episode(name: str) -> bool:
    if not name.startswith(_TEMP_DIRECTORY_PREFIX):
        return False
    prefix, marker, identifier = name.partition(_TEMP_DIRECTORY_MARKER)
    episode_id = prefix.removeprefix(_TEMP_DIRECTORY_PREFIX)
    return (
        marker == _TEMP_DIRECTORY_MARKER
        and episode_id.isdigit()
        and bool(identifier)
        and all(character in _TEMP_IDENTIFIER_CHARACTERS for character in identifier)
    )


def _native_rlbench_types() -> tuple[type[Any], type[Any]]:
    from rlbench.backend.observation import Observation
    from rlbench.demo import Demo

    return Observation, Demo


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

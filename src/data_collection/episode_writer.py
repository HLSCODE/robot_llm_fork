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
from datetime import datetime, timezone
from pathlib import Path
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
    FrameRecord,
    normalize_descriptions,
    validate_source_arm,
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
        source_arm: str,
        clock: Callable[[], float] = time.time,
        identifier_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._save_path = Path(save_path)
        self._format_variant = format_variant
        self._storage_policy = storage_policy
        self._random_seed = random_seed
        self._source_arm = validate_source_arm(source_arm)
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
        fields, dimensions = _validate_frames(frames)
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
            metadata = EpisodeMetadata(
                format_variant=self._format_variant,
                task=normalized_task,
                source_arm=self._source_arm,
                episode_id=episode_id,
                variation_id=variation_id,
                descriptions=normalize_descriptions(description),
                frame_count=len(frames),
                capture_error_count=capture_error_count,
                created_at_utc=datetime.fromtimestamp(
                    self._clock(),
                    timezone.utc,
                ).isoformat(),
                fields=fields,
                units=_episode_units(self._format_variant),
                dimensions=dimensions,
                files=files,
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
                frame.joint_positions,
                frame.joint_velocities,
                frame.gripper_pose,
                frame.joint_forces,
                frame.gripper_matrix,
                frame.gripper_joint_positions,
            ):
                if isinstance(value, np.ndarray):
                    raw_bytes += value.nbytes
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
            "timestamps": np.asarray(
                [frame.timestamp for frame in frames],
                dtype=np.float64,
            ),
            "joint_positions": np.stack(
                [
                    _required_array(
                        frame.joint_positions,
                        "joint_positions",
                    )
                    for frame in frames
                ]
            ),
            "gripper_open": np.asarray(
                [frame.gripper_open for frame in frames],
                dtype=np.float32,
            ),
            "gripper_pose": np.stack(
                [
                    _required_array(frame.gripper_pose, "gripper_pose")
                    for frame in frames
                ]
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
        }
        for name in (
            "joint_velocities",
            "joint_forces",
            "gripper_matrix",
            "gripper_joint_positions",
        ):
            optional = _optional_stack(frames, name)
            if optional is not None:
                arrays[name] = optional

        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
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
        first_frame = frames[0]
        misc = {
            "front_camera_intrinsics": _required_array(
                first_frame.camera_intrinsics,
                "camera_intrinsics",
            ),
        }
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
                    np.deg2rad(frame.joint_velocities)
                    if frame.joint_velocities is not None
                    else None
                ),
                joint_positions=np.deg2rad(frame.joint_positions),
                joint_forces=frame.joint_forces,
                gripper_open=frame.gripper_open,
                gripper_pose=frame.gripper_pose,
                gripper_matrix=frame.gripper_matrix,
                gripper_touch_forces=None,
                gripper_joint_positions=(
                    np.deg2rad(frame.gripper_joint_positions)
                    if frame.gripper_joint_positions is not None
                    else None
                ),
                task_low_dim_state=None,
                ignore_collisions=True,
                misc=misc,
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
) -> tuple[dict[str, str], dict[str, tuple[int, ...]]]:
    if not frames:
        raise EpisodeIntegrityError("episode must contain at least one captured frame")
    required_arrays = (
        "front_rgb",
        "front_depth",
        "camera_intrinsics",
        "joint_positions",
        "gripper_pose",
    )
    optional_arrays = (
        "joint_velocities",
        "joint_forces",
        "gripper_matrix",
        "gripper_joint_positions",
    )
    dimensions: dict[str, tuple[int, ...]] = {}
    fields = {
        "timestamp": "required",
        "gripper_open": "required",
    }
    previous_timestamp: float | None = None
    for index, frame in enumerate(frames):
        timestamp = float(frame.timestamp)
        if not math.isfinite(timestamp):
            raise EpisodeIntegrityError(f"frame {index} timestamp must be finite")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise EpisodeIntegrityError(
                f"frame {index} timestamp is earlier than the previous frame"
            )
        previous_timestamp = timestamp
        if not math.isfinite(float(frame.gripper_open)):
            raise EpisodeIntegrityError(f"frame {index} gripper_open must be finite")
        if not 0.0 <= float(frame.gripper_open) <= 1.0:
            raise EpisodeIntegrityError(
                f"frame {index} gripper_open must be in range 0..1"
            )
        for name in required_arrays:
            array = _required_array(getattr(frame, name), name)
            _require_consistent_shape(dimensions, name, array, index)

        rgb = _required_array(frame.front_rgb, "front_rgb")
        depth = _required_array(frame.front_depth, "front_depth")
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
                f"frame {index} RGB/depth image dimensions do not match"
            )
        if frame.camera_intrinsics is None or (frame.camera_intrinsics.shape != (3, 3)):
            raise EpisodeIntegrityError(
                f"frame {index} camera_intrinsics must have shape (3, 3)"
            )
        if frame.camera_intrinsics[0, 0] <= 0 or frame.camera_intrinsics[1, 1] <= 0:
            raise EpisodeIntegrityError(
                f"frame {index} camera focal lengths must be positive"
            )
        joint_positions = _required_array(
            frame.joint_positions,
            "joint_positions",
        )
        if joint_positions.ndim != 1:
            raise EpisodeIntegrityError(
                f"frame {index} joint_positions must be one-dimensional"
            )
        gripper_pose = _required_array(frame.gripper_pose, "gripper_pose")
        if gripper_pose.shape != (7,):
            raise EpisodeIntegrityError(
                f"frame {index} gripper_pose must be xyz + quaternion xyzw"
            )
        quaternion_norm = float(np.linalg.norm(gripper_pose[3:]))
        if not math.isclose(quaternion_norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise EpisodeIntegrityError(
                f"frame {index} gripper pose quaternion must be normalized"
            )

    for name in required_arrays:
        fields[name] = "required"
    for name in optional_arrays:
        values = [getattr(frame, name) for frame in frames]
        present = [value is not None for value in values]
        if any(present) and not all(present):
            raise EpisodeIntegrityError(
                f"optional field {name} must be present for all frames or none"
            )
        fields[name] = "present" if all(present) else "absent"
        if all(present):
            for index, value in enumerate(values):
                array = _required_array(value, name)
                _require_consistent_shape(
                    dimensions,
                    name,
                    array,
                    index,
                )
    return fields, dimensions


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


def _optional_stack(
    frames: Sequence[FrameRecord],
    name: str,
) -> np.ndarray | None:
    values = [getattr(frame, name) for frame in frames]
    if not all(isinstance(value, np.ndarray) for value in values):
        return None
    return np.stack(values)


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
    return {
        "timestamp": "unix_seconds_utc",
        "front_rgb": "uint8_bgr",
        "front_depth": "camera_device_units_uint16",
        "camera_intrinsics": "pixels",
        "joint_positions": joint_angle_unit,
        "joint_velocities": joint_velocity_unit,
        "gripper_open": "normalized_0_closed_1_open",
        "gripper_pose": "xyz_metres_quaternion_xyzw",
        "joint_forces": "provider_reported_units",
        "gripper_matrix": "homogeneous_transform_metres",
        "gripper_joint_positions": joint_angle_unit,
    }


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


def _native_rlbench_types():
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

from __future__ import annotations

import json
import os
import pickle
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.data_collection.config import DataCollectionConfig
from src.data_collection.episode_writer import (
    DataCollectionEpisodeWriter,
    DataCollectionStoragePolicy,
    EpisodeAlreadyExistsError,
    EpisodeFormatUnavailableError,
    EpisodeIntegrityError,
    EpisodeWriteError,
    InsufficientStorageError,
)
from src.data_collection.recorder import FrameData
from src.data_collection.schema import (
    DATA_COLLECTION_SCHEMA_NAME,
    DATA_COLLECTION_SCHEMA_VERSION,
    EPISODE_METADATA_FILENAME,
    PORTABLE_LOW_DIM_FILENAME,
    DataCollectionFormat,
)
from src.data_collection.validation import (
    main as validation_main,
)
from src.data_collection.validation import (
    validate_dataset,
    validate_episode,
)


class DataCollectionStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.dataset_path = Path(self.temporary_directory.name)
        self.writer = self._writer()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_portable_episode_is_versioned_verified_and_atomically_published(self):
        result = self.writer.save_episode(
            task="pick_bottle",
            episode_id=0,
            frames=_frames(2),
            description="pick bottle,place bottle",
        )

        self.assertTrue(result.episode_path.is_dir())
        self.assertFalse(list(result.episode_path.parent.glob(".*.tmp-*")))
        self.assertTrue((result.episode_path / PORTABLE_LOW_DIM_FILENAME).is_file())
        self.assertFalse((result.episode_path / "low_dim_obs.pkl").exists())

        metadata = json.loads(
            (result.episode_path / EPISODE_METADATA_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(DATA_COLLECTION_SCHEMA_NAME, metadata["schema_name"])
        self.assertEqual(
            DATA_COLLECTION_SCHEMA_VERSION,
            metadata["schema_version"],
        )
        self.assertEqual(
            DataCollectionFormat.PORTABLE_SIMPLIFIED.value,
            metadata["format_variant"],
        )
        self.assertEqual("left", metadata["source_arm"])
        self.assertEqual("degrees", metadata["units"]["joint_positions"])
        self.assertEqual(2, metadata["frame_count"])
        self.assertEqual(
            ["pick bottle", "place bottle"],
            metadata["descriptions"],
        )
        self.assertTrue(metadata["files"])

        report = validate_episode(result.episode_path)
        self.assertTrue(report.valid, report.to_dict())
        self.assertEqual(len(metadata["files"]), report.checked_files)

    def test_native_format_requires_explicit_optional_dependency(self):
        writer = self._writer(format_variant=DataCollectionFormat.RLBENCH_NATIVE)
        with patch(
            "src.data_collection.episode_writer._native_rlbench_types",
            side_effect=ImportError("rlbench unavailable"),
        ):
            with self.assertRaises(EpisodeFormatUnavailableError):
                writer.prepare_session("pick")

        self.assertFalse((self.dataset_path / "pick").exists())

    def test_native_format_uses_real_types_and_converts_joint_units(self):
        writer = self._writer(format_variant=DataCollectionFormat.RLBENCH_NATIVE)
        with patch(
            "src.data_collection.episode_writer._native_rlbench_types",
            return_value=(_FakeObservation, _FakeDemo),
        ):
            result = writer.save_episode(
                task="pick",
                episode_id=0,
                frames=_frames(1),
                description="test",
            )

        with (result.episode_path / "low_dim_obs.pkl").open("rb") as file:
            demo = pickle.load(file)

        self.assertIsInstance(demo, _FakeDemo)
        np.testing.assert_allclose(
            np.deg2rad(np.arange(7, dtype=np.float64)),
            demo.observations[0].joint_positions,
        )
        self.assertNotIn(
            "front_camera_extrinsics",
            demo.observations[0].misc,
        )
        self.assertEqual(
            "radians",
            result.metadata.units["joint_positions"],
        )
        report = validate_episode(result.episode_path)
        self.assertTrue(report.valid, report.to_dict())
        self.assertIn(
            "native_pickle_not_inspected",
            {issue.code for issue in report.issues},
        )

    def test_capacity_preflight_fails_before_creating_staged_episode(self):
        writer = self._writer(minimum_free_bytes=1000)
        with patch(
            "src.data_collection.episode_writer.shutil.disk_usage",
            return_value=SimpleNamespace(free=100),
        ):
            with self.assertRaises(InsufficientStorageError) as raised:
                writer.save_episode(
                    task="pick",
                    episode_id=0,
                    frames=_frames(1),
                    description="test",
                )

        self.assertGreater(raised.exception.required_bytes, 100)
        episodes_path = self.dataset_path / "pick" / "all_variations" / "episodes"
        self.assertFalse(list(episodes_path.glob("episode*")))
        self.assertFalse(list(episodes_path.glob(".*.tmp-*")))

    def test_staged_write_failure_leaves_no_visible_or_temporary_episode(self):
        with patch.object(
            self.writer,
            "_write_format_payload",
            side_effect=OSError("simulated write failure"),
        ):
            with self.assertRaisesRegex(
                EpisodeWriteError,
                "simulated write failure",
            ):
                self.writer.save_episode(
                    task="pick",
                    episode_id=0,
                    frames=_frames(1),
                    description="test",
                )

        episodes_path = self.dataset_path / "pick" / "all_variations" / "episodes"
        self.assertFalse((episodes_path / "episode0").exists())
        self.assertFalse(list(episodes_path.glob(".*.tmp-*")))

    def test_existing_episode_is_never_overwritten(self):
        first = self.writer.save_episode(
            task="pick",
            episode_id=0,
            frames=_frames(1),
            description="first",
        )
        metadata_before = (first.episode_path / EPISODE_METADATA_FILENAME).read_bytes()

        with self.assertRaises(EpisodeAlreadyExistsError):
            self.writer.save_episode(
                task="pick",
                episode_id=0,
                frames=_frames(1),
                description="second",
            )

        self.assertEqual(
            metadata_before,
            (first.episode_path / EPISODE_METADATA_FILENAME).read_bytes(),
        )

    def test_prepare_session_recovers_only_stale_scoped_temp_directories(self):
        writer = self._writer(clock=lambda: 10_000.0, stale_seconds=100.0)
        episodes_path = self.dataset_path / "pick" / "all_variations" / "episodes"
        episodes_path.mkdir(parents=True)
        stale = episodes_path / ".episode0.tmp-a1"
        fresh = episodes_path / ".episode1.tmp-b2"
        unrelated = episodes_path / "keep-me"
        stale.mkdir()
        fresh.mkdir()
        unrelated.mkdir()
        os.utime(stale, (9_000.0, 9_000.0))
        os.utime(fresh, (9_950.0, 9_950.0))

        status = writer.prepare_session("pick")

        self.assertEqual((stale,), status.recovered_paths)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())

    def test_validator_detects_corruption_without_unpickling(self):
        result = self.writer.save_episode(
            task="pick",
            episode_id=0,
            frames=_frames(1),
            description="test",
        )
        rgb_path = result.episode_path / "front_rgb" / "0.png"
        rgb_path.write_bytes(b"corrupted")

        report = validate_episode(result.episode_path)

        self.assertFalse(report.valid)
        codes = {issue.code for issue in report.issues}
        self.assertIn("checksum_mismatch", codes)
        self.assertIn("image_decode_failed", codes)

    def test_dataset_validator_and_cli_report_valid_and_invalid_data(self):
        result = self.writer.save_episode(
            task="pick",
            episode_id=0,
            frames=_frames(1),
            description="test",
        )

        report = validate_dataset(self.dataset_path)
        self.assertTrue(report.valid)
        with patch("builtins.print"):
            self.assertEqual(
                0,
                validation_main([str(self.dataset_path), "--json"]),
            )

        (result.episode_path / PORTABLE_LOW_DIM_FILENAME).unlink()
        invalid = validate_dataset(self.dataset_path)
        self.assertFalse(invalid.valid)
        with patch("builtins.print"):
            self.assertEqual(
                1,
                validation_main([str(self.dataset_path)]),
            )

    def test_dataset_validator_rejects_empty_or_missing_task(self):
        empty = validate_dataset(self.dataset_path)
        missing_task = validate_dataset(
            self.dataset_path,
            task="missing",
        )

        self.assertFalse(empty.valid)
        self.assertIn(
            "no_episodes",
            {issue.code for issue in empty.issues},
        )
        self.assertFalse(missing_task.valid)
        self.assertIn(
            "task_not_found",
            {issue.code for issue in missing_task.issues},
        )

    def test_invalid_frames_and_task_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            self.writer.save_episode(
                task="../escape",
                episode_id=0,
                frames=_frames(1),
                description="test",
            )
        with self.assertRaises(EpisodeIntegrityError):
            self.writer.save_episode(
                task="pick",
                episode_id=0,
                frames=[],
                description="test",
            )
        self.assertEqual([], list(self.dataset_path.iterdir()))

    def test_environment_config_is_typed_and_validated(self):
        config = DataCollectionConfig.from_environment(
            {
                "DATA_COLLECTION_FPS": "15",
                "DATA_COLLECTION_CAMERA_INDEX": "2",
                "DATA_COLLECTION_ARM": "right",
                "DATA_COLLECTION_SAVE_PATH": "custom/demos",
                "DATA_COLLECTION_FORMAT_VARIANT": "portable_simplified",
                "DATA_COLLECTION_MIN_FREE_BYTES": "2048",
                "DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR": "1.5",
                "DATA_COLLECTION_STALE_WRITE_SECONDS": "120",
                "DATA_COLLECTION_RANDOM_SEED": "7",
                "DATA_COLLECTION_STOP_TIMEOUT_SECONDS": "3",
            },
            load_project_dotenv=False,
        )

        self.assertEqual(15, config.fps)
        self.assertEqual(2, config.camera_index)
        self.assertEqual("right", config.arm_id.value)
        self.assertEqual(Path("custom/demos"), config.save_path)
        self.assertEqual(
            DataCollectionFormat.PORTABLE_SIMPLIFIED,
            config.format_variant,
        )
        self.assertEqual(2048, config.storage_policy.minimum_free_bytes)
        self.assertEqual(1.5, config.storage_policy.overhead_factor)
        self.assertEqual(120, config.storage_policy.stale_write_seconds)
        self.assertEqual(7, config.random_seed)
        self.assertEqual(3, config.recording_stop_timeout_seconds)

        with self.assertRaisesRegex(ValueError, "FPS"):
            DataCollectionConfig.from_environment(
                {"DATA_COLLECTION_FPS": "invalid"},
                load_project_dotenv=False,
            )
        with self.assertRaisesRegex(ValueError, "SAVE_PATH"):
            DataCollectionConfig.from_environment(
                {"DATA_COLLECTION_SAVE_PATH": "  "},
                load_project_dotenv=False,
            )

    def _writer(
        self,
        *,
        format_variant: DataCollectionFormat = (
            DataCollectionFormat.PORTABLE_SIMPLIFIED
        ),
        minimum_free_bytes: int = 0,
        stale_seconds: float = 3600.0,
        clock=None,
    ) -> DataCollectionEpisodeWriter:
        options = {
            "format_variant": format_variant,
            "storage_policy": DataCollectionStoragePolicy(
                minimum_free_bytes=minimum_free_bytes,
                overhead_factor=1.0,
                stale_write_seconds=stale_seconds,
            ),
            "random_seed": 42,
            "source_arm": "left",
            "identifier_factory": lambda: "abc123",
        }
        if clock is not None:
            options["clock"] = clock
        return DataCollectionEpisodeWriter(
            self.dataset_path,
            **options,
        )


def _frames(count: int) -> list[FrameData]:
    return [
        FrameData(
            timestamp=float(index),
            front_rgb=np.full(
                (4, 5, 3),
                index,
                dtype=np.uint8,
            ),
            front_depth=np.full(
                (4, 5),
                index + 1,
                dtype=np.uint16,
            ),
            camera_intrinsics=np.asarray(
                [
                    [100.0, 0.0, 2.0],
                    [0.0, 100.0, 2.0],
                    [0.0, 0.0, 1.0],
                ]
            ),
            joint_positions=np.arange(7, dtype=np.float64) + index,
            gripper_open=0.0,
            gripper_pose=np.asarray(
                [float(index), 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                dtype=np.float64,
            ),
        )
        for index in range(count)
    ]


class _FakeObservation:
    def __init__(self, **values):
        self.__dict__.update(values)


class _FakeDemo:
    def __init__(self, observations, *, random_seed):
        self.observations = observations
        self.random_seed = random_seed
        self.variation_number = None


if __name__ == "__main__":
    unittest.main()

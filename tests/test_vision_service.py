from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from src.application import create_application_services
from src.domain.execution_context import ExecutionContext, VisionRelocalizationState
from src.persistence.json_documents import JsonDocumentSchemaError
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import (
    ApplicationSettings,
    CameraProfile,
    CameraRole,
    VisionSettings,
)
from src.persistence.vision_station_storage import VisionStationStorage
from src.execution import ExecutionState
from src.vision.artifacts import VisionArtifactStore
from src.vision.models import (
    VisionOperation,
    VisionPipelineResult,
    vision_configuration,
)
from src.vision.service import VisionService
from src.vision.relocalization.service import compensate_pose_with_context


class VisionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = VisionSettings(
            vision_model_version="detector-2026.08",
            vision_calibration_version="cell-a-3",
            vision_debug_save_dir=str(self.root / "debug"),
            vision_relocalization_stations_file=str(self.root / "stations.json"),
        )

    def test_capture_returns_typed_result_and_atomically_published_artifact(self) -> None:
        def pipeline(
            _robot,
            _camera,
            _parameters,
            _settings,
            _log,
            debug_directory,
        ) -> VisionPipelineResult:
            Path(debug_directory, "frame.jpg").write_bytes(b"fixture-image")
            return VisionPipelineResult(True, frames_processed=2, inference_count=1)

        clock = iter((10.0, 10.04))

        service = VisionService(
            self.settings,
            ExecutionContext(),
            capture_pipeline=pipeline,
            clock=lambda: next(clock),
        )

        result = service.capture(object(), object(), {"workflow": "fixture"}, lambda _message: None)

        self.assertTrue(result.successful)
        self.assertEqual(VisionOperation.CAPTURE, result.operation)
        self.assertEqual(["image"], [artifact.kind for artifact in result.artifacts])
        artifact = result.artifacts[0]
        self.assertTrue(artifact.path.is_file())
        self.assertFalse(artifact.path.parent.name.startswith("."))
        manifest = json.loads((artifact.path.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["successful"])
        self.assertEqual("detector-2026.08", manifest["configuration"]["model_version"])
        metrics = service.metrics_snapshot()
        self.assertEqual(1, metrics.operations_succeeded_total)
        self.assertEqual(2, metrics.frames_processed_total)
        self.assertEqual(1, metrics.inference_count_total)
        self.assertAlmostEqual(
            50.0,
            metrics.to_dict()["observed_processing_fps"],
        )
        self.assertEqual("detector-2026.08", metrics.model_version)

    def test_pipeline_exception_publishes_failed_run_and_preserves_exception(self) -> None:
        def pipeline(*_args) -> VisionPipelineResult:
            debug_directory = Path(_args[-1])
            (debug_directory / "failure.txt").write_text("diagnostic", encoding="utf-8")
            raise RuntimeError("fixture failure")

        service = VisionService(
            self.settings,
            ExecutionContext(),
            capture_pipeline=pipeline,
        )

        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            service.capture(object(), object(), {}, lambda _message: None)

        manifests = list((self.root / "debug").rglob("manifest.json"))
        self.assertEqual(1, len(manifests))
        self.assertFalse(json.loads(manifests[0].read_text(encoding="utf-8"))["successful"])
        self.assertEqual(1, service.metrics_snapshot().operations_failed_total)

    def test_rejected_pipeline_is_counted_separately_from_internal_failure(self) -> None:
        service = VisionService(
            self.settings,
            ExecutionContext(),
            capture_pipeline=lambda *_args: VisionPipelineResult(
                False,
                frames_processed=1,
                inference_count=1,
            ),
        )

        result = service.capture(object(), object(), {}, lambda _message: None)

        self.assertFalse(result.successful)
        metrics = service.metrics_snapshot()
        self.assertEqual(1, metrics.operations_rejected_total)
        self.assertEqual(0, metrics.operations_failed_total)
        self.assertEqual(1, metrics.frames_processed_total)

    def test_artifact_cleanup_removes_stale_temporary_and_bounds_completed_runs(self) -> None:
        store = VisionArtifactStore(
            self.root / "debug",
            retention_days=1,
            max_runs=1,
            configuration=vision_configuration(self.settings),
        )
        first = store.begin(VisionOperation.CAPTURE)
        first.finish(successful=True)
        earlier = time.time() - 10
        os.utime(first.final_directory, (earlier, earlier))
        second = store.begin(VisionOperation.CAPTURE)
        second.finish(successful=True)
        stale = store.root / "capture" / ".abandoned.tmp"
        stale.mkdir()
        old = time.time() - (2 * 24 * 60 * 60)
        os.utime(stale, (old, old))

        store.cleanup()

        self.assertFalse(first.final_directory.exists())
        self.assertTrue(second.final_directory.exists())
        self.assertFalse(stale.exists())

    def test_station_choices_are_exposed_through_the_vision_service(self) -> None:
        storage = VisionStationStorage(
            self.root / "stations.json",
            configuration=vision_configuration(self.settings),
        )
        storage.upsert_profile(
            {
                "station_id": "station-left",
                "station_name": "装粉工位",
                "arm": "left",
                "T_B0_M": [[1.0]],
            }
        )
        service = VisionService(
            self.settings,
            ExecutionContext(),
            station_storage=storage,
        )

        self.assertEqual(
            [("station-left", "装粉工位 (左臂)")],
            service.list_station_choices("left"),
        )
        self.assertEqual([], service.list_station_choices("right"))

    def test_camera_choices_are_exposed_without_initializing_hardware(self) -> None:
        settings = replace(
            self.settings,
            cameras=(
                CameraProfile(
                    name="monitor1",
                    label="左臂视觉相机",
                    provider="realsense",
                    device_id="serial-left",
                    roles=(CameraRole.RELOCALIZATION.value,),
                    arms=("left",),
                    camera_matrix=(
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ),
                    end_effector_to_camera=(
                        (1.0, 0.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0, 0.0),
                        (0.0, 0.0, 0.0, 1.0),
                    ),
                ),
            ),
        )
        service = VisionService(settings, ExecutionContext())

        self.assertEqual(
            [("monitor1", "左臂视觉相机")],
            service.list_camera_choices(CameraRole.RELOCALIZATION, arm="left"),
        )
        self.assertEqual(
            [],
            service.list_camera_choices(CameraRole.RELOCALIZATION, arm="right"),
        )

    def test_compensation_uses_camera_recorded_by_this_relocalization_run(self) -> None:
        identity = (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
        settings = replace(
            self.settings,
            cameras=(
                CameraProfile(
                    name="left-default-uncalibrated",
                    provider="realsense",
                    device_id="serial-default",
                    roles=(CameraRole.RELOCALIZATION.value,),
                    arms=("left",),
                    camera_matrix=(
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ),
                    end_effector_to_camera=identity,
                ),
                CameraProfile(
                    name="left-calibrated",
                    provider="realsense",
                    device_id="serial-calibrated",
                    roles=(CameraRole.RELOCALIZATION.value,),
                    arms=("left",),
                    camera_matrix=(
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ),
                    end_effector_to_camera=identity,
                ),
            ),
        )
        storage = VisionStationStorage(
            settings.vision_relocalization_stations_file,
            configuration=vision_configuration(settings),
        )
        storage.upsert_profile(
            {
                "station_id": "station-left",
                "station_name": "左臂工位",
                "arm": "left",
                "T_B0_M": [list(row) for row in identity],
            }
        )
        context = ExecutionContext()
        context.set_vision_state(
            VisionRelocalizationState(
                station_id="station-left",
                arm="left",
                marker_pose=[list(row) for row in identity],
                camera_name="left-calibrated",
            )
        )

        with patch(
            "src.vision.relocalization.service.compensate_taught_pose",
            return_value=[1.0, 2.0, 3.0, 0.0, 0.0, 0.0],
        ) as compensate:
            result = compensate_pose_with_context(
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "station-left",
                "left",
                context,
                settings,
            )

        self.assertEqual([1.0, 2.0, 3.0, 0.0, 0.0, 0.0], result)
        compensation_config = compensate.call_args.args[3]
        self.assertEqual("left-calibrated", compensation_config["camera_name"])


class VisionStationStorageTests(unittest.TestCase):
    def test_profiles_require_versioned_document_and_active_calibration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stations.json"
            settings = VisionSettings(
                vision_model_version="model-1",
                vision_calibration_version="calibration-1",
            )
            storage = VisionStationStorage(
                path,
                configuration=vision_configuration(settings),
            )
            saved = storage.upsert_profile(
                {
                    "station_id": "station-a",
                    "arm": "left",
                    "T_B0_M": [[1.0]],
                }
            )

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("robot-llm.vision-stations", document["schema"])
            self.assertEqual(1, document["schema_version"])
            self.assertEqual(1, saved["profile_version"])
            self.assertEqual("model-1", saved["model_version"])
            self.assertEqual("calibration-1", saved["calibration_version"])

            mismatched = VisionStationStorage(
                path,
                configuration=vision_configuration(
                    replace(settings, vision_calibration_version="calibration-2")
                ),
            )
            with self.assertRaisesRegex(JsonDocumentSchemaError, "calibration_version"):
                mismatched.load_profiles()

    def test_legacy_station_document_is_backed_up_and_migrated(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stations.json"
            legacy_document = {
                "version": 1,
                "profiles": [
                    {
                        "station_id": "legacy-station",
                        "station_name": "旧工位",
                        "arm": "left",
                        "T_B0_M": [[1.0]],
                    }
                ],
            }
            path.write_text(
                json.dumps(legacy_document, ensure_ascii=False),
                encoding="utf-8",
            )
            storage = VisionStationStorage(
                path,
                configuration=vision_configuration(
                    VisionSettings(
                        vision_model_version="model-current",
                        vision_calibration_version="calibration-current",
                    )
                ),
            )

            with self.assertRaisesRegex(JsonDocumentSchemaError, "robot-init migrate-data"):
                storage.load_profiles()
            self.assertEqual(
                legacy_document,
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertFalse(path.with_name("stations.json.v0.bak").exists())

            self.assertTrue(storage.migrate_legacy_document())
            profiles = storage.load_profiles()

            self.assertEqual("legacy-station", profiles[0]["station_id"])
            self.assertEqual(1, profiles[0]["profile_version"])
            self.assertEqual("model-current", profiles[0]["model_version"])
            self.assertEqual("calibration-current", profiles[0]["calibration_version"])
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("robot-llm.vision-stations", migrated["schema"])
            self.assertEqual(1, migrated["schema_version"])
            backup = path.with_name("stations.json.v0.bak")
            self.assertEqual(legacy_document, json.loads(backup.read_text(encoding="utf-8")))
            self.assertFalse(storage.migrate_legacy_document())

    def test_unknown_legacy_station_version_is_rejected_without_rewriting(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stations.json"
            original = '{"version": 2, "profiles": []}'
            path.write_text(original, encoding="utf-8")
            storage = VisionStationStorage(
                path,
                configuration=vision_configuration(VisionSettings()),
            )

            with self.assertRaisesRegex(JsonDocumentSchemaError, "legacy version 2"):
                storage.migrate_legacy_document()

            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_name("stations.json.v0.bak").exists())


class VisionSimulationIntegrationTests(unittest.TestCase):
    def test_application_startup_rejects_legacy_station_document_without_writing(self) -> None:
        with TemporaryDirectory() as directory:
            station_path = Path(directory) / "stations.json"
            legacy_document = {
                "version": 1,
                "profiles": [
                    {
                        "station_id": "startup-station",
                        "arm": "left",
                        "T_B0_M": [[1.0]],
                    }
                ],
            }
            original = json.dumps(legacy_document)
            station_path.write_text(original, encoding="utf-8")
            defaults = ApplicationSettings.defaults()
            settings = replace(
                defaults,
                vision=replace(
                    defaults.vision,
                    vision_relocalization_stations_file=str(station_path),
                ),
            )

            with self.assertRaisesRegex(JsonDocumentSchemaError, "robot-init migrate-data"):
                create_application_services(settings, simulation=True)

            self.assertEqual(original, station_path.read_text(encoding="utf-8"))
            self.assertFalse(station_path.with_name("stations.json.v0.bak").exists())

    def test_simulation_fixture_executes_capture_and_relocalization_without_models(self) -> None:
        with TemporaryDirectory() as directory:
            defaults = ApplicationSettings.defaults()
            settings = replace(
                defaults,
                vision=replace(
                    defaults.vision,
                    vision_debug_save_dir=str(Path(directory) / "debug"),
                    vision_relocalization_stations_file=str(Path(directory) / "stations.json"),
                ),
            )
            services = create_application_services(settings, simulation=True)
            try:
                sequence = [
                    SequenceItem.from_definition(
                        ActionDefinition(
                            id="capture",
                            name="capture",
                            type=ActionType.VISION_CAPTURE,
                            parameters={"workflow": "fixture"},
                        )
                    ),
                    SequenceItem.from_definition(
                        ActionDefinition(
                            id="relocalize",
                            name="relocalize",
                            type=ActionType.VISION_RELOCALIZE,
                            parameters={"station_id": "fixture-station", "arm": "left"},
                        )
                    ),
                ]
                handle = services.execution.start_entries(
                    sequence,
                    origin="vision-fixture-test",
                )
                self.assertTrue(handle.wait(timeout=2))
                self.assertEqual(ExecutionState.SUCCEEDED, handle.snapshot().state)
                artifacts = list((Path(directory) / "debug").rglob("fixture.json"))
                self.assertEqual(2, len(artifacts))
            finally:
                services.external_localization.close()
                services.devices.shutdown_all()

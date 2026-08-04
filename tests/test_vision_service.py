from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from src.application import create_application_services
from src.core.execution_context import ExecutionContext
from src.core.json_documents import JsonDocumentSchemaError
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.settings import ApplicationSettings, VisionSettings
from src.core.vision_station_storage import VisionStationStorage
from src.execution import ExecutionState
from src.vision.artifacts import VisionArtifactStore
from src.vision.models import VisionOperation, vision_configuration
from src.vision.service import VisionService


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
        ) -> bool:
            Path(debug_directory, "frame.jpg").write_bytes(b"fixture-image")
            return True

        service = VisionService(
            self.settings,
            ExecutionContext(),
            capture_pipeline=pipeline,
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

    def test_pipeline_exception_publishes_failed_run_and_preserves_exception(self) -> None:
        def pipeline(*_args) -> bool:
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

    def test_unversioned_station_document_is_rejected_without_compatibility_path(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stations.json"
            path.write_text("[]", encoding="utf-8")
            storage = VisionStationStorage(
                path,
                configuration=vision_configuration(VisionSettings()),
            )
            with self.assertRaisesRegex(JsonDocumentSchemaError, "unversioned legacy"):
                storage.load_profiles()


class VisionSimulationIntegrationTests(unittest.TestCase):
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
                handle = services.execution.start(sequence, origin="vision-fixture-test")
                self.assertTrue(handle.wait(timeout=2))
                self.assertEqual(ExecutionState.SUCCEEDED, handle.snapshot().state)
                artifacts = list((Path(directory) / "debug").rglob("fixture.json"))
                self.assertEqual(2, len(artifacts))
            finally:
                services.localization.close()
                services.devices.shutdown_all()

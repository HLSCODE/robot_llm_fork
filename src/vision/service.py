from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Protocol

from ..core.execution_context import ExecutionContext
from ..core.settings import VisionSettings
from ..core.vision_station_storage import VisionStationStorage
from ..device_runtime import CameraSource, DepthCameraSource, RobotSystem
from .artifacts import VisionArtifactStore
from .models import (
    VisionOperation,
    VisionResult,
    VisionResultCode,
    vision_configuration,
)

VisionLog = Callable[[str], None]


class CapturePipeline(Protocol):
    def __call__(
        self,
        robot_system: RobotSystem,
        camera: DepthCameraSource,
        parameters: dict[str, object],
        settings: VisionSettings,
        log: VisionLog,
        debug_directory: str,
    ) -> bool: ...


class RelocalizationPipeline(Protocol):
    def __call__(
        self,
        robot_system: RobotSystem,
        camera: CameraSource,
        parameters: dict[str, object],
        execution_context: ExecutionContext,
        settings: VisionSettings,
        station_storage: VisionStationStorage,
        debug_directory: str,
        log: VisionLog,
    ) -> bool: ...


class VisionService:
    """Run vision pipelines with typed results and owned debug artifacts."""

    def __init__(
        self,
        settings: VisionSettings,
        execution_context: ExecutionContext,
        *,
        capture_pipeline: CapturePipeline | None = None,
        relocalization_pipeline: RelocalizationPipeline | None = None,
        artifact_store: VisionArtifactStore | None = None,
        station_storage: VisionStationStorage | None = None,
    ) -> None:
        self._settings = settings
        self._execution_context = execution_context
        configuration = vision_configuration(settings)
        self._artifact_store = artifact_store or VisionArtifactStore(
            settings.vision_debug_save_dir,
            retention_days=settings.vision_debug_retention_days,
            max_runs=settings.vision_debug_max_runs,
            configuration=configuration,
        )
        self._station_storage = station_storage or VisionStationStorage(
            settings.vision_relocalization_stations_file,
            configuration=configuration,
        )
        self._capture_pipeline = capture_pipeline
        self._relocalization_pipeline = relocalization_pipeline

    def capture(
        self,
        robot_system: RobotSystem,
        camera: DepthCameraSource,
        parameters: dict[str, object],
        log: VisionLog,
    ) -> VisionResult:
        return self._run(
            VisionOperation.CAPTURE,
            lambda debug_directory: self._capture_executor()(
                robot_system,
                camera,
                parameters,
                self._settings,
                log,
                debug_directory,
            ),
        )

    def relocalize(
        self,
        robot_system: RobotSystem,
        camera: CameraSource,
        parameters: dict[str, object],
        log: VisionLog,
    ) -> VisionResult:
        return self._run(
            VisionOperation.RELOCALIZATION,
            lambda debug_directory: self._relocalization_executor()(
                robot_system,
                camera,
                parameters,
                self._execution_context,
                self._settings,
                self._station_storage,
                debug_directory,
                log,
            ),
        )

    def _run(
        self,
        operation: VisionOperation,
        pipeline: Callable[[str], bool],
    ) -> VisionResult:
        with self._artifact_store.begin(operation) as run:
            successful = pipeline(str(run.staging_directory))
            artifacts = run.finish(successful=successful)
        code = VisionResultCode.SUCCEEDED if successful else VisionResultCode.REJECTED
        return VisionResult(
            operation=operation,
            code=code,
            message=(
                f"vision {operation.value} succeeded"
                if successful
                else f"vision {operation.value} rejected"
            ),
            artifacts=artifacts,
            metadata=MappingProxyType({"run_id": run.run_id}),
        )

    def _capture_executor(self) -> CapturePipeline:
        if self._capture_pipeline is None:
            from .executor import execute_vision_capture

            self._capture_pipeline = execute_vision_capture
        return self._capture_pipeline

    def _relocalization_executor(self) -> RelocalizationPipeline:
        if self._relocalization_pipeline is None:
            from .relocalization import execute_vision_relocalization

            self._relocalization_pipeline = execute_vision_relocalization
        return self._relocalization_pipeline

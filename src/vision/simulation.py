from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

from ..domain.arm_names import normalize_arm_name
from ..domain.execution_context import ExecutionContext, VisionRelocalizationState
from ..persistence.json_documents import write_json_atomic
from ..configuration.settings import VisionSettings
from ..persistence.vision_station_storage import VisionStationStorage
from ..devices import CameraSource, DepthCameraSource, RobotSystem
from .models import VisionPipelineResult

_IDENTITY_MATRIX = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


@dataclass(slots=True)
class VisionPipelineFixture:
    """Deterministic no-hardware pipeline used by simulation composition roots."""

    capture_calls: list[dict[str, object]] = field(default_factory=list)
    relocalization_calls: list[dict[str, object]] = field(default_factory=list)

    def capture(
        self,
        _robot_system: RobotSystem,
        _camera: DepthCameraSource,
        parameters: dict[str, object],
        _settings: VisionSettings,
        log: Callable[[str], None],
        debug_directory: str,
    ) -> VisionPipelineResult:
        self.capture_calls.append(dict(parameters))
        self._write_fixture(debug_directory, "capture", parameters)
        log("simulation vision capture completed")
        return VisionPipelineResult(True, frames_processed=1, inference_count=1)

    def relocalize(
        self,
        _robot_system: RobotSystem,
        _camera: CameraSource,
        parameters: dict[str, object],
        execution_context: ExecutionContext,
        _settings: VisionSettings,
        _station_storage: VisionStationStorage,
        debug_directory: str,
        log: Callable[[str], None],
    ) -> VisionPipelineResult:
        self.relocalization_calls.append(dict(parameters))
        station_id = str(parameters.get("station_id") or "simulation-station")
        arm = normalize_arm_name(str(parameters.get("arm") or "left"))
        self._write_fixture(debug_directory, "relocalization", parameters)
        execution_context.set_vision_state(
            VisionRelocalizationState(
                station_id=station_id,
                arm=arm,
                marker_pose=_IDENTITY_MATRIX,
                camera_name="simulation-camera",
                metadata={"fixture": True},
            )
        )
        log("simulation vision relocalization completed")
        return VisionPipelineResult(True, frames_processed=1, inference_count=1)

    @staticmethod
    def _write_fixture(
        debug_directory: str,
        operation: str,
        parameters: dict[str, object],
    ) -> None:
        write_json_atomic(
            Path(debug_directory) / "fixture.json",
            {
                "schema": "robot-llm.vision-fixture",
                "schema_version": 1,
                "operation": operation,
                "parameters": parameters,
            },
        )

from __future__ import annotations

from typing import Any

from ..core.data_paths import ApplicationDataPaths
from ..core.storage import JsonCompositionRepository
from ..device_runtime import (
    ArmTelemetryReader,
    DepthCameraSource,
    ResourceArbiter,
)
from ..device_runtime.factory import create_device_runtime
from ..execution.engine import ActionEngine
from ..execution.manager import ExecutionManager
from ..skill_system import SkillEngine
from .camera_access import CameraAccessService
from .builtin_data import BuiltinDataInstaller
from .command_runtime import CommandRuntime
from .composition import CompositionService
from .data_collection import (
    DataCollectionRecorder,
    DataCollectionService,
)
from .safety import SafetyService
from .services import (
    ApplicationServices,
    DeviceManagementService,
    ExecutionService,
    ManualControlService,
    RobotQueryService,
    TeleoperationService,
    TrajectoryTeachingService,
)


def create_application_services(
    config: Any,
    *,
    simulation: bool,
) -> ApplicationServices:
    data_paths = ApplicationDataPaths.from_config(config)
    BuiltinDataInstaller(data_paths).install_missing()
    composition = CompositionService(
        JsonCompositionRepository(
            actions_file=data_paths.actions_file,
            tasks_directory=data_paths.tasks_directory,
        )
    )
    device_runtime = create_device_runtime(config, simulation=simulation)
    resources = ResourceArbiter()
    engine = ActionEngine(device_runtime, config)
    manager = ExecutionManager(
        engine=engine,
        resource_arbiter=resources,
        execution_resources=engine.required_resources,
    )
    execution = ExecutionService(manager)
    skill_engine = SkillEngine()
    skill_engine.load_skills(str(data_paths.skills_file))
    commands = CommandRuntime(
        execution=execution,
        skill_engine=skill_engine,
        preview_ttl_s=float(getattr(config, "COMMAND_PREVIEW_TTL_SECONDS", 120.0)),
    )
    manual_control = ManualControlService(device_runtime, resources)
    camera_access = CameraAccessService(device_runtime, resources)
    teleoperation = TeleoperationService(device_runtime, resources)
    robot_query = RobotQueryService(device_runtime)
    trajectory_teaching = TrajectoryTeachingService(
        device_runtime,
        resources,
    )
    safety = SafetyService(
        execution,
        device_runtime,
        teleoperation,
        trajectory_teaching,
        wait_timeout_seconds=float(
            getattr(config, "SAFETY_STOP_WAIT_TIMEOUT_SECONDS", 2.0)
        ),
    )
    devices = DeviceManagementService(
        device_runtime,
        resources,
        safety,
    )
    data_collection = DataCollectionService(
        camera_access=camera_access,
        devices=devices,
        robot_query=robot_query,
        teleoperation=teleoperation,
        recorder_factory=_create_data_collection_recorder,
    )
    safety.register_control_session(
        "data collection",
        data_collection.close,
    )
    return ApplicationServices(
        camera_access=camera_access,
        composition=composition,
        data_collection=data_collection,
        execution=execution,
        devices=devices,
        manual_control=manual_control,
        teleoperation=teleoperation,
        robot_query=robot_query,
        trajectory_teaching=trajectory_teaching,
        safety=safety,
        commands=commands,
        device_runtime=device_runtime,
        resources=resources,
        simulation=simulation,
    )


def _create_data_collection_recorder(
    robot_state_reader: ArmTelemetryReader,
    camera_source: DepthCameraSource,
) -> DataCollectionRecorder:
    """Load optional data-collection infrastructure only when requested."""

    from ..data_collection.config import DataCollectionConfig
    from ..data_collection.recorder import DemonstrationRecorder

    return DemonstrationRecorder(
        robot_state_reader=robot_state_reader,
        camera_source=camera_source,
        config=DataCollectionConfig.from_environment(),
    )

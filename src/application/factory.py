from __future__ import annotations

from functools import partial

from ..core.data_paths import ApplicationDataPaths
from ..core.settings import ApplicationSettings, DataCollectionSettings
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
from ..core.execution_context import ExecutionContext
from ..vision.service import VisionService
from ..vision.simulation import VisionPipelineFixture
from .builtin_data import BuiltinDataInstaller
from .command_runtime import CommandRuntime
from .composition import CompositionService
from .data_collection import (
    DataCollectionRecorder,
    DataCollectionService,
)
from .localization import LocalizationService
from .safety import SafetyService
from .services import (
    ApplicationServices,
    DeviceManagementService,
    ExecutionService,
    ManualControlService,
    RobotQueryService,
    TrajectoryTeachingService,
)
from .teleoperation import TeleoperationService
from .task_composer import TaskComposerService


def create_application_services(
    settings: ApplicationSettings,
    *,
    simulation: bool,
) -> ApplicationServices:
    data_paths = ApplicationDataPaths.from_settings(settings.data)
    BuiltinDataInstaller(data_paths).install_missing()
    composition = CompositionService(
        JsonCompositionRepository(
            actions_file=data_paths.actions_file,
            tasks_directory=data_paths.tasks_directory,
        )
    )
    task_composer = TaskComposerService(composition)
    device_runtime = create_device_runtime(settings, simulation=simulation)
    resources = ResourceArbiter()
    localization = LocalizationService()
    execution_context = ExecutionContext()
    vision_fixture = VisionPipelineFixture() if simulation else None
    vision = VisionService(
        settings.vision,
        execution_context,
        capture_pipeline=vision_fixture.capture if vision_fixture else None,
        relocalization_pipeline=(
            vision_fixture.relocalize if vision_fixture else None
        ),
    )
    engine = ActionEngine(
        device_runtime,
        settings.execution,
        settings.devices,
        settings.vision,
        settings.secrets,
        localization.latest,
        execution_context,
        vision,
    )
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
        preview_ttl_s=settings.runtime.command_preview_ttl_seconds,
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
        wait_timeout_seconds=settings.execution.safety_stop_wait_timeout_seconds,
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
        recorder_factory=partial(
            _create_data_collection_recorder,
            settings=settings.data_collection,
        ),
    )
    safety.register_control_session(
        "data collection",
        data_collection.close,
    )
    return ApplicationServices(
        camera_access=camera_access,
        vision=vision,
        localization=localization,
        composition=composition,
        task_composer=task_composer,
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
        settings=settings,
    )


def _create_data_collection_recorder(
    robot_state_reader: ArmTelemetryReader,
    camera_source: DepthCameraSource,
    *,
    settings: DataCollectionSettings,
) -> DataCollectionRecorder:
    """Load optional data-collection infrastructure only when requested."""

    from ..data_collection.config import DataCollectionConfig
    from ..data_collection.recorder import DemonstrationRecorder

    return DemonstrationRecorder(
        robot_state_reader=robot_state_reader,
        camera_source=camera_source,
        config=DataCollectionConfig.from_settings(settings),
    )

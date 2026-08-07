from __future__ import annotations

from functools import partial

from ..configuration.data_paths import ApplicationDataPaths
from ..configuration.settings import ApplicationSettings, DataCollectionSettings
from ..persistence.storage import JsonCompositionRepository
from ..devices import (
    ArmTelemetryReader,
    DepthCameraSource,
    ResourceArbiter,
)
from ..devices.runtime.factory import create_device_runtime
from ..execution.engine import ActionEngine
from ..execution.manager import ExecutionManager
from ..llm import LLMRegistry
from ..localization import (
    NullExternalLocalizationProvider,
    UdpExternalLocalizationProvider,
)
from ..skill_system import SkillEngine
from .camera_access import CameraAccessService
from .balance import register_balance_reader
from ..domain.execution_context import ExecutionContext
from ..vision.service import VisionService
from ..vision.simulation import VisionPipelineFixture
from .builtin_data import BuiltinDataInstaller
from .command_runtime import CommandRuntime
from .command_catalog import CommandCatalog
from .composition import CompositionService
from .data_collection import (
    DataCollectionRecorder,
    DataCollectionService,
)
from .external_localization import ExternalLocalizationService
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
from .workflow_editing import WorkflowEditingSession
from .workflow_compiler import WorkflowCompiler
from .workflow_preflight import WorkflowPreflightService


def create_application_services(
    settings: ApplicationSettings,
    *,
    simulation: bool,
) -> ApplicationServices:
    data_paths = ApplicationDataPaths.from_settings(settings.data)
    BuiltinDataInstaller(data_paths).install_missing()
    composition = CompositionService(
        JsonCompositionRepository(
            actions_directory=data_paths.actions_directory,
            workflows_directory=data_paths.workflows_directory,
            workflow_drafts_directory=data_paths.workflow_drafts_directory,
        )
    )
    workflow_editing = WorkflowEditingSession(composition)
    device_runtime = create_device_runtime(settings, simulation=simulation)
    resources = ResourceArbiter()
    llm = LLMRegistry.from_settings(settings.llm, settings.secrets)
    camera_access = CameraAccessService(device_runtime, resources)
    register_balance_reader(
        device_runtime,
        camera_access,
        llm,
        settings.vision,
        simulation=simulation,
    )
    localization_provider = (
        NullExternalLocalizationProvider()
        if simulation
        else UdpExternalLocalizationProvider(settings.localization)
    )
    external_localization = ExternalLocalizationService(localization_provider)
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
        external_localization.latest,
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
    skill_engine.load_skills(str(data_paths.skills_directory))
    workflow_compiler = WorkflowCompiler()
    command_catalog = CommandCatalog(
        composition,
        skill_engine,
        settings.runtime,
    )
    commands = CommandRuntime(
        execution=execution,
        skill_engine=skill_engine,
        composition=composition,
        workflow_compiler=workflow_compiler,
        catalog=command_catalog,
        preview_ttl_s=settings.runtime.command_preview_ttl_seconds,
    )
    manual_control = ManualControlService(device_runtime, resources)
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
    workflow_preflight = WorkflowPreflightService(execution, devices)
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
        external_localization=external_localization,
        composition=composition,
        workflow_editing=workflow_editing,
        data_collection=data_collection,
        execution=execution,
        workflow_compiler=workflow_compiler,
        workflow_preflight=workflow_preflight,
        devices=devices,
        manual_control=manual_control,
        teleoperation=teleoperation,
        robot_query=robot_query,
        trajectory_teaching=trajectory_teaching,
        safety=safety,
        commands=commands,
        llm=llm,
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

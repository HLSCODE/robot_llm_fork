from __future__ import annotations

from typing import Any

from ..core.storage import JsonCompositionRepository
from ..device_runtime import ResourceArbiter
from ..device_runtime.factory import create_device_runtime
from ..execution.engine import ActionEngine
from ..execution.manager import ExecutionManager
from ..skill_system import SkillEngine
from .camera_access import CameraAccessService
from .command_runtime import CommandRuntime
from .composition import CompositionService
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
    composition = CompositionService(JsonCompositionRepository())
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
    skill_engine.load_skills()
    commands = CommandRuntime(
        execution=execution,
        skill_engine=skill_engine,
        preview_ttl_s=float(
            getattr(config, "COMMAND_PREVIEW_TTL_SECONDS", 120.0)
        ),
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
    return ApplicationServices(
        camera_access=camera_access,
        composition=composition,
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

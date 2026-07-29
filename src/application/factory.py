from __future__ import annotations

from typing import Any

from ..core.storage import JsonCompositionRepository
from ..device_runtime import ResourceArbiter
from ..device_runtime.factory import create_device_runtime
from ..execution.engine import ActionEngine
from ..execution.manager import ExecutionManager
from .camera_access import CameraAccessService
from .composition import CompositionService
from .services import (
    ApplicationServices,
    DeviceManagementService,
    ExecutionService,
    ManualControlService,
    RobotQueryService,
    TeleoperationService,
    TrajectoryTeachingService,
)
from .safety import SafetyService


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
        device_runtime=device_runtime,
        resources=resources,
        simulation=simulation,
    )

from __future__ import annotations

from typing import Any

from ..device_runtime import ResourceArbiter
from ..device_runtime.factory import create_device_runtime
from ..execution.engine import ActionEngine
from ..execution.manager import ExecutionManager
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
    device_runtime = create_device_runtime(config, simulation=simulation)
    resources = ResourceArbiter()
    engine = ActionEngine(device_runtime, config)
    manager = ExecutionManager(
        engine=engine,
        resource_arbiter=resources,
        execution_resources=device_runtime.registered_device_ids,
    )
    execution = ExecutionService(manager)
    manual_control = ManualControlService(device_runtime, resources)
    teleoperation = TeleoperationService(device_runtime, resources)
    robot_query = RobotQueryService(device_runtime)
    trajectory_teaching = TrajectoryTeachingService(
        device_runtime,
        resources,
    )
    devices = DeviceManagementService(
        device_runtime,
        execution,
        teleoperation,
        trajectory_teaching,
    )
    return ApplicationServices(
        execution=execution,
        devices=devices,
        manual_control=manual_control,
        teleoperation=teleoperation,
        robot_query=robot_query,
        trajectory_teaching=trajectory_teaching,
        device_runtime=device_runtime,
        resources=resources,
        simulation=simulation,
    )

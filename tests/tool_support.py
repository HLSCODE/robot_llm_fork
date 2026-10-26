"""Explicit tool deployment for simulated integration tests, never app defaults."""

from dataclasses import replace

from src.configuration.end_effectors import (
    EndEffectorConnection,
    EndEffectorMount,
    EndEffectorSettings,
    EndEffectorTool,
)
from src.configuration.settings import ApplicationSettings


def tool_settings() -> ApplicationSettings:
    return replace(
        ApplicationSettings.defaults(),
        end_effectors=EndEffectorSettings(
            connections=(EndEffectorConnection("bus", "COM90"),),
            tools=(
                EndEffectorTool("left", "gripper", "robot"),
                EndEffectorTool("right", "gripper", "zhixing", connection="bus", station=17),
                EndEffectorTool("pipette", "pipette", "adp", connection="bus"),
                EndEffectorTool("changer", "tool_changer", "kuaihuanshou", connection="bus"),
            ),
            mounts=(
                EndEffectorMount("left", "fixed", tool="left"),
                EndEffectorMount(
                    "right",
                    "exchangeable",
                    changer="changer",
                    tools=("right", "pipette"),
                ),
            ),
        ),
    )


def register_test_tools(runtime):
    from src.devices.end_effectors.factory import register_tools
    from src.devices.transports.shared_serial import SharedSerialPool
    from src.devices.runtime.runtime import DeviceRegistration
    from src.devices.runtime.fakes import SimulatedRobotSystem
    from src.devices.runtime.models import DeviceCapability

    if "robot-system" not in runtime.registered_device_ids():
        runtime.register(
            DeviceRegistration(
                "robot-system",
                frozenset({DeviceCapability.GRIPPER}),
                SimulatedRobotSystem,
                lambda device: device.close(),
            )
        )

    register_tools(runtime, tool_settings().end_effectors, SharedSerialPool(), simulation=True)
    return runtime

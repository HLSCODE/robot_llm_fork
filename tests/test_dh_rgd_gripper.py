from dataclasses import replace
from unittest.mock import patch

import pytest

from src.configuration.end_effectors import (
    EndEffectorTool, EndEffectorConnection, EndEffectorMount, EndEffectorSettings,
)
from src.configuration.errors import ConfigLoadError
from src.configuration.settings import ApplicationSettings
from src.devices.end_effectors import end_effectors
from src.devices.end_effectors.providers import RotatingGripper, InitializableGripper
from src.devices.runtime.factory import create_device_runtime
from src.devices.tools.dh_rgd.driver import RGDGripper
from src.devices.transports.protocols.crc import append_crc
from src.devices.transports.core.exceptions import CRCError, ProtocolError
from src.domain.action_schema import validate_action_parameters
from src.domain.models import ActionType
from src.domain.models import ActionDefinition, SequenceItem
from src.application import create_application_services
from src.execution.models import ExecutionState


def configuration(**overrides):
    return EndEffectorTool("rgd", "gripper", "dh_rgd", connection="bus", **overrides)


class Bus:
    def __init__(self):
        self.registers = {0x0200: 1, 0x020A: 1, 0x0201: 1, 0x020B: 1}
        self.writes = []
        self.closed = False
        self.corrupt = False
        self.wrong_address = False

    def transact_with_strategy(self, strategy):
        request = strategy.payload
        station, function = request[:2]
        register = int.from_bytes(request[2:4], "big")
        value = int.from_bytes(request[4:6], "big")
        if function == 6:
            self.writes.append((register, value))
            if register == 0x0103:
                self.registers[0x0202] = value
            if register == 0x0105:
                self.registers[0x0208] = value
            if register == 0x0109:
                self.registers[0x0208] = (self.registers.get(0x0208, 0) + value) & 65535
            response = request
        else:
            response = append_crc(bytes([station + int(self.wrong_address), 3, 2]) +
                                  self.registers.get(register, 0).to_bytes(2, "big"))
        return response[:-1] + bytes([response[-1] ^ 1]) if self.corrupt else response

    def close(self):
        self.closed = True


def test_no_startup_motion_and_no_shutdown_release_of_held_load():
    bus = Bus()
    gripper = RGDGripper(bus, configuration())
    assert bus.writes == []
    gripper.initialize()
    assert bus.writes == [(0x0100, 1)]
    gripper.grip()
    assert bus.writes[-1] == (0x0103, 1000)
    gripper.open()
    assert bus.writes[-1] == (0x0103, 0)
    gripper.move_closure(25)
    assert bus.writes[-1] == (0x0103, 250)
    before = bus.writes.copy()
    gripper.close()
    assert bus.closed and bus.writes == before


def test_configured_endpoints_and_signed_rotations():
    bus = Bus()
    gripper = RGDGripper(bus, configuration(
        open_position=1000, closed_position=0, rotation_units_per_degree=10,
    ))
    gripper.open()
    assert bus.writes[-1] == (0x0103, 1000)
    gripper.rotate(-18)
    assert bus.writes[-1] == (0x0105, (-180) & 65535)
    gripper.rotate(2, relative=True)
    assert bus.writes[-1] == (0x0109, 20)
    assert bus.registers[0x0208] == (-160) & 65535


@pytest.mark.parametrize("opened,closed", [(0, 1000), (1000, 0)])
def test_alternating_open_grip_and_position_use_same_mapping(opened, closed):
    bus = Bus()
    gripper = RGDGripper(bus, configuration(open_position=opened, closed_position=closed))
    for _ in range(3):
        gripper.open()
        assert bus.registers[0x0202] == opened
        gripper.grip()
        assert bus.registers[0x0202] == closed
        gripper.move_closure(25)
        assert bus.registers[0x0202] == round(opened + (closed - opened) * .25)


@pytest.mark.parametrize("values", [
    {"speed": 101}, {"torque": 19}, {"rotation_force": 101},
    {"rotation_speed": 0}, {"rotation_units_per_degree": float("nan")},
    {"open_position": 1001}, {"closed_position": 0},
])
def test_invalid_config(values):
    with pytest.raises(ConfigLoadError):
        configuration(**values)


@pytest.mark.parametrize("percent", [-1, 101, float("nan"), float("inf"), True])
def test_invalid_closure_has_no_side_effect(percent):
    bus = Bus()
    with pytest.raises(ValueError):
        RGDGripper(bus, configuration()).move_closure(percent)
    assert not bus.writes


def test_uninitialized_and_alarm_fail_before_movement():
    bus = Bus()
    bus.registers[0x0200] = 0
    gripper = RGDGripper(bus, configuration())
    with pytest.raises(ValueError, match="初始化"):
        gripper.grip()
    bus.registers[0x0200] = 1
    bus.registers[0x0205] = 8
    with pytest.raises(IOError, match="0008"):
        gripper.grip()
    assert not bus.writes


@pytest.mark.parametrize("state", [2, 3, 65535, 10])
def test_rotation_stall_and_invalid_status_stop_and_fail(state):
    bus = Bus()
    bus.registers[0x020B] = state
    with pytest.raises(IOError):
        RGDGripper(bus, configuration()).rotate(180)
    assert bus.writes[-1] == (0x0502, 1)
    assert bus.writes.count((0x0105, 180)) == 1


def test_contact_is_success_but_drop_and_blocked_open_are_failures():
    bus = Bus()
    gripper = RGDGripper(bus, configuration())
    bus.registers[0x0201] = 2
    gripper.grip()
    with pytest.raises(IOError, match="受阻"):
        gripper.open()
    bus.registers[0x0201] = 3
    with pytest.raises(IOError, match="掉落"):
        gripper.grip()


def test_rotation_timeout_and_cancellation_stop_without_replay():
    bus = Bus()
    bus.registers[0x020B] = 0
    gripper = RGDGripper(bus, configuration())
    with patch("src.devices.tools.dh_rgd.driver.time.monotonic", side_effect=[0, 0, 61]), patch(
        "src.devices.tools.dh_rgd.driver.time.sleep"
    ), pytest.raises(TimeoutError):
        gripper.rotate(90)
    assert bus.writes[-1] == (0x0502, 1)

    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        gripper.rotate(90, checkpoint=cancelled)
    assert bus.writes[-1] == (0x0502, 1)


def test_crc_station_and_angle_validation():
    bus = Bus()
    gripper = RGDGripper(bus, configuration())
    bus.corrupt = True
    with pytest.raises(CRCError):
        gripper.grip()
    bus.corrupt = False
    bus.wrong_address = True
    with pytest.raises(ProtocolError):
        gripper.grip()
    for angle in (32768, -32769, float("nan"), True):
        with pytest.raises(ValueError):
            gripper.rotate(angle)
    assert not bus.writes


def test_simulated_provider_and_action_schema():
    settings = replace(ApplicationSettings.defaults(), end_effectors=EndEffectorSettings(
        connections=(EndEffectorConnection("bus", "COM90"),),
        tools=(configuration(),),
        mounts=(EndEffectorMount("left", "fixed", tool="rgd"),),
    ))
    runtime = create_device_runtime(settings, simulation=True)
    try:
        routing = end_effectors(runtime)
        gripper = routing.gripper(routing.resolve("gripper", arm="left"))
        assert isinstance(gripper, RotatingGripper)
        assert isinstance(gripper, InitializableGripper)
        gripper.initialize()
        gripper.rotate(180)
        gripper.rotate(-90, relative=True)
        assert gripper.angle_degrees == 90
        for operation in ("初始化", "绝对旋转", "相对旋转", "停止旋转"):
            assert validate_action_parameters(ActionType.MANIPULATE, {
                "执行器": "夹爪", "操作": operation, "arm": "left", "tool_id": "rgd",
                "旋转角度": 90,
            }).is_valid
    finally:
        runtime.shutdown_all()


@pytest.mark.parametrize("provider", ["dh_rgd", "zhixing"])
def test_execution_routes_rotation_only_to_capable_provider(provider):
    tool = replace(configuration(), provider=provider)
    settings = replace(ApplicationSettings.defaults(), end_effectors=EndEffectorSettings(
        connections=(EndEffectorConnection("bus", "COM90"),), tools=(tool,),
        mounts=(EndEffectorMount("left", "fixed", tool="rgd"),),
    ))
    services = create_application_services(settings, simulation=True)
    try:
        commands = [SequenceItem(str(i), ActionDefinition(str(i), str(i), ActionType.MANIPULATE, {
            "执行器": "夹爪", "操作": operation, "arm": "left", "tool_id": "rgd",
            "旋转角度": angle,
        })) for i, (operation, angle) in enumerate([
            ("初始化", 0), ("绝对旋转", 180), ("相对旋转", -90), ("停止旋转", 0),
        ])]
        resources = services.execution.required_resources_for_entries(commands)
        assert "connection:bus" in resources
        final = services.execution.start_entries(commands, origin="test").wait(3)
        if provider == "dh_rgd":
            assert final.state is ExecutionState.SUCCEEDED
            routing = services.manual_control.end_effectors.routing
            assert routing.device(routing.resolve("gripper", arm="left")).angle_degrees == 90
        else:
            assert final.state is ExecutionState.FAILED
    finally:
        services.devices.shutdown_all()

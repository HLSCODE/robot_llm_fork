from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from src.configuration.end_effectors import (
    EndEffectorSettings,
    EndEffectorConnection,
    EndEffectorTool,
    EndEffectorMount,
)
from src.configuration.settings import ApplicationSettings, CameraProfile
from src.configuration.config_loader import load_application_settings
from src.configuration.toml_source import SCHEMA_VERSION
from src.devices.runtime.factory import create_device_runtime
from src.devices.end_effectors import end_effectors
from src.devices.transports.shared_serial import SharedSerialPool
from src.devices.transports import SerialSettings
from src.devices.runtime.arm_models import ArmId
from src.application import create_application_services
from src.domain.models import (
    ActionDefinition,
    ActionType,
    SequenceItem,
    ParallelBlock,
    ParallelBranch,
)
from src.execution.models import ExecutionState, ParallelResourceConflictError


def test_status_query_does_not_claim_tool_identity_or_force_tool_initialization():
    services = create_application_services(
        replace(ApplicationSettings.defaults(), end_effectors=configuration()), simulation=True
    )
    try:
        service = services.manual_control.end_effectors
        routing = service.routing
        pipette = routing.device(routing.resolve("pipette", arm="right", tool_id="pipette"))
        changer = routing.device(routing.resolve("tool_changer", arm="right"))
        with patch.object(pipette, "initialize", side_effect=AssertionError("unexpected reset")):
            states = service.read_changer_states()
        assert states[0]["locked"] == "unlocked"
        assert "installed_tool" not in states[0]
        with patch.object(changer, "read_status", side_effect=TimeoutError("status timeout")):
            failed = service.read_changer_states()[0]
        assert failed["locked"] == "unknown" and failed["sampled_at"] is None
    finally:
        services.devices.shutdown_all()


def configuration():
    return EndEffectorSettings(
        connections=(EndEffectorConnection("bus", "COM90"),),
        tools=(
            EndEffectorTool("left", "gripper", "robot"),
            EndEffectorTool("changer", "tool_changer", "kuaihuanshou", connection="bus"),
            EndEffectorTool("pipette", "pipette", "adp", connection="bus"),
            EndEffectorTool("right", "gripper", "zhixing", connection="bus", station=17),
        ),
        mounts=(
            EndEffectorMount("left", "fixed", tool="left"),
            EndEffectorMount(
                "right",
                "exchangeable",
                changer="changer",
                tools=("pipette", "right"),
            ),
        ),
    )


def action(identity, executor, operation, tool, **extra):
    return SequenceItem(
        identity,
        ActionDefinition(
            identity,
            identity,
            ActionType.MANIPULATE,
            {
                "执行器": executor,
                "操作": operation,
                "arm": "right",
                "tool_id": tool,
                **extra,
            },
        ),
    )


def test_fixed_and_exchangeable_routes_are_explicit_and_do_not_open_ports():
    settings = replace(ApplicationSettings.defaults(), end_effectors=configuration())
    settings = replace(
        settings,
        vision=replace(
            settings.vision, cameras=(CameraProfile(name="test", provider="opencv", device_id="0"),)
        ),
    )
    with patch(
        "src.devices.transports.core.transport._load_serial_factory",
        side_effect=AssertionError("opened port"),
    ):
        runtime = create_device_runtime(settings, simulation=False)
        routing = end_effectors(runtime)
        assert routing.resolve("gripper", arm="left").id == "left"
        with pytest.raises(ValueError, match="明确选择工具"):
            routing.resolve("gripper", arm="right")
        target = routing.resolve("gripper", arm="right", tool_id="right")
        assert target.settings.station == 17
        assert "connection:bus" in target.resources
        assert "end-effector:right" in target.resources
        with pytest.raises(ValueError):
            routing.resolve("pipette", arm="left", tool_id="pipette")
        runtime.shutdown_all()


def test_configuration_rejects_duplicate_ports_and_wrong_sdk_provider():
    with pytest.raises(ValueError, match="串口只能声明一次"):
        replace(
            configuration(),
            connections=(EndEffectorConnection("a", "COM90"), EndEffectorConnection("b", "COM90")),
        )
    settings = replace(ApplicationSettings.defaults(), end_effectors=configuration())
    tianji = replace(settings, robot=replace(settings.robot, provider="tianji"))
    runtime = create_device_runtime(tianji, simulation=True)
    try:
        assert runtime.snapshot("tool:left").state.value == "failed"
        assert "内置夹爪" in end_effectors(runtime).unavailable_reason("left")
        with pytest.raises(Exception, match="内置夹爪"):
            end_effectors(runtime).resolve("gripper", arm="left")
        assert end_effectors(runtime).choices("gripper", "left") == []
    finally:
        runtime.shutdown_all()


def test_new_fragment_is_loaded_without_changing_default_include(tmp_path):
    import json
    import shutil

    source = Path("config/fragments")
    names = [
        "end-effectors.toml",
        *(
            f"end-effectors/{p.stem.removesuffix('.example')}.toml"
            for p in (source / "end-effectors").glob("*.example.toml")
        ),
    ]
    for name in names:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name.replace(".toml", ".example.toml"), dest)
    path = tmp_path / "config.toml"
    path.write_text(
        f"schema_version = {SCHEMA_VERSION}\ninclude = " + json.dumps(names), encoding="utf-8"
    )
    settings = load_application_settings(path)
    assert {t.provider for t in settings.end_effectors.tools} == {
        "robot",
        "zhixing",
        "dh_rgd",
        "adp",
        "kuaihuanshou",
    }
    assert all(m.mode == "none" for m in settings.end_effectors.mounts)


def test_shared_port_is_opened_once_and_borrower_close_does_not_close_it():
    opened = []

    class FakeHandle:
        closed = False

        def transact_with_strategy(self, strategy):
            return b"ok"

        def close(self):
            self.closed = True

    def factory(settings):
        handle = FakeHandle()
        opened.append(handle)
        return handle

    pool = SharedSerialPool(factory)
    changer = pool.borrow(SerialSettings("COM90", timeout_seconds=1))
    pipette = pool.borrow(SerialSettings("COM90", timeout_seconds=5))
    assert not opened
    assert changer.transact(b"query", 2) == b"ok"
    changer.close()
    assert pipette.transact(b"aspirate", 2) == b"ok"
    assert len(opened) == 1 and not opened[0].closed
    with pytest.raises(ValueError, match="线路参数冲突"):
        pool.borrow(SerialSettings("COM90", baudrate=9600))
    pool.close()
    assert opened[0].closed
    with pytest.raises(RuntimeError, match="连接池已关闭"):
        pipette.transact(b"late", 2)


def test_explicit_tool_actions_and_shared_resource_conflicts():
    services = create_application_services(
        replace(ApplicationSettings.defaults(), end_effectors=configuration()), simulation=True
    )
    try:
        gripper = action("grip", "夹爪", "定位", "right", 闭合度=30)
        liquid = action("liquid", "吸液枪", "吸", "pipette", 容量=10)
        resources = services.execution.required_resources_for_entries([gripper])
        assert "connection:bus" in resources
        final = services.execution.start_entries([gripper, liquid], origin="test").wait(3)
        assert final.state is ExecutionState.SUCCEEDED
        routing = services.manual_control.end_effectors.routing
        device = routing.device(routing.resolve("gripper", arm="right", tool_id="right"))
        assert device.closure_percent == 30
        with pytest.raises(ParallelResourceConflictError):
            services.execution.required_resources_for_entries(
                [
                    ParallelBlock(
                        "parallel",
                        [
                            ParallelBranch("a", [gripper]),
                            ParallelBranch("b", [liquid]),
                        ],
                    )
                ]
            )
    finally:
        services.devices.shutdown_all()


def test_non_idempotent_tool_timeout_is_not_retried_and_stops_next_action():
    services = create_application_services(
        replace(ApplicationSettings.defaults(), end_effectors=configuration()), simulation=True
    )
    try:
        routing = services.manual_control.end_effectors.routing
        device = routing.device(routing.resolve("pipette", arm="right", tool_id="pipette"))
        commands = [
            action("liquid", "吸液枪", "吸", "pipette", 容量=10),
            action("next", "夹爪", "关", "right"),
        ]
        with patch.object(device, "absorb", side_effect=TimeoutError("reply lost")) as absorb:
            final = services.execution.start_entries(commands, origin="test").wait(3)
        assert final.state is ExecutionState.FAILED
        assert absorb.call_count == 1
        gripper = routing.device(routing.resolve("gripper", arm="right", tool_id="right"))
        assert gripper.closure_percent == 0
    finally:
        services.devices.shutdown_all()


def test_realman_position_uses_original_sdk_value():
    settings = replace(ApplicationSettings.defaults(), end_effectors=configuration())
    runtime = create_device_runtime(settings, simulation=True)
    routing = end_effectors(runtime)
    target = routing.resolve("gripper", arm=ArmId.LEFT)
    robot = runtime.require("robot-system")
    with patch.object(robot, "move_gripper") as move:
        routing.move_position(target, 375)
    move.assert_called_once_with(ArmId.LEFT, 375)
    runtime.shutdown_all()


def test_vision_composition_routes_gripper_without_tianji_sdk_gripper_methods():
    from src.devices.end_effectors.vision_facade import VisionRobotFacade

    settings = replace(ApplicationSettings.defaults(), end_effectors=configuration())
    runtime = create_device_runtime(settings, simulation=True)
    try:
        routing = end_effectors(runtime)
        target = routing.resolve("gripper", arm="right", tool_id="right")
        robot = VisionRobotFacade(routing, target)
        gripper = routing.device(target)
        robot.close_gripper(ArmId.RIGHT)
        assert gripper.closure_percent == 100
        with pytest.raises(ValueError, match="不一致"):
            robot.open_gripper(ArmId.LEFT)
    finally:
        runtime.shutdown_all()


def test_shared_connection_serializes_whole_exchanges_and_can_reconnect():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()
    handles = []

    class Handle:
        closed = False

        def transact_with_strategy(self, strategy):
            if strategy.payload == b"first":
                entered.set()
                assert release.wait(2)
            return strategy.payload

        def close(self):
            self.closed = True

    def factory(_settings):
        handle = Handle()
        handles.append(handle)
        return handle

    pool = SharedSerialPool(factory)
    first, second = (pool.borrow(SerialSettings("COM92")) for _ in range(2))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(first.transact, b"first", 5)
            assert entered.wait(2)
            b = executor.submit(second.transact, b"second", 6)
            assert not b.done()
            release.set()
            assert a.result(2) == b"first" and b.result(2) == b"second"
        pool.reconnect("COM92")
        assert handles[0].closed
        assert second.transact(b"recovered", 9) == b"recovered"
        assert len(handles) == 2
    finally:
        release.set()
        pool.close()


def test_changer_rejects_corrupt_crc_and_wrong_protocol_response():
    from src.devices.tools.tool_changer.driver import Kuaihuanshou, _append_crc
    from src.devices.transports.testing import FakeTransport
    from src.devices.transports import ProtocolError

    for response in (b"\x53\x26\x02\x01\x01\x00\x00", _append_crc(b"\x53\x26\x05\x01\x01")):
        with pytest.raises(ProtocolError):
            Kuaihuanshou(FakeTransport((response,))).send_command("status")


def test_same_address_alternatives_are_allowed_only_on_one_exchangeable_mount():
    settings = configuration()
    other = replace(settings.tools[-1], id="other")
    mounts = (
        settings.mounts[0],
        replace(settings.mounts[1], tools=(*settings.mounts[1].tools, "other")),
    )
    configured = replace(settings, tools=(*settings.tools, other), mounts=mounts)
    assert configured.configured
    with pytest.raises(ValueError, match="多个机械臂"):
        replace(configured, mounts=(replace(mounts[0], tool="other"), mounts[1]))
    with pytest.raises(ValueError, match="互斥备选工具"):
        replace(
            configured,
            mounts=(
                EndEffectorMount("left", "fixed", tool="right"),
                EndEffectorMount("right", "fixed", tool="other"),
            ),
        )


def test_explicit_initialization_recovers_once_and_no_old_alias_is_registered():
    runtime = create_device_runtime(
        replace(ApplicationSettings.defaults(), end_effectors=configuration()), simulation=True
    )
    try:
        routing = end_effectors(runtime)
        target = routing.resolve("pipette", arm="right", tool_id="pipette")
        device = routing.device(target)
        pool = runtime.require("end-effector-connections")
        assert "pipette" not in runtime.registered_device_ids()
        assert "zhixing-grippers" not in runtime.registered_device_ids()
        with (
            patch.object(pool, "reconnect") as reconnect,
            patch.object(device, "initialize", return_value=True) as initialize,
        ):
            assert routing.initialize_pipette(target)
        reconnect.assert_called_once_with("COM90")
        initialize.assert_called_once_with()
        with pytest.raises(Exception, match="zhixing-grippers"):
            runtime.require("zhixing-grippers")
    finally:
        runtime.shutdown_all()


def test_teleoperation_uses_selected_tool_and_deduplicates_per_instance():
    from src.application import TeleoperationService
    from src.devices import ResourceArbiter

    original = configuration()
    extra = replace(original.tools[-1], id="second_gripper")
    configured = replace(
        original,
        tools=(*original.tools, extra),
        mounts=(
            original.mounts[0],
            replace(original.mounts[1], tools=("right", "second_gripper", "pipette")),
        ),
    )
    runtime = create_device_runtime(
        replace(ApplicationSettings.defaults(), end_effectors=configured), simulation=True
    )
    arbiter = ResourceArbiter()
    service = TeleoperationService(runtime, arbiter)
    try:
        service.start("test", ("right",))
        assert arbiter.owner_of("connection:bus") == "teleoperation"
        assert service.set_gripper("test", "right", 300, tool_id="right").applied
        assert not service.set_gripper("test", "right", 300, tool_id="right").applied
        assert service.set_gripper("test", "right", 300, tool_id="second_gripper").applied
        routing = end_effectors(runtime)
        for identity in ("right", "second_gripper"):
            device = routing.device(routing.resolve("gripper", arm="right", tool_id=identity))
            assert device.closure_percent == 70
    finally:
        service.stop_all()
        runtime.shutdown_all()


def test_physical_transport_timeout_is_restored_and_late_reply_window_is_respected():
    from src.devices.transports import SerialTransport, TransportError

    class Port:
        is_open = True
        timeout = 0.01
        write_timeout = 0.01

        def __init__(self):
            self.replies = iter((b"o", b"ok"))
            self.write_timeouts = []
            self.flushes = 0

        def write(self, payload):
            self.write_timeouts.append(self.timeout)
            return len(payload)

        def flush(self):
            pass

        def reset_input_buffer(self):
            self.flushes += 1

        def reset_output_buffer(self):
            pass

        def read(self, size):
            return next(self.replies)

        def close(self):
            self.is_open = False

    port = Port()
    pool = SharedSerialPool(
        lambda options: SerialTransport(options, serial_factory=lambda **_: port)
    )
    first = pool.borrow(SerialSettings("COM93", timeout_seconds=0.01))
    second = pool.borrow(SerialSettings("COM93", timeout_seconds=0.02))
    try:
        with patch("src.devices.transports.shared_serial.time.sleep") as pause:
            with pytest.raises(TransportError):
                first.transact(b"first", 2)
            assert second.transact(b"second", 2) == b"ok"
        assert any(call.args[0] > 0.005 for call in pause.call_args_list)
        assert port.write_timeouts == [0.01, 0.02]
        assert port.timeout == port.write_timeout == 0.01
        assert port.flushes == 2
    finally:
        pool.close()


def test_websocket_gripper_passes_explicit_tool_id():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.robot_server.controllers.teleoperation import TeleoperationWebSocketHandler

    gripper = Mock()
    server = SimpleNamespace(
        _services=SimpleNamespace(
            devices=SimpleNamespace(is_ready=lambda _: True),
            teleoperation=SimpleNamespace(set_gripper=gripper),
        )
    )
    handler = TeleoperationWebSocketHandler(server)
    asyncio.run(handler._execute_grip_async("owner", "右", 500, "zhixing_gripper_1"))
    gripper.assert_called_once_with("owner", "右", 500, tool_id="zhixing_gripper_1")
    assert handler._grip_tool_id({"grip_tool_id": {"右": "zhixing_gripper_1"}}, "右") == "zhixing_gripper_1"


@pytest.mark.parametrize(
    "field", ["adp_serial_port", "kuaihuanshou_timeout", "zhixing_gripper_speed"]
)
def test_removed_device_configuration_is_rejected(field, tmp_path):
    from src.configuration.errors import ConfigLoadError

    path = tmp_path / "old.toml"
    value = '"COM90"' if field.endswith("port") else "3"
    path.write_text(
        f"schema_version = {SCHEMA_VERSION}\n[devices]\n{field} = {value}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigLoadError, match=field):
        load_application_settings(path)


def test_empty_deployment_and_old_action_fields_never_fall_back_to_robot():
    from src.domain.action_schema import validate_action_parameters

    runtime = create_device_runtime(ApplicationSettings.defaults(), simulation=True)
    try:
        assert not any(
            identity in runtime.registered_device_ids()
            for identity in ("pipette", "tool-changer", "zhixing-grippers")
        )
        with pytest.raises(ValueError, match="end_effectors"):
            end_effectors(runtime).resolve("gripper", arm="left")
        assert runtime.get_if_ready("robot-system") is None
        for params in (
            {"执行器": "夹爪", "编号": 1, "操作": "开"},
            {"执行器": "吸液枪", "操作": "吸", "容量": 10},
            {"执行器": "知行夹爪", "编号": 1, "开合度": 50},
        ):
            assert not validate_action_parameters(ActionType.MANIPULATE, params).is_valid
    finally:
        runtime.shutdown_all()

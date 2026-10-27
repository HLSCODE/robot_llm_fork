from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from tool_support import tool_settings
from src.execution.end_effector_policy import resolve_tool_policy

from src.application.services import ManualControlService
from src.configuration.settings import ApplicationSettings
from src.devices import ResourceArbiter, ResourceBusyError
from src.devices.runtime.factory import create_device_runtime
from src.devices.runtime.ids import ZHIXING_GRIPPERS
from src.devices.tools.zhixing_gripper import ZhixingGripperController
from src.devices.transports import CRCError, ProtocolError, append_crc
from src.devices.transports.testing import FakeTransport
from src.domain.action_schema import validate_action_parameters
from src.domain.models import ActionType
from src.execution import ActionExecutionContext, ActionResultCode, ExecutionControl
from src.execution.action_control import resolve_manipulate_control_policy
from src.execution.handlers.manipulation import (
    create_manipulation_handler,
)


def read_response(station: int, *values: int) -> bytes:
    return append_crc(
        bytes([station, 3, len(values) * 2])
        + b"".join(value.to_bytes(2, "big") for value in values)
    )


def motion_responses(station: int, *, arrived: bool = True) -> tuple[bytes, ...]:
    return (
        read_response(station, 0),
        append_crc(bytes([station, 6, 1, 0, 0, 1])),
        append_crc(bytes([station, 16, 1, 2, 0, 2])),
        *(
            append_crc(bytes([station, 6, 1, register]) + value.to_bytes(2, "big"))
            for register, value in ((4, 100), (5, 60), (6, 2000), (7, 2000), (8, 1))
        ),
        read_response(station, 0),
        read_response(station, 0, int(arrived)),
    )


@pytest.mark.parametrize("station,percent,target", [(1, 0, -100), (2, 100, 8000), (2, 25, 3500)])
def test_each_station_uses_its_signed_mapping_and_protocol(station, percent, target):
    transport = FakeTransport(
        (
            read_response(1, 0),
            read_response(1, 0xFFFF, 0xFF9C),
            read_response(1, 0, 3900),
            read_response(2, 0),
            read_response(2, 0, 2000),
            read_response(2, 0, 8000),
            *motion_responses(station),
        )
    )
    device = ZhixingGripperController(transport)
    device.initialize()
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.move_percent(station, percent)
    frames = [call.payload for call in transport.calls[6:]]
    assert all(frame[0] == station for frame in frames)
    assert frames[2] == append_crc(
        bytes([station, 16, 1, 2, 0, 2, 4]) + (target & 0xFFFFFFFF).to_bytes(4, "big")
    )
    assert frames[7] == append_crc(bytes([station, 6, 1, 8, 0, 1]))


def test_invalid_mapping_uses_independent_configured_fallbacks():
    transport = FakeTransport(
        (
            read_response(1, 0),
            read_response(1, 0, 0),
            read_response(1, 0, 0),
            read_response(2, 0),
            IOError("mapping unavailable"),
            *motion_responses(2),
        )
    )
    device = ZhixingGripperController(transport, left_range=(0, 1000), right_range=(10, 2010))
    device.initialize()
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.move_percent(2, 50)
    assert transport.calls[7].payload[7:11] == (1010).to_bytes(4, "big")


@pytest.mark.parametrize(
    "station,percent",
    [
        (0, 50),
        (3, 50),
        (True, 50),
        (1, -1),
        (2, 101),
        (1, float("nan")),
        (2, float("inf")),
        (1, True),
    ],
)
def test_invalid_commands_never_touch_bus(station, percent):
    transport = FakeTransport()
    with pytest.raises(ValueError):
        ZhixingGripperController(transport).move_percent(station, percent)
    assert not transport.calls


def test_alarm_aborts_without_sending_motion():
    transport = FakeTransport((read_response(1, 2),))
    with pytest.raises(IOError, match="报警"):
        ZhixingGripperController(transport).move_percent(1, 100)
    assert len(transport.calls) == 1


def test_torque_arrival_completes_motion():
    responses = list(motion_responses(2))
    responses[-1] = read_response(2, 1, 0)
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        ZhixingGripperController(FakeTransport(tuple(responses))).move_percent(2, 100)


@pytest.mark.parametrize("percent,target", [(0, 4000), (25, 3000), (100, 0)])
def test_reverse_direction_preserves_closure_semantics(percent, target):
    transport = FakeTransport(motion_responses(1))
    device = ZhixingGripperController(transport, reverse_direction=True)
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.move_percent(1, percent)
    assert transport.calls[2].payload[7:11] == target.to_bytes(4, "big")


def test_provider_loads_device_stroke_once_on_first_motion():
    from src.configuration.end_effectors import EndEffectorTool
    from src.devices.end_effectors.providers import ZhixingGripper

    transport = FakeTransport((
        read_response(1, 0), read_response(1, 0, 0), read_response(1, 0, 6500),
        *motion_responses(1), *motion_responses(1),
    ))
    device = ZhixingGripper(transport, EndEffectorTool(
        "test", "gripper", "zhixing", connection="bus",
    ))
    assert not transport.calls
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.grip()
        device.open()
    assert transport.calls[5].payload[7:11] == (6500).to_bytes(4, "big")
    assert transport.calls[15].payload[7:11] == bytes(4)
    assert len(transport.calls) == 23


def test_provider_manual_range_skips_device_mapping():
    from src.configuration.end_effectors import EndEffectorTool
    from src.devices.end_effectors.providers import ZhixingGripper

    transport = FakeTransport(motion_responses(1))
    device = ZhixingGripper(transport, EndEffectorTool(
        "test", "gripper", "zhixing", connection="bus",
        read_device_range=False, position_max=7200, reverse_direction=True,
    ))
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.open()
    assert transport.calls[2].payload[7:11] == (7200).to_bytes(4, "big")


def test_provider_invalid_percent_does_not_read_mapping():
    from src.configuration.end_effectors import EndEffectorTool
    from src.devices.end_effectors.providers import ZhixingGripper

    transport = FakeTransport()
    device = ZhixingGripper(transport, EndEffectorTool(
        "test", "gripper", "zhixing", connection="bus",
    ))
    with pytest.raises(ValueError):
        device.move_closure(float("nan"))
    assert not transport.calls


def test_retry_reconfirms_enable_after_lost_reply():
    transport = FakeTransport(
        (
            read_response(1, 0),
            IOError("enable reply lost"),
            *motion_responses(1),
        )
    )
    device = ZhixingGripperController(transport)
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        with pytest.raises(IOError, match="enable reply lost"):
            device.move_percent(1, 100)
        device.move_percent(1, 100)
    assert transport.calls[1].payload == transport.calls[3].payload


def test_timeout_is_reported():
    transport = FakeTransport(motion_responses(1, arrived=False))
    with (
        patch("src.devices.tools.zhixing_gripper.driver.time.sleep"),
        patch(
            "src.devices.tools.zhixing_gripper.driver.time.monotonic",
            side_effect=[0, 0, 9],
        ),
        pytest.raises(TimeoutError),
    ):
        ZhixingGripperController(transport).move_percent(1, 50)


@pytest.mark.parametrize(
    "response,error",
    [
        (read_response(2, 0), ProtocolError),
        (read_response(1, 0)[:-1] + b"\x00", CRCError),
    ],
)
def test_wrong_station_and_bad_crc_are_rejected(response, error):
    with pytest.raises(error):
        ZhixingGripperController(FakeTransport((response,))).move_percent(1, 0)


def test_shutdown_attempts_both_disables_and_closes_even_if_one_fails():
    transport = FakeTransport(
        (
            *motion_responses(1),
            *motion_responses(2),
            IOError("left disconnected"),
            append_crc(bytes([2, 6, 1, 0, 0, 0])),
        )
    )
    device = ZhixingGripperController(transport)
    with patch("src.devices.tools.zhixing_gripper.driver.time.sleep"):
        device.move_percent(1, 0)
        device.move_percent(2, 100)
    device.close()
    assert transport.closed
    assert [call.payload[0] for call in transport.calls[-2:]] == [1, 2]


def test_shared_bus_serializes_entire_motions():
    entered, release = Event(), Event()
    transport = FakeTransport((*motion_responses(1), *motion_responses(2)))
    original_exchange = transport.transact_with_strategy

    def exchange(strategy):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        return original_exchange(strategy)

    with (
        patch.object(transport, "transact_with_strategy", side_effect=exchange),
        patch(
            "src.devices.tools.zhixing_gripper.driver.time.sleep",
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        device = ZhixingGripperController(transport)
        first = pool.submit(device.move_percent, 1, 0)
        assert entered.wait(2)
        second = pool.submit(device.move_percent, 2, 100)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert [call.payload[0] for call in transport.calls] == [1] * 10 + [2] * 10


def test_action_routing_and_manual_controls_share_bus_resource():
    runtime = create_device_runtime(tool_settings(), simulation=True)
    resources = ResourceArbiter()
    manual = ManualControlService(runtime, resources)
    handler = create_manipulation_handler(runtime, lambda: {})
    parameters = {"执行器": "夹爪", "arm": "right", "tool_id": "right", "操作": "定位", "闭合度": 37.5}
    assert validate_action_parameters(ActionType.MANIPULATE, parameters).is_valid
    policy = resolve_tool_policy(runtime, ActionType.MANIPULATE, parameters, resolve_manipulate_control_policy(parameters))
    assert "connection:bus" in policy.device_ids
    context = ActionExecutionContext(
        action_name="右知行夹爪",
        control=ExecutionControl(),
        timeout_seconds=10,
        log=lambda *_args: None,
    )
    result = handler(parameters, context)
    assert result.successful
    device = runtime.require("tool:right")
    assert device.closure_percent == 37.5
    manual.set_gripper("right", tool_id="right", closure_percent=100)
    assert device.closure_percent == 100
    with resources.acquire("workflow", policy.device_ids), pytest.raises(ResourceBusyError):
        manual.set_gripper("right", tool_id="right", closure_percent=0)
    runtime.shutdown_all()


@pytest.mark.parametrize(
    "extra",
    [
        {"开合度": -1},
        {"开合度": 101},
        {"开合度": float("nan")},
        {"编号": 3},
        {"编号": 1.5},
        {"开合度": True},
    ],
)
def test_action_handler_rejects_invalid_values_before_initialization(extra):
    runtime = create_device_runtime(ApplicationSettings.defaults(), simulation=True)
    handler = create_manipulation_handler(runtime, lambda: {})
    result = handler(
        {"执行器": "知行夹爪", "编号": 1, "开合度": 50, **extra},
        ActionExecutionContext(
            action_name="invalid",
            control=ExecutionControl(),
            timeout_seconds=10,
            log=lambda *_args: None,
        ),
    )
    assert result.code is ActionResultCode.UNSUPPORTED_OPERATION
    assert ZHIXING_GRIPPERS not in runtime.registered_device_ids()

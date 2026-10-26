from __future__ import annotations

from dataclasses import replace
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from tool_support import tool_settings

from src.application.services import DeviceManagementService, ManualControlService
from src.application.safety import SafetyService
from src.configuration.settings import ApplicationSettings, CameraProfile
from src.devices import DeviceOperationError, ResourceArbiter
from src.devices.runtime.factory import create_device_runtime
from src.devices.runtime.models import DeviceState
from src.devices.tools.pipette.driver import ADP
from src.devices.transports import TransportError, TransportErrorCategory
from src.devices.transports.testing import FakeTransport
from src.domain.execution_context import ExecutionContext
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.execution.engine import ActionEngine
from src.execution.manager import ExecutionManager
from src.execution.models import ExecutionState

PIPETTE = "tool:pipette"


def _runtime():
    settings = tool_settings()
    settings = replace(
        settings,
        vision=replace(
            settings.vision,
            cameras=(CameraProfile(name="test", provider="opencv", device_id="0"),),
        ),
    )
    return create_device_runtime(settings, simulation=False)


def test_runtime_registers_protocol_without_opening_or_resetting_tool():
    with patch(
        "src.devices.runtime.factory.SerialTransport", side_effect=AssertionError("unexpected I/O")
    ):
        runtime = _runtime()
        assert runtime.require(PIPETTE) is runtime.initialize(PIPETTE)
        assert "pipette" not in runtime.registered_device_ids()
        runtime.shutdown_all()


def test_explicit_initialization_and_reconnect_each_send_one_handshake():
    runtime = _runtime()
    manual = ManualControlService(runtime, ResourceArbiter())
    first = FakeTransport((b">01G6158\r\n",))
    second = FakeTransport((b">01G6158\r\n",))
    try:
        with patch("src.devices.runtime.factory.SerialTransport", side_effect=(first, second)):
            assert manual.initialize_pipette(arm="right", tool_id="pipette")
            assert manual.initialize_pipette(arm="right", tool_id="pipette")
        assert first.closed
        assert len(first.calls) == len(second.calls) == 1
    finally:
        runtime.shutdown_all()
    assert second.closed


def test_initialization_failure_does_not_prevent_explicit_recovery():
    runtime = _runtime()
    resources = ResourceArbiter()
    manual = ManualControlService(runtime, resources)
    failed = FakeTransport((TransportError("no reply", category=TransportErrorCategory.TIMEOUT),))
    recovered = FakeTransport((b">01G6158\r\n",))
    try:
        with patch("src.devices.runtime.factory.SerialTransport", side_effect=(failed, recovered)):
            with pytest.raises(DeviceOperationError):
                manual.initialize_pipette(arm="right", tool_id="pipette")
            assert manual.initialize_pipette(arm="right", tool_id="pipette")
        assert failed.closed
        assert resources.owner_of("connection:bus") is None
        assert len(recovered.calls) == 1
    finally:
        runtime.shutdown_all()


def test_reply_02_fails_sequence_without_retry_or_starting_next_action():
    runtime = _runtime()
    settings = ApplicationSettings.defaults()
    engine = ActionEngine(
        runtime,
        settings.execution,
        settings.devices,
        settings.vision,
        lambda: None,
        ExecutionContext(),
        MagicMock(),
    )
    manager = ExecutionManager(engine, ResourceArbiter(), engine.required_resources)
    plan = ExecutionPlan.from_entries(
        (
            SequenceItem.from_definition(
                ActionDefinition(
                    id="tu-200",
                    name="tu-200",
                    type=ActionType.MANIPULATE,
                    parameters={
                        "执行器": "吸液枪",
                        "arm": "right",
                        "tool_id": "pipette",
                        "操作": "吐",
                        "容量": 200,
                        "吐液速度": 800,
                        "吐液容量模式": "指定容量",
                        "全吐": False,
                    },
                )
            ),
            SequenceItem.from_definition(
                ActionDefinition(
                    id="next-absorb",
                    name="next-absorb",
                    type=ActionType.MANIPULATE,
                    parameters={
                        "执行器": "吸液枪",
                        "arm": "right",
                        "tool_id": "pipette",
                        "操作": "吸",
                        "容量": 10,
                    },
                )
            ),
        )
    )
    transport = FakeTransport(
        (
            b">01G6158\r\n",
            bytes.fromhex("3e303142363239380d0a"),
            bytes.fromhex("3e3031703032333344450d0a"),
        )
    )
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
        ManualControlService(runtime, ResourceArbiter()).initialize_pipette(
            arm="right", tool_id="pipette"
        )
        result = manager.submit(plan, origin="test").wait(1)
    assert result.state is ExecutionState.FAILED
    assert result.error_category == "protocol"
    assert result.raw_error_code == "02"
    assert len(transport.calls) == 3
    runtime.shutdown(PIPETTE)


@pytest.mark.parametrize("lose_first_query", [False, True])
def test_sequence_waits_for_pump_completion_before_starting_next_liquid_action(lose_first_query):
    waiting = Event()
    finish_motion = Event()

    def frame(body: bytes) -> bytes:
        return body + f"{ADP._cal_crc(body):04X}".encode("ascii") + b"\r\n"

    class GatedTransport(FakeTransport):
        queries = 0

        def transact_with_strategy(self, strategy):
            if strategy.payload == b">01dB819":
                self.queries += 1
                if self.queries == (3 if lose_first_query else 2):
                    waiting.set()
                    assert finish_motion.wait(2), "test did not release pump completion"
            return super().transact_with_strategy(strategy)

    timeout_replies = (
        (
            TransportError(
                "serial timeout",
                category=TransportErrorCategory.TIMEOUT,
                operation="read_until",
            ),
        )
        if lose_first_query
        else ()
    )
    transport = GatedTransport(
        (
            b">01G6158\r\n",
            frame(b">01n01"),
            *timeout_replies,
            frame(b">01d00"),
            frame(b">01d01"),
            frame(b">01p01"),
            frame(b">01d01"),
        )
    )
    runtime = _runtime()
    settings = ApplicationSettings.defaults()
    engine = ActionEngine(
        runtime,
        settings.execution,
        settings.devices,
        settings.vision,
        lambda: None,
        ExecutionContext(),
        MagicMock(),
    )
    resources = ResourceArbiter()
    manager = ExecutionManager(engine, resources, engine.required_resources)
    plan = ExecutionPlan.from_entries(
        tuple(
            SequenceItem.from_definition(
                ActionDefinition(
                    id=f"liquid-{index}",
                    name=f"liquid-{index}",
                    type=ActionType.MANIPULATE,
                    parameters={
                        "执行器": "吸液枪",
                        "arm": "right",
                        "tool_id": "pipette",
                        "操作": operation,
                        "容量": 10,
                        "全吐": False,
                    },
                )
            )
            for index, operation in enumerate(("吸", "吐"))
        )
    )
    try:
        with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
            ManualControlService(runtime, resources).initialize_pipette(
                arm="right", tool_id="pipette"
            )
            handle = manager.submit(plan, origin="test")
            try:
                assert waiting.wait(2), "pump completion query was never reached"
                assert handle.snapshot().state is ExecutionState.RUNNING
                assert resources.owner_of(PIPETTE) is not None
                assert [call.payload for call in transport.calls] == [
                    b">01G6158",
                    ADP(transport)._create_command("n", 10),
                ] + [b">01dB819"] * (2 if lose_first_query else 1)
            finally:
                finish_motion.set()
                result = handle.wait(2)
        assert result.state is ExecutionState.SUCCEEDED
        assert [call.payload for call in transport.calls][5 if lose_first_query else 4 :] == [
            ADP(transport)._create_command("p", 10),
            b">01dB819",
        ]
        assert resources.owner_of(PIPETTE) is None
    finally:
        runtime.shutdown(PIPETTE)


def test_application_shutdown_closes_pipette_without_reset_or_eject_commands():
    runtime = _runtime()
    resources = ResourceArbiter()
    execution = MagicMock()
    execution.snapshot.return_value.active = False
    trajectory = MagicMock()
    trajectory.active = False
    safety = SafetyService(
        execution,
        runtime,
        MagicMock(),
        trajectory,
        wait_timeout_seconds=1,
    )
    devices = DeviceManagementService(runtime, resources, safety)
    transport = FakeTransport((b">01G6158\r\n",))
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
        ManualControlService(runtime, resources).initialize_pipette(arm="right", tool_id="pipette")
        assert devices.shutdown_all() == {}

    assert transport.closed
    assert [call.payload for call in transport.calls] == [b">01G6158"]
    assert runtime.snapshot(PIPETTE).state is DeviceState.STOPPED

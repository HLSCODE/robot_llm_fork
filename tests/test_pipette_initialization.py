from __future__ import annotations

from dataclasses import replace
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from src.application.services import DeviceManagementService, ManualControlService
from src.application.safety import SafetyService
from src.configuration.settings import ApplicationSettings, CameraProfile
from src.devices import DeviceOperationError, ResourceArbiter
from src.devices.runtime.factory import create_device_runtime
from src.devices.runtime.ids import PIPETTE
from src.devices.runtime.models import DeviceState
from src.devices.tools.pipette.adapter import PipetteAdapter
from src.devices.tools.pipette.driver import ADP
from src.devices.transports import TransportError, TransportErrorCategory
from src.devices.transports.testing import FakeTransport
from src.gui.view_models.models import DeviceViewState
from src.gui.views.device import DeviceControlView
from src.domain.execution_context import ExecutionContext
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.execution.engine import ActionEngine
from src.execution.manager import ExecutionManager
from src.execution.models import ExecutionState


def _runtime():
    settings = ApplicationSettings.defaults()
    settings = replace(settings, vision=replace(
        settings.vision,
        cameras=(CameraProfile(name="test", provider="opencv", device_id="0"),),
    ))
    return create_device_runtime(settings, simulation=False)


def test_runtime_initializes_pipette_before_ready_and_does_not_repeat_handshake():
    runtime = _runtime()
    transport = FakeTransport((b">01G6158\r\n",))
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
        device = runtime.require(PIPETTE)
        assert runtime.initialize(PIPETTE) is device
    assert runtime.snapshot(PIPETTE).ready
    assert [call.payload for call in transport.calls] == [b">01G6158"]


def test_initialization_timeout_closes_transport_and_records_failure():
    runtime = _runtime()
    error = TransportError("no reply", category=TransportErrorCategory.TIMEOUT)
    transport = FakeTransport((error,))
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
        with pytest.raises(DeviceOperationError):
            runtime.require(PIPETTE)
    status = runtime.snapshot(PIPETTE)
    assert status.state is DeviceState.FAILED
    assert not status.ready
    assert status.error_category == "timeout"
    assert transport.closed
    assert runtime.get_if_ready(PIPETTE) is None


def test_negative_initialization_result_does_not_mark_device_ready():
    runtime = _runtime()
    transport = FakeTransport()
    with (
        patch("src.devices.runtime.factory.SerialTransport", return_value=transport),
        patch.object(PipetteAdapter, "initialize", return_value=False),
        pytest.raises(DeviceOperationError),
    ):
        runtime.initialize(PIPETTE)
    assert runtime.snapshot(PIPETTE).state is DeviceState.FAILED
    assert transport.closed


def test_manual_reconnect_recovers_cached_failure_and_sends_one_handshake():
    runtime = _runtime()
    resources = ResourceArbiter()
    manual = ManualControlService(runtime, resources)
    failed = FakeTransport((TransportError("timeout", category=TransportErrorCategory.TIMEOUT),))
    recovered = FakeTransport((b">01G6158\r\n",))
    with patch("src.devices.runtime.factory.SerialTransport", side_effect=(failed, recovered)):
        with pytest.raises(DeviceOperationError):
            manual.initialize_pipette()
        assert manual.initialize_pipette()
    assert failed.closed
    assert runtime.snapshot(PIPETTE).ready
    assert len(recovered.calls) == 1
    assert resources.owner_of(PIPETTE) is None
    runtime.shutdown(PIPETTE)
    assert recovered.closed


def test_failed_reinitialization_removes_previous_ready_state():
    runtime = _runtime()
    manual = ManualControlService(runtime, ResourceArbiter())
    ready = FakeTransport((b">01G6158\r\n",))
    failed = FakeTransport((TransportError("timeout", category=TransportErrorCategory.TIMEOUT),))
    with patch("src.devices.runtime.factory.SerialTransport", side_effect=(ready, failed)):
        assert manual.initialize_pipette()
        with pytest.raises(DeviceOperationError):
            manual.initialize_pipette()
    assert ready.closed
    assert failed.closed
    assert not runtime.snapshot(PIPETTE).ready


def test_reply_02_fails_sequence_without_retry_or_starting_next_action():
    runtime = _runtime()
    settings = ApplicationSettings.defaults()
    engine = ActionEngine(
        runtime, settings.execution, settings.devices, settings.vision,
        lambda: None, ExecutionContext(), MagicMock(),
    )
    manager = ExecutionManager(engine, ResourceArbiter(), engine.required_resources)
    plan = ExecutionPlan.from_entries((SequenceItem.from_definition(ActionDefinition(
        id="tu-200", name="tu-200", type=ActionType.MANIPULATE,
        parameters={
            "执行器": "吸液枪", "操作": "吐", "容量": 200,
            "吐液速度": 800, "吐液容量模式": "指定容量", "全吐": False,
        },
    )), SequenceItem.from_definition(ActionDefinition(
        id="next-absorb", name="next-absorb", type=ActionType.MANIPULATE,
        parameters={"执行器": "吸液枪", "操作": "吸", "容量": 10},
    ))))
    transport = FakeTransport((
        b">01G6158\r\n",
        bytes.fromhex("3e303142363239380d0a"),
        bytes.fromhex("3e3031703032333344450d0a"),
    ))
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
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

    timeout_replies = (TransportError(
        "serial timeout", category=TransportErrorCategory.TIMEOUT, operation="read_until",
    ),) if lose_first_query else ()
    transport = GatedTransport((
        b">01G6158\r\n",
        frame(b">01n01"), *timeout_replies, frame(b">01d00"), frame(b">01d01"),
        frame(b">01p01"), frame(b">01d01"),
    ))
    runtime = _runtime()
    settings = ApplicationSettings.defaults()
    engine = ActionEngine(
        runtime, settings.execution, settings.devices, settings.vision,
        lambda: None, ExecutionContext(), MagicMock(),
    )
    resources = ResourceArbiter()
    manager = ExecutionManager(engine, resources, engine.required_resources)
    plan = ExecutionPlan.from_entries(tuple(
        SequenceItem.from_definition(ActionDefinition(
            id=f"liquid-{index}", name=f"liquid-{index}", type=ActionType.MANIPULATE,
            parameters={
                "执行器": "吸液枪", "操作": operation, "容量": 10, "全吐": False,
            },
        ))
        for index, operation in enumerate(("吸", "吐"))
    ))
    try:
        with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
            handle = manager.submit(plan, origin="test")
            try:
                assert waiting.wait(1), "pump completion query was never reached"
                assert handle.snapshot().state is ExecutionState.RUNNING
                assert resources.owner_of(PIPETTE) is not None
                assert [call.payload for call in transport.calls] == [
                    b">01G6158", ADP(transport)._create_command("n", 10),
                ] + [b">01dB819"] * (2 if lose_first_query else 1)
            finally:
                finish_motion.set()
                result = handle.wait(2)
        assert result.state is ExecutionState.SUCCEEDED
        assert [call.payload for call in transport.calls][5 if lose_first_query else 4:] == [
            ADP(transport)._create_command("p", 10), b">01dB819",
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
        execution, runtime, MagicMock(), trajectory, wait_timeout_seconds=1,
    )
    devices = DeviceManagementService(runtime, resources, safety)
    transport = FakeTransport((b">01G6158\r\n",))
    with patch("src.devices.runtime.factory.SerialTransport", return_value=transport):
        devices.initialize(PIPETTE)
        assert devices.shutdown_all() == {}

    assert transport.closed
    assert [call.payload for call in transport.calls] == [b">01G6158"]
    assert runtime.snapshot(PIPETTE).state is DeviceState.STOPPED


def test_gui_keeps_initialize_available_when_pipette_is_not_ready():
    application = QApplication.instance() or QApplication([])
    view = DeviceControlView()
    view.render_state(DeviceViewState(False, False, False, False))
    assert view._pipette_initialize_button.isEnabled()
    assert not view._pipette_button.isEnabled()
    view.set_pipette_action_enabled(False)
    view.render_state(DeviceViewState(False, False, True, False))
    assert not view._pipette_initialize_button.isEnabled()
    assert not view._pipette_button.isEnabled()
    view.set_pipette_action_enabled(True)
    assert view._pipette_button.isEnabled()
    view.render_state(DeviceViewState(False, False, False, False))
    assert view._pipette_initialize_button.isEnabled()
    assert not view._pipette_button.isEnabled()
    view.deleteLater()
    application.processEvents()

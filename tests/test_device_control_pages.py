from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from src.application import create_application_services
from src.configuration.settings import ApplicationSettings
from src.devices.runtime.ids import BODY_AXIS
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.execution import ExecutionState
from src.gui.controllers.main_window import MainWindow
from src.gui.view_models.models import DeviceViewState
from src.gui.views.device import DeviceControlView


def test_device_switching_is_display_only_keeps_inputs_and_stop_outside_scroll():
    app = QApplication.instance() or QApplication([])
    view = DeviceControlView()
    tools = QWidget()
    view.use_configured_end_effectors(tools)
    moves, relays, stops = [], [], []
    view.body_move_requested.connect(moves.append)
    view.relay_requested.connect(lambda channel, enabled: relays.append((channel, enabled)))
    view.execution_stop_requested.connect(lambda: stops.append(True))
    try:
        assert view.device_selector.currentData() == "tools"
        assert view._pages["relay"].isHidden() and view._body_page.isHidden()
        view.body_position.setValue(12000)
        for identity in ("relay", "body", "tools", "body"):
            view.device_selector.setCurrentIndex(view.device_selector.findData(identity))
            app.processEvents()
            assert all(page.isHidden() == (key != identity) for key, page in view._pages.items())
        assert not moves and not relays and not stops
        assert view.body_position.value() == 12000
        assert not view.body_move_button.isEnabled()
        assert "未就绪" in view.body_status.text()
        view.render_state(DeviceViewState(True, True, True))
        view.body_move_button.click()
        assert moves == [12000]
        view.set_execution_active(True)
        assert not view.body_position.isEnabled() and not view.body_move_button.isEnabled()
        assert view.device_selector.isEnabled() and view.stop_button.isEnabled()
        for identity in ("relay", "tools", "body"):
            view.device_selector.setCurrentIndex(view.device_selector.findData(identity))
            assert view.stop_button.isEnabled() and not view.stop_button.isHidden()
        view.stop_button.click()
        assert stops == [True]
        assert view.body_position.minimum() == 0 and view.body_position.maximum() == 500000
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_body_ui_submits_normal_move_action_not_direct_device_io():
    captured = []
    window = SimpleNamespace(
        _services=SimpleNamespace(
            settings=SimpleNamespace(robot_profile_id=lambda: "tianji-tianji-dual")
        ),
        _start_sequence_execution=lambda entries, **options: captured.append((entries, options)),
    )
    MainWindow._execute_manual_body(window, 12345)
    action = captured[0][0][0].definition
    assert action.type is ActionType.MOVE
    assert action.parameters == {"目标": "身体", "位置": 12345}
    assert action.robot_profile_id == "tianji-tianji-dual"


def test_running_body_action_cancellation_sends_stop_before_run_finishes():
    services = create_application_services(ApplicationSettings.defaults(), simulation=True)
    services.devices.initialize(BODY_AXIS)
    body = services.devices._runtime.require(BODY_AXIS)
    polled = Event()

    def moving():
        polled.set()
        return False

    action = ActionDefinition("body", "身体升降", ActionType.MOVE, {"目标": "身体", "位置": 123})
    try:
        with (
            patch.object(body, "is_reached", side_effect=moving),
            patch.object(body, "emergency_stop") as stop,
        ):
            handle = services.execution.start_entries(
                [SequenceItem("body-node", action)], origin="test"
            )
            assert polled.wait(3)
            handle.cancel()
            assert handle.wait(3).state is ExecutionState.CANCELLED
            stop.assert_called_once()
        assert body.position == 123
    finally:
        services.devices.shutdown_all()


@pytest.mark.parametrize("position", [-1, 500001, True, float("inf"), 12.3])
def test_body_invalid_target_fails_before_movement(position):
    from src.execution.handlers.motion import BodyMoveActionHandler, MotionHandlerOptions
    from src.execution import ActionExecutionContext, ExecutionControl

    runtime = Mock()
    context = ActionExecutionContext(
        action_name="body", control=ExecutionControl(), timeout_seconds=1, log=lambda *_: None
    )
    result = BodyMoveActionHandler(runtime, MotionHandlerOptions())({"位置": position}, context)
    assert not result.successful
    runtime.get_if_ready.assert_not_called()

from __future__ import annotations

import time
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.application.teaching import TeachingService
from src.devices import ResourceArbiter
from src.devices.runtime.arm_models import ArmId
from src.devices.runtime.fakes import SimulatedRobotSystem
from src.domain.action_schema import validate_action_parameters
from src.gui.controllers.teaching import TeachingController
from src.gui.icons import IconName
from src.gui.views.teaching import TeachingView
from src.gui.views.teaching_arrow import TeachingArrowButton
from src.gui.views.workbench import WorkbenchPage, WorkbenchView


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


def test_workspace_navigation_preserves_editor(application):
    editor, actions, logs = QWidget(), QWidget(), QWidget()
    teaching = TeachingView()
    shell = WorkbenchView(
        side_pages=(WorkbenchPage("actions", "基础动作", IconName.ACTIONS, actions),
                    WorkbenchPage("teaching", "示教", IconName.POSES, teaching, True)),
        editor=editor, bottom_pages=(WorkbenchPage("logs", "日志", IconName.LOGS, logs),),
    )
    shell.show()
    shell.toggle_side_page("teaching")
    assert shell.workspace_stack.currentWidget() is teaching
    assert editor.isHidden() is False  # preserved under the hidden splitter
    assert not editor.isVisible()
    shell.toggle_side_page("actions")
    assert editor.isVisible()
    assert shell.side_stack.currentWidget() is actions
    shell.close()


def test_session_move_save_and_page_hide(application):
    robot = SimulatedRobotSystem()
    service = TeachingService(SimpleNamespace(get_if_ready=lambda _id: robot), ResourceArbiter())
    saved = []
    services = SimpleNamespace(
        manual_control=SimpleNamespace(teaching=service),
        settings=SimpleNamespace(robot=SimpleNamespace(provider="tianji")),
        composition=SimpleNamespace(robot_profile_id="tianji-test",
                                    create_action=lambda action, **_kw: saved.append(action)),
    )
    view = TeachingView()
    controller = TeachingController(view, services, view)
    view.show()
    QApplication.processEvents()
    try:
        assert not view.motion_controls.isEnabled()
        view.enter_button.click()
        wait_for(lambda: view.motion_controls.isEnabled())
        assert service.active
        assert not view.arm.isEnabled()
        view.axis_rows[0][2].click()
        wait_for(lambda: robot.states[ArmId.LEFT].pose.x_m > 0)
        wait_for(lambda: controller._command is None)
        with patch("src.gui.controllers.teaching.ask_text", return_value=("示教点", True)):
            view.save_button.click()
        wait_for(lambda: len(saved) == 1)
        assert saved[0].robot_profile_id == "tianji-test"
        assert validate_action_parameters(saved[0].type, saved[0].parameters).is_valid
        view.hide()
        wait_for(lambda: not service.active)
    finally:
        controller.prepare_shutdown()
        controller.shutdown()
        view.close()


def test_dynamic_joint_rows_and_disabled_unsupported_motion(application):
    view = TeachingView()
    robot = SimulatedRobotSystem()
    view.render_state(robot.states[ArmId.LEFT], joints_supported=False)
    view.set_session(active=True, ready=True, busy=False)
    view.mode.setCurrentIndex(1)
    assert len(view.axis_rows) == 7
    assert view.axis_rows[6][0].text() == "J7"
    assert not view.axis_rows[0][2].isEnabled()
    assert not view.save_button.isEnabled()
    view.close()


@pytest.mark.parametrize("width", [460, 820])
def test_panel_padding_and_fixed_stop_remain_visible(application, width):
    view = TeachingView()
    view.resize(width, 750)
    view.show()
    QApplication.processEvents()
    assert view.controls_panel.x() >= 16
    assert view.display_panel.x() - view.controls_panel.geometry().right() >= 24
    assert view.stop_button.x() >= 16
    assert view.stop_button.geometry().right() <= view.controls_panel.width() - 16
    assert view.display_panel.geometry().right() <= view.width() - 16
    assert view.stop_button.isVisible()
    view.close()


@pytest.mark.parametrize("width", [460, 820])
@pytest.mark.parametrize("joint", [False, True])
def test_axis_labels_are_centered_between_equal_arrow_columns(application, width, joint):
    view = TeachingView()
    view.render_state(SimulatedRobotSystem().states[ArmId.LEFT], joints_supported=True)
    view.mode.setCurrentIndex(int(joint))
    view.resize(width, 850)
    view.show()
    QApplication.processEvents()
    for label, minus, plus in view.axis_rows:
        if label.isHidden():
            continue
        assert minus.geometry().right() < label.x()
        assert label.geometry().right() < plus.x()
        assert abs(minus.width() - plus.width()) <= 1
        midpoint = (minus.geometry().center().x() + plus.geometry().center().x()) / 2
        assert abs(label.geometry().center().x() - midpoint) <= 1
        assert label.alignment() == Qt.AlignmentFlag.AlignCenter
        assert label.font().bold()
    view.close()


def test_busy_disables_motion_and_save_but_keeps_stop_available(application):
    view = TeachingView()
    requests = []
    view.step_requested.connect(lambda *args: requests.append(args))
    view.set_session(active=True, ready=True, busy=False)
    view.axis_rows[0][2].click()
    assert len(requests) == 1
    view.set_session(active=True, ready=True, busy=True)
    assert view.motion_controls.isEnabled()
    assert view.mode.isEnabled()
    assert all(not button.isEnabled() for row in view.axis_rows for button in row[1:])
    assert not view.save_button.isEnabled()
    assert view.stop_button.isEnabled()
    view.axis_rows[0][2].click()
    assert len(requests) == 1
    view.render_state(SimulatedRobotSystem().states[ArmId.LEFT], joints_supported=True)
    view.mode.setCurrentIndex(1)
    assert all(not button.isEnabled() for row in view.axis_rows for button in row[1:])
    assert not view.save_button.isEnabled()
    view.set_session(active=True, ready=True, busy=False)
    assert view.axis_rows[0][2].isEnabled()
    assert view.save_button.isEnabled()
    view.set_session(active=True, ready=False, busy=True)
    assert not view.motion_controls.isEnabled()
    assert view.stop_button.isEnabled()
    view.close()


def test_telemetry_does_not_rebuild_unchanged_controls(application):
    view = TeachingView()
    state = SimulatedRobotSystem().states[ArmId.LEFT]
    view.render_state(state, joints_supported=True)
    with patch.object(view, "_update_axes", wraps=view._update_axes) as update:
        for _ in range(5):
            view.render_state(state, joints_supported=True)
        update.assert_not_called()
        view.mode.setCurrentIndex(1)
        update.assert_called_once()
    view.close()


def test_arrow_buttons_keep_direction_and_refresh_step_hints(application):
    view = TeachingView()
    requests = []
    view.step_requested.connect(lambda *args: requests.append(args))
    view.set_session(active=True, ready=True, busy=False)
    minus, plus = view.axis_rows[0][1:]
    assert minus.text() == "−"
    assert plus.text() == "+"
    view.distance.setValue(2.5)
    assert minus.toolTip() == "X 减小 2.5 mm（单步）"
    minus.click()
    plus.click()
    assert [request[2] for request in requests] == [-2.5, 2.5]
    view.render_state(SimulatedRobotSystem().states[ArmId.LEFT], joints_supported=True)
    view.mode.setCurrentIndex(1)
    view.angle.setValue(.5)
    assert plus.toolTip() == "J1 增大 0.5°（单步）"
    assert view.axis_rows[6][1].accessibleName() == "J7 减小 0.5°（单步）"
    view.mode.setCurrentIndex(0)
    assert view.axis_rows[3][2].toolTip() == "Rx 正向旋转 0.5°（单步）"
    view.close()


@pytest.mark.parametrize("positive", [False, True])
def test_arrow_click_region_excludes_rectangular_corners(application, positive):
    button = TeachingArrowButton(positive=positive)
    button.resize(100, 40)
    button.show()
    QApplication.processEvents()
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert not clicks
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    assert len(clicks) == 1
    button.setEnabled(False)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    assert len(clicks) == 1
    button.close()


def test_completion_preserves_freshness_and_controller_guards_busy(application):
    robot = SimulatedRobotSystem()
    service = TeachingService(SimpleNamespace(get_if_ready=lambda _id: robot), ResourceArbiter())
    services = SimpleNamespace(
        manual_control=SimpleNamespace(teaching=service),
        settings=SimpleNamespace(robot=SimpleNamespace(provider="tianji")),
    )
    view = TeachingView()
    controller = TeachingController(view, services, view)
    service.begin(ArmId.LEFT)
    sampled_at = time.monotonic()
    controller._last_state_at = sampled_at
    command = Future()
    controller._command = command
    try:
        with patch.object(controller._commands, "submit") as submit:
            controller.step(False, 0, 1, 5)
            controller.save()
            submit.assert_not_called()
        command.set_result(None)
        controller._tick()
        assert controller._last_state_at == sampled_at
        assert controller._ready()
        assert view.motion_controls.isEnabled()
    finally:
        controller.prepare_shutdown()
        controller.shutdown()
        view.close()

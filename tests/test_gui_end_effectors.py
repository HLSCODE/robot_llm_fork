from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.configuration.end_effectors import EndEffectorMount
from src.gui.controllers.end_effector_status import EndEffectorStatusController
from src.gui.views.device import DeviceHealthView
from src.gui.views.end_effector_controls import EndEffectorControls


def test_manual_controls_emit_explicit_arm_and_tool():
    application = QApplication.instance() or QApplication([])

    def choices(kind, arm):
        return [("gripper_a", "配置的夹爪")] if kind == "gripper" else []

    view = EndEffectorControls(choices, ("left", "right"))
    commands = []
    view.action_requested.connect(commands.append)
    view.arm.setCurrentIndex(1)
    buttons = [view._buttons.itemAt(i).widget() for i in range(view._buttons.count())]
    assert all(isinstance(button, QPushButton) for button in buttons)
    buttons[0].click()
    assert commands == [{"执行器": "夹爪", "操作": "开", "arm": "right", "tool_id": "gripper_a"}]
    view.close()
    view.deleteLater()
    application.processEvents()


def test_status_failure_preserves_last_sample_and_expiry_is_unknown():
    application = QApplication.instance() or QApplication([])
    parent, view = QWidget(), DeviceHealthView()
    # The controller needs only mount descriptions; no runtime or hardware.
    settings = SimpleNamespace(
        mounts=(EndEffectorMount("right", "exchangeable", changer="changer", tools=("pipette",)),),
        status_poll_interval_seconds=2,
    )
    service = SimpleNamespace(
        routing=SimpleNamespace(settings=settings), read_changer_states=Mock(return_value=[])
    )
    controller = EndEffectorStatusController(service, view, parent)
    controller._timer.stop()
    controller._next_query = float("inf")
    sample = {"arm": "right", "locked": "locked", "power": "on", "sampled_at": 100.0}
    try:
        with patch("src.gui.controllers.end_effector_status.time.time", return_value=101.0):
            future = Future()
            future.set_result([sample])
            controller._future = future
            controller._tick()
            assert "锁紧" in view._end_effectors.text()
        with patch("src.gui.controllers.end_effector_status.time.time", return_value=120.0):
            controller._tick()
            assert "未知" in view._end_effectors.text()
            assert "过期" in view._end_effectors.text()
        failure = Future()
        failure.set_result(
            [
                {
                    "arm": "right",
                    "locked": "unknown",
                    "power": "unknown",
                    "sampled_at": None,
                    "error": "timeout",
                }
            ]
        )
        controller._future = failure
        with patch("src.gui.controllers.end_effector_status.time.time", return_value=102.0):
            controller._tick()
        assert controller._states[0]["sampled_at"] == 100.0
        assert "timeout" in view._end_effectors.text()
        assert "installed_tool" not in controller._states[0]
    finally:
        controller.prepare_shutdown()
        controller.shutdown()
        parent.deleteLater()
        view.deleteLater()
        application.processEvents()


def test_action_form_filters_tools_when_arm_changes():
    from src.domain.models import ActionType
    from src.gui.views.dialogs import SchemaActionForm

    application = QApplication.instance() or QApplication([])

    def choices(kind, arm):
        assert kind == "gripper"
        return [(arm + "_tool", arm + "工具")] if arm else []

    form = SchemaActionForm(
        ActionType.MANIPULATE,
        {"执行器": "夹爪", "arm": "left", "tool_id": "left_tool", "操作": "开"},
        tool_choices_reader=choices,
    )
    try:
        assert form._field_widgets["tool_id"].currentData() == "left_tool"
        arm = form._field_widgets["arm"]
        arm.setCurrentIndex(arm.findData("right"))
        application.processEvents()
        tool = form._field_widgets["tool_id"]
        assert tool.findData("right_tool") >= 0
        # A persisted old selection is not silently replaced by another tool.
        assert tool.currentData() != "right_tool"
    finally:
        form.close()
        form.deleteLater()
        application.processEvents()


def test_none_and_unsupported_native_mounts_are_visible_without_startup_failure():
    from dataclasses import replace
    from src.application.end_effectors import EndEffectorService
    from src.configuration.end_effectors import (
        EndEffectorMount,
        EndEffectorSettings,
        EndEffectorTool,
    )
    from src.configuration.settings import ApplicationSettings
    from src.devices import ResourceArbiter
    from src.devices.runtime.factory import create_device_runtime

    application = QApplication.instance() or QApplication([])
    defaults = ApplicationSettings.defaults()
    settings = replace(
        defaults,
        robot=replace(defaults.robot, provider="tianji"),
        end_effectors=EndEffectorSettings(
            tools=(EndEffectorTool("native", "gripper", "robot"),),
            mounts=(
                EndEffectorMount("left", "fixed", tool="native"),
                EndEffectorMount("right", "none"),
            ),
        ),
    )
    runtime = create_device_runtime(settings, simulation=True)
    parent = QWidget()
    view = DeviceHealthView(parent)
    service = EndEffectorService(runtime, ResourceArbiter())
    controller = EndEffectorStatusController(service, view, parent)
    controls = EndEffectorControls(service.choices, ("left", "right"), parent)
    try:
        text = view._end_effectors.text()
        assert "内置夹爪" in text and "不可用" in text
        assert "右臂：未安装末端工具" in text
        assert controls.tool.count() == 0
        assert controls._buttons.count() == 0
    finally:
        controller.prepare_shutdown()
        controller.shutdown()
        runtime.shutdown_all()
        parent.deleteLater()
        application.processEvents()

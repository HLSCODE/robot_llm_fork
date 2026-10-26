from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QPushButton

from src.application.end_effectors import EndEffectorService
from src.configuration.end_effectors import EndEffectorConnection, EndEffectorMount, EndEffectorSettings, EndEffectorTool
from src.configuration.settings import ApplicationSettings
from src.devices import ResourceArbiter
from src.devices.runtime.factory import create_device_runtime
from src.gui.controllers.main_window import MainWindow
from src.gui.views.dialogs import SchemaActionForm
from src.gui.views.end_effector_controls import EndEffectorControls
from src.domain.models import ActionType


def service():
    settings = replace(ApplicationSettings.defaults(), end_effectors=EndEffectorSettings(
        connections=(EndEffectorConnection("bus", "COM90"),),
        tools=(
            EndEffectorTool("rgd", "gripper", "dh_rgd", connection="bus", display_name="大寰旋转夹爪", rotation_units_per_degree=10),
            EndEffectorTool("zx", "gripper", "zhixing", connection="bus", station=2, display_name="知行夹爪"),
        ),
        mounts=(EndEffectorMount("left", "fixed", tool="rgd"), EndEffectorMount("right", "fixed", tool="zx")),
    ))
    runtime = create_device_runtime(settings, simulation=True)
    return EndEffectorService(runtime, ResourceArbiter()), runtime


def test_aliases_and_capabilities_are_configuration_only_and_keep_ids():
    tools, runtime = service()
    try:
        with patch.object(tools.routing, "device", side_effect=AssertionError("must not open hardware")):
            assert tools.choices("gripper", "left") == [("rgd", "大寰旋转夹爪")]
            assert tools.choices("gripper", "right") == [("zx", "知行夹爪")]
            rgd, zx = tools.describe_tool("rgd"), tools.describe_tool("zx")
            assert "相对旋转" in rgd.operations and "初始化" in rgd.operations
            assert "相对旋转" not in zx.operations
            assert "rgd" in rgd.details and "dh_rgd" in rgd.details
            assert rgd.rotation_min == -3276.8 and rgd.rotation_max == 3276.7
    finally:
        runtime.shutdown_all()


def test_manual_controls_follow_capability_and_emit_stable_rotation_command():
    app = QApplication.instance() or QApplication([])
    tools, runtime = service()
    view = EndEffectorControls(tools.choices, ("left", "right"), describe_tool=tools.describe_tool)
    commands = []
    view.action_requested.connect(commands.append)
    try:
        assert view.tool.currentText() == "大寰旋转夹爪"
        assert not view._rotation_row.isHidden()
        assert view.angle.maximum() == 3276.7
        view.angle.setValue(-20)
        next(button for button in view._rotation_row.findChildren(QPushButton) if button.text() == "执行旋转").click()
        assert commands[-1] == {"执行器": "夹爪", "操作": "相对旋转", "arm": "left", "tool_id": "rgd", "旋转角度": -20}
        view.set_execution_active(True)
        assert not view.arm.isEnabled() and not view.angle.isEnabled()
        assert view.stop_rotation.isEnabled()
        view.stop_rotation.click()
        assert commands[-1]["操作"] == "停止旋转"
        view.set_execution_active(False)
        view.arm.setCurrentIndex(1)
        assert view.tool.currentText() == "知行夹爪"
        assert view._rotation_row.isHidden()
        assert not view._position_row.isHidden()
        assert all(view._buttons.itemAt(i).widget().text() != "初始化" for i in range(view._buttons.count()))
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()
        runtime.shutdown_all()


def test_gripper_form_filters_operations_and_only_shows_relevant_parameters():
    app = QApplication.instance() or QApplication([])
    tools, runtime = service()
    form = SchemaActionForm(ActionType.MANIPULATE, {
        "执行器": "夹爪", "arm": "left", "tool_id": "rgd", "操作": "开",
    }, tool_choices_reader=tools.choices, tool_description_reader=tools.describe_tool)
    try:
        operation, tool = form._field_widgets["操作"], form._field_widgets["tool_id"]
        assert tool.currentText() == "大寰旋转夹爪"
        assert form._field_widgets["闭合度"].isHidden()
        assert form._field_widgets["旋转角度"].isHidden()
        operation.setCurrentIndex(operation.findData("相对旋转"))
        assert not form._field_widgets["旋转角度"].isHidden()
        assert "闭合度" not in form.parameters()
        form._field_widgets["arm"].setCurrentIndex(1)
        tool.setCurrentIndex(tool.findData("zx"))
        assert operation.findData("相对旋转") == -1
        assert operation.currentData() == "开"
        operation.setCurrentIndex(operation.findData("定位"))
        assert not form._field_widgets["闭合度"].isHidden()
        assert form._field_widgets["旋转角度"].isHidden()
    finally:
        form.close()
        form.deleteLater()
        app.processEvents()
        runtime.shutdown_all()


def test_stop_running_manual_rotation_cancels_instead_of_queueing_another_action():
    cancel, submit = Mock(), Mock()
    target = {"arm": "left", "tool_id": "rgd"}
    window = SimpleNamespace(
        _execution_view_model=SimpleNamespace(snapshot=lambda: SimpleNamespace(active=True), cancel=cancel),
        _manual_rotation_parameters=target, _notifications=SimpleNamespace(warning=Mock()),
        _start_sequence_execution=submit,
    )
    MainWindow._execute_manual_tool(window, {**target, "操作": "停止旋转"})
    cancel.assert_called_once()
    submit.assert_not_called()
    MainWindow._execute_manual_tool(window, {"arm": "right", "tool_id": "zx", "操作": "停止旋转"})
    assert cancel.call_count == 1

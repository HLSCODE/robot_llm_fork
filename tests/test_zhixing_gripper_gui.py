from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QPushButton

from src.domain.models import ActionType
from src.gui.controllers.gripper_control import ZhixingGripperWorker
from src.gui.view_models.models import DeviceViewState
from src.gui.views.device import DeviceControlView
from src.gui.views.dialogs import SchemaActionForm


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def test_four_buttons_emit_station_and_opening_and_obey_bus_readiness(application):
    view = DeviceControlView()
    calls = []
    view.zhixing_gripper_requested.connect(
        lambda station, percent: calls.append((station, percent))
    )
    buttons = [
        view.findChild(QPushButton, f"zhixingGripper{station}{operation}")
        for station in (1, 2)
        for operation in ("Open", "Close")
    ]
    view.render_state(DeviceViewState(True, True, True, True, False))
    assert all(not button.isEnabled() for button in buttons)
    view.render_state(DeviceViewState(False, False, False, False, True))
    for button in buttons:
        assert button.isEnabled()
        button.click()
    assert calls == [(1, 0), (1, 100), (2, 0), (2, 100)]
    view.set_zhixing_gripper_action_enabled(False)
    view.render_state(DeviceViewState(False, False, False, False, True))
    assert all(not button.isEnabled() for button in buttons)
    view.set_zhixing_gripper_action_enabled(True)
    assert all(button.isEnabled() for button in buttons)
    view.close()


def test_new_and_edited_actions_keep_side_and_fractional_percent(application):
    form = SchemaActionForm(ActionType.MANIPULATE, initial_variant="知行夹爪")
    assert form.parameters() == {"执行器": "知行夹爪", "编号": 1, "开合度": 0}
    parameters = {"执行器": "知行夹爪", "编号": 2, "开合度": 37.5}
    edited = SchemaActionForm(ActionType.MANIPULATE, parameters)
    assert edited.parameters() == parameters
    opening = edited.findChild(QDoubleSpinBox)
    assert opening is not None
    assert (opening.minimum(), opening.maximum()) == (0, 100)
    form.close()
    edited.close()


@pytest.mark.parametrize("width", [380, 700])
def test_gripper_button_labels_fit_at_compact_and_wide_sizes(application, width):
    view = DeviceControlView()
    view.resize(width, 300)
    view.show()
    application.processEvents()
    for button in view.findChildren(QPushButton):
        if button.objectName().startswith("zhixingGripper"):
            assert button.width() >= button.sizeHint().width()
    view.close()


def test_worker_reports_failure_and_finishes_without_gui_io(application):
    service = Mock()
    service.set_zhixing_gripper.side_effect = IOError("bus disconnected")
    worker = ZhixingGripperWorker(service, 2, 100)
    worker.start()
    assert worker.wait(2000)
    service.set_zhixing_gripper.assert_called_once_with(2, 100)
    assert worker.error == "bus disconnected"

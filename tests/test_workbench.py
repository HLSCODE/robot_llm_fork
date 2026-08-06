from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.view_models.models import DeviceViewState
from src.gui.views.workbench import WorkbenchPage, WorkbenchView
from src.gui.views.workbench.shell import (
    BOTTOM_PANEL_MINIMUM_HEIGHT,
    SIDE_BAR_MINIMUM_WIDTH,
    SPLITTER_HIT_WIDTH,
)


class WorkbenchViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.resource_page = QWidget()
        self.device_page = QWidget()
        self.log_page = QWidget()
        self.workbench = WorkbenchView(
            side_pages=(
                WorkbenchPage("resources", "资源", "R", self.resource_page),
            ),
            editor=QWidget(),
            bottom_pages=(
                WorkbenchPage("devices", "设备", "D", self.device_page),
                WorkbenchPage("logs", "日志", "L", self.log_page),
            ),
        )
        self.workbench.resize(900, 700)
        self.workbench.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.workbench.close()
        self.workbench.deleteLater()
        QApplication.processEvents()

    def test_activity_button_toggles_side_page_without_a_separate_handle_button(
        self,
    ) -> None:
        button = self.workbench.activity_bar.buttons["resources"]

        self.assertTrue(button.isChecked())
        self.assertTrue(self.workbench.side_stack.isVisible())
        self.assertEqual(SPLITTER_HIT_WIDTH, self.workbench.side_splitter.handleWidth())

        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        QApplication.processEvents()
        self.assertIsNone(self.workbench.active_side_page)
        self.assertFalse(self.workbench.side_stack.isVisible())
        self.assertFalse(button.isChecked())

        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        QApplication.processEvents()
        self.assertEqual("resources", self.workbench.active_side_page)
        self.assertTrue(self.workbench.side_stack.isVisible())
        self.assertGreaterEqual(
            self.workbench.side_splitter.sizes()[0],
            SIDE_BAR_MINIMUM_WIDTH,
        )

    def test_status_buttons_switch_and_toggle_the_resizable_bottom_panel(self) -> None:
        device_button = self.workbench.status_bar.buttons["devices"]
        log_button = self.workbench.status_bar.buttons["logs"]

        QTest.mouseClick(device_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("devices", self.workbench.active_bottom_page)
        self.assertIs(self.device_page, self.workbench.bottom_stack.currentWidget())
        self.assertGreaterEqual(
            self.workbench.bottom_splitter.sizes()[1],
            BOTTOM_PANEL_MINIMUM_HEIGHT,
        )

        QTest.mouseClick(log_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("logs", self.workbench.active_bottom_page)
        self.assertIs(self.log_page, self.workbench.bottom_stack.currentWidget())
        self.assertTrue(log_button.isChecked())
        self.assertFalse(device_button.isChecked())

        QTest.mouseClick(log_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertIsNone(self.workbench.active_bottom_page)
        self.assertFalse(self.workbench.bottom_stack.isVisible())

    def test_status_bar_renders_device_summary_from_view_state(self) -> None:
        self.workbench.status_bar.render_device_state(
            DeviceViewState(
                robot_ready=True,
                body_ready=True,
                pipette_ready=False,
                relay_ready=False,
            )
        )

        self.assertEqual("● 设备 2/4", self.workbench.status_bar.device_summary.text())
        self.assertEqual(
            "danger",
            self.workbench.status_bar.device_summary.property("themeRole"),
        )


if __name__ == "__main__":
    unittest.main()

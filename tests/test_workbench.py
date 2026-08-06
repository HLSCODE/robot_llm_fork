from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.gui.view_models.models import DeviceViewState
from src.gui.icons import IconName
from src.gui.workbench_layout import (
    LayoutLoadResult,
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WorkbenchLayoutState,
)
from src.gui.views.workbench import WorkbenchPage, WorkbenchView
from src.gui.views.workbench.shell import (
    ACTIVITY_TOOLTIP_DELAY_MS,
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
                WorkbenchPage("resources", "资源", IconName.TASKS, self.resource_page),
            ),
            editor=QWidget(),
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, self.device_page),
                WorkbenchPage("logs", "日志", IconName.LOGS, self.log_page),
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
        self.assertEqual("", button.text())
        self.assertFalse(button.icon().isNull())
        self.assertEqual("资源", button.accessibleName())
        self.assertTrue(button.accessibleDescription())
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

    def test_activity_hover_uses_compact_custom_tooltip(self) -> None:
        button = self.workbench.activity_bar.buttons["resources"]
        tooltip = self.workbench.activity_bar.findChild(
            QLabel,
            "activityToolTip",
        )
        assert tooltip is not None

        QApplication.sendEvent(button, QEvent(QEvent.Type.Enter))
        QTest.qWait(ACTIVITY_TOOLTIP_DELAY_MS + 30)
        QApplication.processEvents()

        self.assertTrue(tooltip.isVisible())
        self.assertEqual("资源", tooltip.text())
        self.assertLessEqual(tooltip.height(), 36)
        self.assertLessEqual(tooltip.width(), 96)
        self.assertEqual("", button.toolTip())
        rendered = tooltip.grab().toImage()
        background = rendered.pixelColor(
            tooltip.width() - 5,
            tooltip.height() // 2,
        )
        expected = tooltip.palette().color(QPalette.ColorRole.ToolTipBase)
        self.assertEqual(255, background.alpha())
        self.assertEqual(expected.name(), background.name())

        QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
        QApplication.processEvents()
        self.assertFalse(tooltip.isVisible())

    def test_status_buttons_switch_and_toggle_the_resizable_bottom_panel(self) -> None:
        device_button = self.workbench.status_bar.buttons["devices"]
        log_button = self.workbench.status_bar.buttons["logs"]

        QTest.mouseClick(device_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("devices", self.workbench.active_bottom_page)
        self.assertEqual("设备", device_button.text())
        self.assertFalse(device_button.icon().isNull())
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

    def test_restores_and_persists_versioned_layout_state(self) -> None:
        store = _MemoryLayoutStore(
            LayoutLoadResult(
                WorkbenchLayoutState(
                    schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
                    side_page="resources",
                    side_visible=False,
                    side_width=360,
                    bottom_page="logs",
                    bottom_visible=True,
                    bottom_height=180,
                )
            )
        )
        workbench = WorkbenchView(
            side_pages=(
                WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),
            ),
            editor=QWidget(),
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, QWidget()),
                WorkbenchPage("logs", "日志", IconName.LOGS, QWidget()),
            ),
            layout_store=store,
        )
        workbench.resize(900, 700)
        workbench.show()
        QApplication.processEvents()

        self.assertIsNone(workbench.active_side_page)
        self.assertEqual("logs", workbench.active_bottom_page)
        workbench.toggle_last_side_page()
        workbench.persist_layout()
        assert store.saved is not None
        self.assertTrue(store.saved.side_visible)
        self.assertEqual("resources", store.saved.side_page)
        self.assertEqual("logs", store.saved.bottom_page)
        workbench.close()

    def test_unknown_persisted_page_recovers_default_layout(self) -> None:
        store = _MemoryLayoutStore(
            LayoutLoadResult(
                WorkbenchLayoutState(
                    schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
                    side_page="removed",
                    side_visible=True,
                    side_width=300,
                    bottom_page="devices",
                    bottom_visible=False,
                    bottom_height=180,
                )
            )
        )
        workbench = WorkbenchView(
            side_pages=(
                WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),
            ),
            editor=QWidget(),
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, QWidget()),
            ),
            layout_store=store,
        )

        self.assertEqual("resources", workbench.active_side_page)
        self.assertIsNotNone(workbench.layout_recovery_reason)
        self.assertTrue(store.cleared)
        workbench.close()

    def test_reset_layout_restores_default_pages_and_sizes(self) -> None:
        store = _MemoryLayoutStore(LayoutLoadResult(None))
        workbench = WorkbenchView(
            side_pages=(
                WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),
            ),
            editor=QWidget(),
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, QWidget()),
                WorkbenchPage("logs", "日志", IconName.LOGS, QWidget()),
            ),
            layout_store=store,
        )
        workbench.toggle_bottom_page("logs")
        workbench.toggle_side_page("resources")

        workbench.reset_layout()

        state = workbench.layout_state()
        self.assertEqual("resources", workbench.active_side_page)
        self.assertIsNone(workbench.active_bottom_page)
        self.assertTrue(state.side_visible)
        self.assertFalse(state.bottom_visible)
        self.assertEqual(280, state.side_width)
        self.assertEqual(220, state.bottom_height)
        self.assertEqual(state, store.saved)
        workbench.close()


class _MemoryLayoutStore:
    def __init__(self, result: LayoutLoadResult) -> None:
        self.result = result
        self.saved: WorkbenchLayoutState | None = None
        self.cleared = False

    def load(self) -> LayoutLoadResult:
        return self.result

    def save(self, state: WorkbenchLayoutState) -> None:
        self.saved = state

    def clear(self) -> None:
        self.cleared = True


if __name__ == "__main__":
    unittest.main()

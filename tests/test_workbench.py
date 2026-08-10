from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.view_models.models import DeviceViewState
from src.gui.icons import IconName
from src.gui.tooltips import install_tooltip_service
from src.gui.workbench_layout import (
    LayoutLoadResult,
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WorkbenchLayoutState,
)
from src.gui.views.workbench import WorkbenchPage, WorkbenchView
from src.gui.views.workflow_canvas.view import WorkflowCanvasView
from src.gui.views.workbench.shell import (
    DETAIL_PANEL_MARGIN,
    SIDE_BAR_MINIMUM_WIDTH,
    SPLITTER_HIT_WIDTH,
)


class WorkbenchViewTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tooltip_service = install_tooltip_service(self.application)
        self.tooltip_service.hide()
        self.resource_page = QWidget()
        self.device_page = QWidget()
        self.log_page = QWidget()
        self.editor = WorkflowCanvasView()
        self.workbench = WorkbenchView(
            side_pages=(WorkbenchPage("resources", "资源", IconName.TASKS, self.resource_page),),
            editor=self.editor,
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, self.device_page),
                WorkbenchPage("logs", "日志", IconName.LOGS, self.log_page),
            ),
        )
        self.workbench.resize(900, 700)
        self.workbench.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.tooltip_service.hide()
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
        tooltip = self.tooltip_service.bubble
        event = QHelpEvent(
            QEvent.Type.ToolTip,
            QPoint(20, 20),
            button.mapToGlobal(QPoint(20, 20)),
        )
        QApplication.sendEvent(button, event)
        QApplication.processEvents()

        self.assertTrue(tooltip.isVisible())
        self.assertEqual("资源", tooltip.text)
        self.assertLessEqual(tooltip.height(), 36)
        self.assertLessEqual(tooltip.width(), 96)
        self.assertEqual("资源", button.toolTip())
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

    def test_status_buttons_switch_and_toggle_the_floating_detail_panel(self) -> None:
        device_button = self.workbench.status_bar.buttons["devices"]
        log_button = self.workbench.status_bar.buttons["logs"]
        editor_size_before = self.editor.size()

        QTest.mouseClick(device_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("devices", self.workbench.active_bottom_page)
        self.assertEqual("", device_button.text())
        self.assertEqual(QPoint(28, 28), QPoint(device_button.width(), device_button.height()))
        self.assertFalse(device_button.icon().isNull())
        self.assertIs(self.device_page, self.workbench.bottom_stack.currentWidget())
        self.assertTrue(self.workbench.detail_panel.isVisible())
        self.assertEqual("设备", self.workbench.detail_panel.title_label.text())
        self.assertEqual(editor_size_before, self.editor.size())
        self.assertLessEqual(
            self.workbench.detail_panel.geometry().bottom(),
            self.workbench.status_bar.geometry().top() - DETAIL_PANEL_MARGIN,
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
        self.assertFalse(self.workbench.detail_panel.isVisible())

    def test_floating_panel_closes_with_escape_and_stays_in_narrow_bounds(self) -> None:
        QTest.mouseClick(
            self.workbench.status_bar.buttons["devices"],
            Qt.MouseButton.LeftButton,
        )
        self.workbench.resize(360, 420)
        QApplication.processEvents()

        panel_geometry = self.workbench.detail_panel.geometry()
        self.assertGreaterEqual(panel_geometry.left(), DETAIL_PANEL_MARGIN)
        self.assertLessEqual(
            panel_geometry.right(),
            self.workbench.width() - DETAIL_PANEL_MARGIN,
        )
        self.assertLessEqual(
            panel_geometry.bottom(),
            self.workbench.status_bar.geometry().top() - DETAIL_PANEL_MARGIN,
        )

        QTest.keyClick(self.workbench, Qt.Key.Key_Escape)
        QApplication.processEvents()
        self.assertIsNone(self.workbench.active_bottom_page)
        self.assertFalse(self.workbench.detail_panel.isVisible())

    def test_escape_reaches_canvas_unless_detail_panel_is_visible(self) -> None:
        cancellations: list[bool] = []
        self.editor.drag_cancel_requested.connect(lambda: cancellations.append(True))

        self.workbench.activateWindow()
        self.editor.setFocus()
        QApplication.processEvents()
        self.assertTrue(self.editor.hasFocus())
        focus_widget = QApplication.focusWidget()
        self.assertIsNotNone(focus_widget)
        assert focus_widget is not None
        QTest.keyClick(focus_widget, Qt.Key.Key_Escape)
        QApplication.processEvents()
        self.assertEqual([True], cancellations)

        self.workbench.toggle_bottom_page("devices")
        self.editor.setFocus()
        QApplication.processEvents()
        focus_widget = QApplication.focusWidget()
        self.assertIsNotNone(focus_widget)
        assert focus_widget is not None
        QTest.keyClick(focus_widget, Qt.Key.Key_Escape)
        QApplication.processEvents()
        self.assertEqual([True], cancellations)
        self.assertIsNone(self.workbench.active_bottom_page)

    def test_detail_pages_keep_one_widget_instance_and_one_stack_owner(self) -> None:
        QTest.mouseClick(
            self.workbench.status_bar.buttons["devices"],
            Qt.MouseButton.LeftButton,
        )
        QTest.mouseClick(
            self.workbench.status_bar.buttons["logs"],
            Qt.MouseButton.LeftButton,
        )
        QApplication.processEvents()

        self.assertEqual(0, self.workbench.bottom_stack.indexOf(self.device_page))
        self.assertEqual(1, self.workbench.bottom_stack.indexOf(self.log_page))
        self.assertIs(self.workbench.bottom_stack, self.device_page.parentWidget())
        self.assertIs(self.workbench.bottom_stack, self.log_page.parentWidget())

    def test_status_bar_consolidates_device_summary_into_details_button(self) -> None:
        self.workbench.status_bar.render_device_state(
            DeviceViewState(
                robot_ready=True,
                body_ready=True,
                pipette_ready=False,
                relay_ready=False,
            )
        )

        device_button = self.workbench.status_bar.buttons["devices"]
        self.assertFalse(hasattr(self.workbench.status_bar, "device_summary"))
        self.assertEqual(
            "statusDanger",
            device_button.property("themeRole"),
        )
        self.assertIn("2/4", device_button.toolTip())

    def test_restores_and_persists_versioned_layout_state(self) -> None:
        store = _MemoryLayoutStore(
            LayoutLoadResult(
                WorkbenchLayoutState(
                    schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
                    side_page="resources",
                    side_visible=False,
                    side_width=360,
                    panel_page="logs",
                    panel_visible=True,
                )
            )
        )
        workbench = WorkbenchView(
            side_pages=(WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),),
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
        self.assertEqual("logs", store.saved.panel_page)
        workbench.close()

    def test_unknown_persisted_page_recovers_default_layout(self) -> None:
        store = _MemoryLayoutStore(
            LayoutLoadResult(
                WorkbenchLayoutState(
                    schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
                    side_page="removed",
                    side_visible=True,
                    side_width=300,
                    panel_page="devices",
                    panel_visible=False,
                )
            )
        )
        workbench = WorkbenchView(
            side_pages=(WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),),
            editor=QWidget(),
            bottom_pages=(WorkbenchPage("devices", "设备", IconName.DEVICES, QWidget()),),
            layout_store=store,
        )

        self.assertEqual("resources", workbench.active_side_page)
        self.assertIsNotNone(workbench.layout_recovery_reason)
        self.assertTrue(store.cleared)
        workbench.close()

    def test_reset_layout_restores_default_pages_and_sizes(self) -> None:
        store = _MemoryLayoutStore(LayoutLoadResult(None))
        workbench = WorkbenchView(
            side_pages=(WorkbenchPage("resources", "资源", IconName.TASKS, QWidget()),),
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
        self.assertFalse(state.panel_visible)
        self.assertEqual(280, state.side_width)
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

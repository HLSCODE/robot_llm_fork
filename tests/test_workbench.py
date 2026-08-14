from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.view_models.models import DeviceViewState
from src.gui.bridges.notifications import GuiNotification, GuiNotificationLevel
from src.gui.icons import IconName
from src.gui.tooltips import install_tooltip_service
from src.gui.workbench_layout import (
    LayoutLoadResult,
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WorkbenchLayoutState,
)
from src.gui.views import ActionLibraryView
from src.gui.views.log_widget import LogFilter
from src.gui.views.workbench import WorkbenchPage, WorkbenchView
from src.gui.views.workflow_canvas.view import WorkflowCanvasView
from src.gui.views.workbench.shell import (
    DETAIL_PANEL_MARGIN,
    NOTIFICATION_TOAST_MARGIN,
    SIDE_BAR_MINIMUM_WIDTH,
    SPLITTER_HIT_WIDTH,
    STATUS_BUTTON_SIZE,
    STATUS_ICON_SIZE,
    STATUS_PROBLEM_CONTENT_SPACING,
    STATUS_PROBLEM_HORIZONTAL_PADDING,
    WORKBENCH_CARD_MARGIN,
    WORKBENCH_CARD_TOP_MARGIN,
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

    def test_content_regions_are_cards_separated_by_an_invisible_resize_gap(
        self,
    ) -> None:
        margins = self.workbench.content_area.layout().contentsMargins()
        self.assertEqual(WORKBENCH_CARD_MARGIN, margins.left())
        self.assertEqual(WORKBENCH_CARD_TOP_MARGIN, margins.top())
        self.assertEqual(WORKBENCH_CARD_MARGIN, margins.right())
        self.assertEqual(WORKBENCH_CARD_MARGIN, margins.bottom())
        self.assertEqual(SPLITTER_HIT_WIDTH, self.workbench.side_splitter.handleWidth())
        self.assertEqual("workbenchSideBar", self.workbench.side_stack.objectName())
        self.assertTrue(self.editor.property("workbenchCard"))
        self.assertEqual(
            self.workbench.activity_bar.buttons["resources"].mapTo(
                self.workbench,
                QPoint(0, 0),
            ).y(),
            self.workbench.side_stack.mapTo(self.workbench, QPoint(0, 0)).y(),
        )
        self.assertLess(
            self.workbench.activity_bar.geometry().right(),
            self.workbench.side_stack.mapTo(self.workbench, QPoint(0, 0)).x(),
        )

    def test_card_scrollbars_reveal_only_while_the_card_is_hovered(self) -> None:
        scroll_bar = self.editor.verticalScrollBar()
        self.assertFalse(scroll_bar.property("cardHover"))

        QApplication.sendEvent(self.editor, QEvent(QEvent.Type.Enter))
        self.assertTrue(scroll_bar.property("cardHover"))

        QApplication.sendEvent(self.editor, QEvent(QEvent.Type.Leave))
        self.assertFalse(scroll_bar.property("cardHover"))

    def test_side_card_commands_release_header_space_while_hidden(self) -> None:
        action_library = ActionLibraryView()
        command = action_library.create_button
        workbench = WorkbenchView(
            side_pages=(
                WorkbenchPage(
                    "resources",
                    "资源",
                    IconName.TASKS,
                    action_library,
                ),
            ),
            editor=QWidget(),
            bottom_pages=(WorkbenchPage("logs", "日志", IconName.LOGS, QWidget()),),
        )
        workbench.resize(720, 480)
        workbench.show()
        QApplication.processEvents()
        available_width = action_library.category_selector.width()
        stable_header_height = action_library.header.height()
        stable_content_y = action_library.action_stack.mapTo(
            action_library,
            QPoint(0, 0),
        ).y()

        self.assertTrue(command.isHidden())
        self.assertTrue(command.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        QApplication.sendEvent(workbench.side_stack, QEvent(QEvent.Type.Enter))
        QApplication.processEvents()
        self.assertTrue(command.isVisible())
        self.assertFalse(command.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertLess(action_library.category_selector.width(), available_width)
        self.assertEqual(stable_header_height, action_library.header.height())
        self.assertEqual(
            stable_content_y,
            action_library.action_stack.mapTo(action_library, QPoint(0, 0)).y(),
        )
        QApplication.sendEvent(workbench.side_stack, QEvent(QEvent.Type.Leave))
        QApplication.processEvents()
        self.assertTrue(command.isHidden())
        self.assertEqual(available_width, action_library.category_selector.width())
        self.assertEqual(stable_header_height, action_library.header.height())
        self.assertEqual(
            stable_content_y,
            action_library.action_stack.mapTo(action_library, QPoint(0, 0)).y(),
        )

        workbench.close()
        workbench.deleteLater()

    def test_splitter_indicator_is_visible_only_on_hover_or_drag(self) -> None:
        handle = self.workbench.side_splitter.handle(1)
        self.assertFalse(handle.indicator_visible)

        QApplication.sendEvent(handle, QEvent(QEvent.Type.Enter))
        self.assertTrue(handle.indicator_visible)
        QApplication.sendEvent(handle, QEvent(QEvent.Type.Leave))
        self.assertFalse(handle.indicator_visible)

        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.assertTrue(handle.indicator_visible)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.assertFalse(handle.indicator_visible)

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

    def test_log_problem_buttons_share_the_log_panel_and_switch_filters(self) -> None:
        status_bar = self.workbench.status_bar
        error_button = status_bar.log_problem_buttons[LogFilter.ERRORS]
        warning_button = status_bar.log_problem_buttons[LogFilter.WARNINGS]
        log_button = status_bar.buttons["logs"]

        self.assertEqual("0", error_button.text())
        self.assertEqual("0", warning_button.text())
        self.assertEqual("statusMuted", error_button.property("themeRole"))
        self.assertEqual("statusMuted", warning_button.property("themeRole"))
        self.assertFalse(error_button.icon().isNull())
        self.assertFalse(warning_button.icon().isNull())
        single_digit_width = error_button.width()
        expected_single_digit_width = max(
            STATUS_BUTTON_SIZE,
            STATUS_ICON_SIZE
            + STATUS_PROBLEM_CONTENT_SPACING
            + error_button.fontMetrics().horizontalAdvance(error_button.text())
            + (2 * STATUS_PROBLEM_HORIZONTAL_PADDING),
        )
        self.assertEqual(expected_single_digit_width, single_digit_width)

        status_bar.render_log_counts(12, 345)
        self.assertEqual("12", error_button.text())
        self.assertEqual("345", warning_button.text())
        self.assertEqual("statusDanger", error_button.property("themeRole"))
        self.assertEqual("statusWarning", warning_button.property("themeRole"))
        self.assertGreater(error_button.width(), single_digit_width)
        self.assertGreater(warning_button.width(), error_button.width())
        expected_warning_width = (
            STATUS_ICON_SIZE
            + STATUS_PROBLEM_CONTENT_SPACING
            + warning_button.fontMetrics().horizontalAdvance(warning_button.text())
            + (2 * STATUS_PROBLEM_HORIZONTAL_PADDING)
        )
        self.assertEqual(expected_warning_width, warning_button.width())

        status_bar.render_log_counts(2, 3)
        self.assertLessEqual(error_button.width(), single_digit_width)
        self.assertLessEqual(warning_button.width(), single_digit_width)

        QTest.mouseClick(error_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("logs", self.workbench.active_bottom_page)
        self.assertTrue(error_button.isChecked())
        self.assertFalse(warning_button.isChecked())
        self.assertFalse(log_button.isChecked())

        QTest.mouseClick(warning_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual("logs", self.workbench.active_bottom_page)
        self.assertFalse(error_button.isChecked())
        self.assertTrue(warning_button.isChecked())

        QTest.mouseClick(log_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertEqual(LogFilter.ALL, status_bar.active_log_filter)
        self.assertTrue(log_button.isChecked())

        QTest.mouseClick(log_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertIsNone(self.workbench.active_bottom_page)

    def test_notification_toast_reuses_one_instance_and_can_close(self) -> None:
        toast = self.workbench.notification_toast
        first = GuiNotification(
            GuiNotificationLevel.WARNING,
            "设备警告",
            "机械臂尚未连接",
        )
        second = GuiNotification(
            GuiNotificationLevel.ERROR,
            "执行失败",
            "动作已停止",
        )

        self.workbench.show_notification(first)
        QApplication.processEvents()
        self.assertTrue(toast.isVisible())
        self.assertEqual("设备警告", toast.title_label.text())

        self.workbench.show_notification(second)
        QApplication.processEvents()
        self.assertIs(toast, self.workbench.notification_toast)
        self.assertEqual("执行失败", toast.title_label.text())
        self.assertEqual("动作已停止", toast.message_label.text())
        self.assertLessEqual(toast.geometry().right(), self.workbench.width())

        QTest.mouseClick(toast.close_button, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        self.assertFalse(toast.isVisible())

    def test_notification_toast_auto_hides(self) -> None:
        toast = self.workbench.notification_toast
        toast.show_notification(
            GuiNotification(
                GuiNotificationLevel.WARNING,
                "警告",
                "短暂提示",
            ),
            timeout_ms=20,
        )
        QApplication.processEvents()
        self.assertTrue(toast.isVisible())
        QTest.qWait(40)
        self.assertFalse(toast.isVisible())

    def test_notification_created_while_hidden_is_anchored_after_first_show(
        self,
    ) -> None:
        self.workbench.hide()
        QApplication.processEvents()
        toast = self.workbench.notification_toast

        self.workbench.show_notification(
            GuiNotification(
                GuiNotificationLevel.WARNING,
                "设备警告",
                "移液枪初始化失败",
            )
        )
        self.assertFalse(toast.isHidden())
        self.assertFalse(toast.isVisible())

        self.workbench.show()
        QApplication.processEvents()

        self.assertTrue(toast.isVisible())
        self.assertEqual(
            self.workbench.width() - toast.width() - NOTIFICATION_TOAST_MARGIN,
            toast.x(),
        )
        self.assertEqual(
            self.workbench.height()
            - self.workbench.status_bar.height()
            - toast.height()
            - NOTIFICATION_TOAST_MARGIN,
            toast.y(),
        )

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

    def test_floating_panel_closes_only_for_left_clicks_outside_it(self) -> None:
        self.workbench.toggle_bottom_page("devices")
        QApplication.processEvents()

        QTest.mouseClick(
            self.workbench.detail_panel.title_label,
            Qt.MouseButton.LeftButton,
        )
        QApplication.processEvents()
        self.assertEqual("devices", self.workbench.active_bottom_page)

        QTest.mouseClick(
            self.editor.viewport(),
            Qt.MouseButton.LeftButton,
            pos=QPoint(20, 20),
        )
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

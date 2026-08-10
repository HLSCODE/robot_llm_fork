from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from src.domain.models import ActionType
from src.gui.icons import ACTION_TYPE_ICONS, IconName, themed_icon
from src.gui.theme import ThemeController, ThemeMode
from src.gui.toolbars import icon_foreground
from src.gui.views.workflow import (
    ActionLibraryView,
    TaskLibraryView,
    WorkflowEditorView,
)


class GuiToolbarTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_every_declared_svg_icon_is_available_from_qt_resources(self) -> None:
        owner = QWidget()

        for icon_name in IconName:
            with self.subTest(icon=icon_name.value):
                self.assertFalse(themed_icon(owner, icon_name).isNull())

    def test_action_categories_use_distinct_semantic_svg_icons(self) -> None:
        owner = QWidget()
        self.assertEqual(set(ActionType), set(ACTION_TYPE_ICONS))
        self.assertEqual(len(ACTION_TYPE_ICONS), len(set(ACTION_TYPE_ICONS.values())))
        for action_type, icon_name in ACTION_TYPE_ICONS.items():
            with self.subTest(action_type=action_type.value):
                self.assertNotEqual(IconName.ACTIONS, icon_name)
                self.assertFalse(themed_icon(owner, icon_name).isNull())

    def test_task_library_actions_are_icon_only_header_commands(self) -> None:
        view = TaskLibraryView()
        opened: list[bool] = []
        inserted: list[bool] = []
        view.task_open_requested.connect(lambda: opened.append(True))
        view.task_insert_requested.connect(lambda: inserted.append(True))

        self.assertEqual("", view.open_button.text())
        self.assertEqual("", view.insert_button.text())
        self.assertFalse(view.open_button.icon().isNull())
        self.assertFalse(view.insert_button.icon().isNull())
        self.assertEqual("打开选中任务", view.open_button.toolTip())
        view.open_button.click()
        view.insert_button.click()

        self.assertEqual([True], opened)
        self.assertEqual([True], inserted)

    def test_action_library_commands_are_icon_only_and_camera_state_is_visible(self) -> None:
        view = ActionLibraryView()

        self.assertEqual(6, view.category_selector.count())
        self.assertEqual("移动类", view.category_selector.currentText())
        self.assertIs(view.current_action_list(), view.action_list(view.current_category_type()))

        for button in (
            view.create_button,
            view.edit_button,
            view.delete_button,
            view.camera_test_button,
        ):
            self.assertEqual("", button.text())
            self.assertFalse(button.icon().isNull())

        view.set_camera_test_running(True)

        self.assertFalse(view.camera_test_button.isEnabled())
        self.assertEqual("相机测试运行中", view.camera_test_button.toolTip())

    def test_workflow_commands_are_above_canvas_and_keep_semantic_actions(self) -> None:
        view = WorkflowEditorView()
        saved: list[bool] = []
        cleared: list[bool] = []
        view.save_requested.connect(lambda: saved.append(True))
        view.clear_requested.connect(lambda: cleared.append(True))
        view.resize(900, 700)
        view.show()
        QApplication.processEvents()
        controls = view.control_panel

        self.assertLess(controls.geometry().top(), view.sequence_list.geometry().top())
        self.assertEqual(12, controls.edit_command_row.count())
        self.assertEqual(6, controls.execution_command_row.count())
        for button in (
            controls.save_btn,
            controls.undo_btn,
            controls.redo_btn,
            controls.fit_btn,
            controls.reset_zoom_btn,
            controls.clear_btn,
            controls.start_btn,
            controls.pause_btn,
            controls.stop_btn,
            controls.quick_stop_btn,
            controls.emergency_stop_btn,
        ):
            self.assertEqual("", button.text())
            self.assertFalse(button.icon().isNull())

        self.assertEqual("将当前流程保存为任务 (Ctrl+S)", controls.save_btn.toolTip())
        self.assertEqual("清空画布", controls.clear_btn.toolTip())
        self.assertEqual("画布适合内容", controls.fit_btn.toolTip())
        self.assertEqual("恢复 100% 缩放", controls.reset_zoom_btn.toolTip())
        controls.save_btn.click()
        controls.clear_btn.click()
        self.assertEqual([True], saved)
        self.assertEqual([True], cleared)

        execution_buttons = (
            controls.start_btn,
            controls.pause_btn,
            controls.stop_btn,
            controls.quick_stop_btn,
            controls.emergency_stop_btn,
        )
        for button in execution_buttons:
            self.assertEqual((44, 44), (button.width(), button.height()))
            self.assertEqual((20, 20), (button.iconSize().width(), button.iconSize().height()))

        view.render_execution_controls("恢复执行", True, True)

        self.assertEqual(IconName.PLAY, controls.pause_btn.icon_name)
        self.assertEqual("恢复执行", controls.pause_btn.toolTip())
        self.assertTrue(controls.pause_btn.isEnabled())
        self.assertTrue(controls.stop_btn.isEnabled())
        view.close()

    def test_disabled_stop_icon_uses_the_light_theme_disabled_foreground(self) -> None:
        original_palette = QApplication.palette()
        original_stylesheet = self.application.styleSheet()
        try:
            ThemeController(self.application, ThemeMode.LIGHT)
            view = WorkflowEditorView()
            view.render_execution_controls("暂停执行", True, False)
            stop_button = view.control_panel.stop_btn

            self.assertFalse(stop_button.isEnabled())
            self.assertEqual(
                stop_button.palette().color(
                    QPalette.ColorGroup.Disabled,
                    QPalette.ColorRole.ButtonText,
                ),
                icon_foreground(stop_button),
            )
            self.assertNotEqual(QColor("#ffffff"), icon_foreground(stop_button))
            view.close()
        finally:
            QApplication.setPalette(original_palette)
            self.application.setStyleSheet(original_stylesheet)
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()

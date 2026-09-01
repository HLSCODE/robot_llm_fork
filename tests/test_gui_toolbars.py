from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QWidget

from src.domain.models import ActionDefinition, ActionType
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
        self.assertIs(view.header, view.category_selector.parentWidget())
        self.assertTrue(view.header.title_label.isHidden())
        self.assertEqual("", view.header.title_label.text())
        self.assertEqual("paneHeaderSelector", view.category_selector.objectName())
        long_category_label = "机械臂移动与升降平台移动"
        view.category_selector.setItemText(0, long_category_label)
        view.category_selector.resize(64, view.category_selector.sizeHint().height())
        self.assertEqual(long_category_label, view.category_selector.currentText())
        self.assertNotEqual(long_category_label, view.category_selector.visible_text())
        self.assertTrue(view.category_selector.visible_text().endswith("…"))
        view.resize(360, 640)
        view.show()
        QApplication.processEvents()
        self.assertEqual(
            view.category_selector.mapTo(view, view.category_selector.rect().center()).y(),
            view.create_button.mapTo(view, view.create_button.rect().center()).y(),
        )

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
        self.assertEqual("正在重新检测相机", view.camera_test_button.toolTip())
        view.close()

    def test_action_library_context_menu_exposes_item_operations(self) -> None:
        view = ActionLibraryView()
        action = ActionDefinition(
            id="action-1",
            name="测试动作",
            type=ActionType.MOVE,
            parameters={},
        )
        action_list = view.current_action_list()
        action_list.add_action(action)
        action_list.setCurrentRow(0)
        inserted: list[ActionDefinition] = []
        edited: list[bool] = []
        deleted: list[bool] = []
        view.action_insert_requested.connect(inserted.append)
        view.edit_requested.connect(lambda: edited.append(True))
        view.delete_requested.connect(lambda: deleted.append(True))

        menu = view._create_action_context_menu(action_list, action)
        actions = [entry for entry in menu.actions() if not entry.isSeparator()]

        self.assertEqual(
            ["插入到画布", "修改动作", "删除动作"],
            [entry.text() for entry in actions],
        )
        self.assertTrue(all(not entry.icon().isNull() for entry in actions))
        for entry in actions:
            entry.trigger()
        self.assertEqual([action], inserted)
        self.assertEqual([True], edited)
        self.assertEqual([True], deleted)

    def test_action_category_selector_places_its_chevron_before_elided_text(self) -> None:
        original_palette = QApplication.palette()
        original_stylesheet = self.application.styleSheet()
        try:
            ThemeController(self.application, ThemeMode.LIGHT)
            view = ActionLibraryView()
            view.resize(360, 640)
            view.show()
            QApplication.processEvents()
            selector = view.category_selector
            option = QStyleOptionComboBox()
            selector.initStyleOption(option)
            drop_down = selector.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxArrow,
                selector,
            )
            text_area = selector.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                selector,
            )

            self.assertLess(drop_down.center().x(), text_area.left())
            chevron_text_gap = text_area.left() - (drop_down.right() + 1)
            self.assertGreaterEqual(chevron_text_gap, 2)
            self.assertLessEqual(chevron_text_gap, 8)
            view.close()
        finally:
            QApplication.setPalette(original_palette)
            self.application.setStyleSheet(original_stylesheet)
            QApplication.processEvents()

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
        self.assertEqual(
            "workflowBreadcrumb",
            view.sequence_list.root_scope_button.objectName(),
        )

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

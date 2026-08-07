from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtWidgets import QApplication, QWidget

from src.gui.icons import IconName, themed_icon
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
        view.resize(900, 700)
        view.show()
        QApplication.processEvents()
        controls = view.control_panel

        self.assertLess(controls.geometry().top(), view.sequence_list.geometry().top())
        for button in (
            controls.undo_btn,
            controls.redo_btn,
            controls.start_btn,
            controls.pause_btn,
            controls.stop_btn,
            controls.quick_stop_btn,
            controls.emergency_stop_btn,
        ):
            self.assertEqual("", button.text())
            self.assertFalse(button.icon().isNull())

        view.render_execution_controls("恢复执行", True, True)

        self.assertEqual(IconName.PLAY, controls.pause_btn.icon_name)
        self.assertEqual("恢复执行", controls.pause_btn.toolTip())
        self.assertTrue(controls.pause_btn.isEnabled())
        self.assertTrue(controls.stop_btn.isEnabled())
        view.close()


if __name__ == "__main__":
    unittest.main()

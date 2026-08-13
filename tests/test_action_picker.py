from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QWidget

from src.domain.models import ActionDefinition, ActionType
from src.gui.views.action_picker import ActionPickerDialog


class ActionPickerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_actions_are_grouped_by_type_instead_of_flattened(self) -> None:
        move = _action("move", "移动", ActionType.MOVE)
        wait = _action("wait", "等待", ActionType.WAIT)
        second_wait = _action("wait-2", "继续等待", ActionType.WAIT)
        dialog = ActionPickerDialog(
            {
                ActionType.MOVE: [move],
                ActionType.WAIT: [wait, second_wait],
                ActionType.INSPECT: [],
            },
            title="插入动作",
        )

        self.assertEqual(2, dialog.category_list.count())
        self.assertEqual("机械臂移动  (1)", dialog.category_list.item(0).text())
        self.assertEqual(1, dialog.action_list.count())
        self.assertIs(move, dialog.selected_action)

        dialog.category_list.setCurrentRow(1)
        self.assertEqual("等待  (2)", dialog.category_list.item(1).text())
        self.assertEqual(2, dialog.action_list.count())
        self.assertIs(wait, dialog.selected_action)

        dialog.close()

    def test_large_platform_font_keeps_category_and_action_rows_separate(self) -> None:
        parent = QWidget()
        font = parent.font()
        font.setPointSize(16)
        parent.setFont(font)
        actions = [
            _action(f"move-{index}", f"机械臂动作 {index}", ActionType.MOVE)
            for index in range(12)
        ]
        dialog = ActionPickerDialog(
            {ActionType.MOVE: actions},
            title="插入动作",
            parent=parent,
        )
        dialog.show()
        self.application.processEvents()

        for list_widget in (dialog.category_list, dialog.action_list):
            minimum_row_height = list_widget.fontMetrics().lineSpacing() + 12
            first_rect = list_widget.visualItemRect(list_widget.item(0))
            self.assertGreaterEqual(first_rect.height(), minimum_row_height)
            if list_widget.count() > 1:
                second_rect = list_widget.visualItemRect(list_widget.item(1))
                self.assertGreaterEqual(second_rect.top(), first_rect.bottom() + 1)

        dialog.close()
        parent.close()


def _action(
    action_id: str,
    name: str,
    action_type: ActionType,
) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        name=name,
        type=action_type,
        parameters={},
    )


if __name__ == "__main__":
    unittest.main()

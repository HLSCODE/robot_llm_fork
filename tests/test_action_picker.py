from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

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

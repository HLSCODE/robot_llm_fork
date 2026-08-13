from __future__ import annotations

import unittest

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QFrame

from src.domain.models import ActionType
from src.gui.app_dialogs import (
    AppDialog,
    AppMessageDialog,
    MessageDialogKind,
    choose_item,
)
from src.gui.views.action_picker import ActionPickerDialog
from src.gui.views.dialogs import ActionConfigDialog


class AppDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_message_dialog_uses_shared_frameless_chrome_and_chinese_buttons(
        self,
    ) -> None:
        dialog = AppMessageDialog(
            MessageDialogKind.WARNING,
            "参数错误",
            "请检查必填参数",
        )

        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        accept_button = buttons.button(QDialogButtonBox.StandardButton.Ok)

        self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIsNotNone(dialog.findChild(QFrame, "appDialogSurface"))
        self.assertEqual("确定", accept_button.text())
        self.assertEqual("请检查必填参数", dialog.message_label.text())
        dialog.close()

    def test_action_dialogs_share_the_same_cross_platform_shell(self) -> None:
        config = ActionConfigDialog(ActionType.WAIT)
        picker = ActionPickerDialog({}, title="插入动作")

        self.assertIsInstance(config, AppDialog)
        self.assertIsInstance(picker, AppDialog)
        self.assertEqual(config.windowTitle(), config.title_bar.title_label.text())
        self.assertEqual("插入动作", picker.title_bar.title_label.text())
        self.assertEqual(
            "取消",
            picker.buttons.button(QDialogButtonBox.StandardButton.Cancel).text(),
        )
        config.close()
        picker.close()

    def test_temporary_choice_dialog_also_uses_shared_shell(self) -> None:
        observed: dict[str, object] = {}
        parent = AppDialog()

        def choose_second_item() -> None:
            dialog = QApplication.activeModalWidget()
            observed["dialog"] = dialog
            if not isinstance(dialog, AppDialog):
                return
            combo = dialog.findChild(QComboBox)
            assert combo is not None
            combo.setCurrentIndex(1)
            buttons = dialog.findChild(QDialogButtonBox)
            assert buttons is not None
            observed["accept_text"] = buttons.button(
                QDialogButtonBox.StandardButton.Ok
            ).text()
            observed["reject_text"] = buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            ).text()
            dialog.accept()

        QTimer.singleShot(0, choose_second_item)
        selected, accepted = choose_item(
            parent,
            "选择移动类型",
            "创建移动类动作:",
            ["机械臂移动", "身体移动"],
        )

        self.assertIsInstance(observed.get("dialog"), AppDialog)
        self.assertEqual("确定", observed.get("accept_text"))
        self.assertEqual("取消", observed.get("reject_text"))
        self.assertTrue(accepted)
        self.assertEqual("身体移动", selected)
        parent.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from src.gui.menus import SUBMENU_GAP, PositionedSubMenu


class PositionedSubMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_visible_submenu_does_not_overlap_parent_menu(self) -> None:
        parent = QMenu()
        parent.addAction("资源侧栏")
        submenu = PositionedSubMenu("主题", parent)
        parent.addMenu(submenu)
        for label in ("跟随系统", "浅色", "深色"):
            submenu.addAction(label)
        primary_screen = self.application.primaryScreen()
        assert primary_screen is not None
        screen = primary_screen.availableGeometry()
        parent.popup(QPoint(screen.left() + 40, screen.top() + 40))
        self.application.processEvents()
        submenu.popup(QPoint(parent.geometry().center()))
        self.application.processEvents()

        self.assertGreaterEqual(
            submenu.frameGeometry().left(),
            parent.frameGeometry().right() + 1 + SUBMENU_GAP,
        )

        submenu.close()
        parent.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from src.gui.menus import ApplicationMenuBar, MENU_PANEL_GAP


class ApplicationMenuBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.window.resize(720, 480)
        self.window.setCentralWidget(QWidget())
        self.menu_bar = ApplicationMenuBar(self.window)
        self.window.setMenuWidget(self.menu_bar)
        self.file_menu = self.menu_bar.addMenu("文件")
        self.save_action = QAction("保存", self.window)
        self.save_action.setShortcut("Ctrl+S")
        self.file_menu.addAction(self.save_action)
        self.view_menu = self.menu_bar.addMenu("视图")
        self.theme_menu = self.view_menu.addMenu("主题")
        self.theme_menu.addAction("浅色")
        self.theme_menu.addAction("深色")
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.menu_bar.controller.close(restore_focus=False)
        self.window.close()
        self.application.processEvents()

    def test_open_menu_uses_child_panels_without_native_popup_windows(self) -> None:
        button = self.menu_bar._buttons[self.file_menu]
        button.click()
        self.application.processEvents()

        panels = self.menu_bar.controller._panels
        self.assertEqual(1, len(panels))
        panel = panels[0]
        self.assertIs(self.window, panel.parentWidget())
        self.assertFalse(panel.isWindow())
        self.assertTrue(panel.isVisible())
        self.assertTrue(all(row.property("keyboardFocus") is False for row in panel.rows))
        self.assertGreaterEqual(
            panel.y(),
            button.mapTo(self.window, QPoint(0, button.height())).y() + MENU_PANEL_GAP,
        )

    def test_action_activation_reuses_qaction_and_closes_overlay(self) -> None:
        triggered: list[bool] = []
        self.save_action.triggered.connect(lambda: triggered.append(True))
        self.menu_bar._buttons[self.file_menu].click()
        self.application.processEvents()
        row = self.menu_bar.controller._panels[0].rows[0]

        row.activated.emit(self.save_action)
        self.application.processEvents()

        self.assertEqual([True], triggered)
        self.assertFalse(self.menu_bar.controller.is_open)

    def test_submenu_is_positioned_beside_parent_inside_window(self) -> None:
        self.menu_bar._buttons[self.view_menu].click()
        self.application.processEvents()
        controller = self.menu_bar.controller
        root_panel = controller._panels[0]
        theme_row = next(row for row in root_panel.rows if row.action.menu() is self.theme_menu)

        theme_row.activated.emit(theme_row.action)
        self.application.processEvents()

        self.assertEqual(2, len(controller._panels))
        submenu_panel = controller._panels[1]
        self.assertFalse(submenu_panel.geometry().intersects(root_panel.geometry()))
        self.assertGreaterEqual(submenu_panel.x(), 0)
        self.assertLessEqual(submenu_panel.geometry().right(), self.window.rect().right())

    def test_escape_closes_menu_and_restores_focus(self) -> None:
        focus_target = self.window.centralWidget()
        assert focus_target is not None
        focus_target.setFocus()
        self.menu_bar._buttons[self.file_menu].click()
        self.application.processEvents()

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        handled = self.menu_bar.controller.eventFilter(self.application, event)
        self.application.processEvents()

        self.assertTrue(handled)
        self.assertFalse(self.menu_bar.controller.is_open)
        self.assertIs(focus_target, QApplication.focusWidget())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QVBoxLayout, QWidget
from PySide6.QtTest import QTest

from src.gui.views.animated_drawer import (
    DRAWER_ANIMATION_DURATION_MS,
    DRAWER_HANDLE_HOVER_WIDTH,
    DRAWER_HANDLE_IDLE_WIDTH,
    AnimatedSplitterDrawer,
    DrawerHandleButton,
)


class AnimatedSplitterDrawerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_button_collapses_and_restores_drawer_without_dragging_handle(self) -> None:
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())
        splitter.resize(800, 400)
        splitter.setSizes((280, 520))
        button = DrawerHandleButton()
        handle = splitter.handle(1)
        handle_layout = QVBoxLayout(handle)
        handle_layout.setContentsMargins(0, 0, 0, 0)
        handle_layout.addWidget(button)
        drawer = AnimatedSplitterDrawer(splitter, button)
        splitter.show()
        QApplication.processEvents()

        QTest.mouseClick(
            button,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(button.width() // 2, 4),
        )
        QTest.qWait(DRAWER_ANIMATION_DURATION_MS + 40)
        self.assertFalse(drawer.is_expanded)
        self.assertEqual(0, splitter.sizes()[0])
        self.assertFalse(button.icon().isNull())
        self.assertEqual("展开动作库", button.accessibleName())

        QTest.mouseClick(
            button,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(button.width() // 2, button.height() - 4),
        )
        QTest.qWait(DRAWER_ANIMATION_DURATION_MS + 40)
        self.assertTrue(drawer.is_expanded)
        self.assertGreaterEqual(splitter.sizes()[0], 220)
        self.assertFalse(button.icon().isNull())
        self.assertEqual("收起动作库", button.accessibleName())
        self.assertEqual(handle.size(), button.size())

        splitter.close()

    def test_handle_expands_on_hover_and_drag_resizes_the_drawer(self) -> None:
        splitter = QSplitter()
        splitter.addWidget(QWidget())
        splitter.addWidget(QWidget())
        splitter.resize(800, 400)
        splitter.setSizes((280, 520))
        button = DrawerHandleButton()
        handle = splitter.handle(1)
        handle_layout = QVBoxLayout(handle)
        handle_layout.setContentsMargins(0, 0, 0, 0)
        handle_layout.addWidget(button)
        drawer = AnimatedSplitterDrawer(splitter, button)
        splitter.show()
        QApplication.processEvents()

        self.assertEqual(DRAWER_HANDLE_IDLE_WIDTH, splitter.handleWidth())
        QTest.mouseMove(button, QPoint(button.width() // 2, button.height() // 2))
        QApplication.processEvents()
        self.assertEqual(DRAWER_HANDLE_HOVER_WIDTH, splitter.handleWidth())
        initial_width = splitter.sizes()[0]
        center = QPoint(button.width() // 2, button.height() // 2)
        QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(button, center + QPoint(80, 0), delay=20)
        QTest.mouseRelease(
            button,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(80, 0),
        )
        QApplication.processEvents()

        self.assertGreater(splitter.sizes()[0], initial_width)
        self.assertTrue(drawer.is_expanded)
        dragged_width = splitter.sizes()[0]
        drawer.set_expanded(False, animated=False)
        drawer.set_expanded(True, animated=False)
        self.assertEqual(dragged_width, splitter.sizes()[0])

        QTest.mouseMove(splitter.widget(1), QPoint(40, 40))
        QApplication.processEvents()
        self.assertEqual(DRAWER_HANDLE_IDLE_WIDTH, splitter.handleWidth())
        splitter.close()


if __name__ == "__main__":
    unittest.main()

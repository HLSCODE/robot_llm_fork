from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QMainWindow

from src.gui.window_chrome import (
    FramelessResizeController,
    _resize_edges,
)


class FramelessResizeGeometryTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_corner_and_edge_hit_regions_are_detected(self) -> None:
        frame = QRect(100, 100, 800, 600)

        self.assertEqual(
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            _resize_edges(frame, QPoint(102, 102)),
        )
        self.assertEqual(
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            _resize_edges(frame, QPoint(898, 698)),
        )
        self.assertEqual(
            Qt.Edge.LeftEdge,
            _resize_edges(frame, QPoint(101, 400)),
        )

    def test_center_is_not_a_resize_target(self) -> None:
        self.assertEqual(
            Qt.Edge(0),
            _resize_edges(QRect(100, 100, 800, 600), QPoint(500, 400)),
        )

    def test_cursor_override_is_owned_and_cleared_without_child_reference(self) -> None:
        window = QMainWindow()
        controller = FramelessResizeController(window)

        controller._set_cursor(Qt.CursorShape.SizeHorCursor)
        self.assertIsNotNone(QApplication.overrideCursor())
        controller._set_cursor(Qt.CursorShape.SizeVerCursor)
        self.assertEqual(
            Qt.CursorShape.SizeVerCursor,
            QApplication.overrideCursor().shape(),
        )

        window.close()
        QApplication.processEvents()
        self.assertIsNone(QApplication.overrideCursor())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF
from PySide6.QtGui import QHelpEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from src.gui.theme import ThemeController, ThemeMode
from src.gui.tooltips import (
    TOOLTIP_HORIZONTAL_PADDING,
    TOOLTIP_MAXIMUM_TEXT_WIDTH,
    install_tooltip_service,
)


class GuiToolTipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        ThemeController(self.application, ThemeMode.LIGHT)
        self.service = install_tooltip_service(self.application)
        self.service.hide()
        self.widgets: list[QWidget] = []

    def tearDown(self) -> None:
        self.service.hide()
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        QApplication.processEvents()

    def test_widget_list_item_and_graphics_item_share_one_bubble(self) -> None:
        button = QPushButton("按钮")
        button.setToolTip("普通控件提示")
        self._show_widget(button)
        self._send_tooltip_event(button, QPoint(5, 5))
        self.assertEqual("普通控件提示", self.service.bubble.text)

        item_list = QListWidget()
        item = QListWidgetItem("动作")
        item.setToolTip("动作名称\n类型：MOVE_TO_POINT")
        item_list.addItem(item)
        self._show_widget(item_list)
        item_position = item_list.visualItemRect(item).center()
        self._send_tooltip_event(item_list.viewport(), item_position)
        self.assertEqual(
            "动作名称\n类型：MOVE_TO_POINT",
            self.service.bubble.text,
        )

        scene = QGraphicsScene()
        graphics_item = QGraphicsRectItem(QRectF(0, 0, 80, 40))
        graphics_item.setToolTip("在此处插入动作")
        scene.addItem(graphics_item)
        graphics_view = QGraphicsView(scene)
        self._show_widget(graphics_view)
        graphics_position = graphics_view.mapFromScene(QPointF(20, 20))
        self._send_tooltip_event(graphics_view.viewport(), graphics_position)
        self.assertEqual("在此处插入动作", self.service.bubble.text)

    def test_long_tooltip_wraps_and_uses_an_opaque_theme_surface(self) -> None:
        button = QPushButton("按钮")
        button.setToolTip("参数：" + "很长的参数值" * 20)
        self._show_widget(button)
        self._send_tooltip_event(button, QPoint(5, 5))

        bubble = self.service.bubble
        self.assertLessEqual(
            bubble.width(),
            TOOLTIP_MAXIMUM_TEXT_WIDTH + 2 * TOOLTIP_HORIZONTAL_PADDING,
        )
        rendered = bubble.grab().toImage()
        background = rendered.pixelColor(bubble.width() - 5, bubble.height() // 2)
        expected = bubble.palette().color(QPalette.ColorRole.ToolTipBase)
        self.assertEqual(255, background.alpha())
        self.assertEqual(expected.name(), background.name())

    def test_empty_tooltip_event_is_consumed_without_native_fallback(self) -> None:
        button = QPushButton("无提示控件")
        self._show_widget(button)
        event = QHelpEvent(
            QEvent.Type.ToolTip,
            QPoint(5, 5),
            button.mapToGlobal(QPoint(5, 5)),
        )

        self.assertTrue(self.service.eventFilter(button, event))
        self.assertTrue(event.isAccepted())
        self.assertFalse(self.service.bubble.isVisible())

    def test_hiding_a_tooltip_after_its_owner_is_deleted_is_safe(self) -> None:
        button = QPushButton("即将关闭")
        self._show_widget(button)
        self.service.show_text(
            "窗口关闭提示",
            button.mapToGlobal(QPoint(5, 5)),
            owner=button,
        )

        button.close()
        button.deleteLater()
        QApplication.processEvents()
        self.service.hide()
        QApplication.processEvents()

        self.assertFalse(self.service.bubble.isVisible())

    def test_tooltip_lookup_supports_non_viewport_children(self) -> None:
        scene = QGraphicsScene()
        scene.addItem(QGraphicsRectItem(QRectF(0, 0, 80, 40)))
        view = QGraphicsView(scene)
        self._show_widget(view)
        scroll_bar = view.verticalScrollBar()
        event = QHelpEvent(
            QEvent.Type.ToolTip,
            QPoint(2, 2),
            scroll_bar.mapToGlobal(QPoint(2, 2)),
        )

        self.assertTrue(self.service.eventFilter(scroll_bar, event))

    def _show_widget(self, widget: QWidget) -> None:
        self.widgets.append(widget)
        widget.resize(240, 160)
        widget.show()
        QApplication.processEvents()

    @staticmethod
    def _send_tooltip_event(widget: QWidget, position: QPoint) -> None:
        event = QHelpEvent(
            QEvent.Type.ToolTip,
            position,
            widget.mapToGlobal(position),
        )
        QApplication.sendEvent(widget, event)
        QApplication.processEvents()

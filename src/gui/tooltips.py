"""Application-wide themed tooltip presentation."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QHelpEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGraphicsView,
    QToolTip,
    QWidget,
)
from shiboken6 import isValid


TOOLTIP_SERVICE_OBJECT_NAME = "unifiedToolTipService"
TOOLTIP_BUBBLE_OBJECT_NAME = "unifiedToolTipBubble"
TOOLTIP_HORIZONTAL_OFFSET = 12
TOOLTIP_VERTICAL_OFFSET = 18
TOOLTIP_HORIZONTAL_PADDING = 10
TOOLTIP_VERTICAL_PADDING = 7
TOOLTIP_MAXIMUM_TEXT_WIDTH = 360
TOOLTIP_CORNER_RADIUS = 6.0

_APPLICATION_TOOLTIP_SERVICES: dict[int, ToolTipService] = {}

_WidgetType = TypeVar("_WidgetType", bound=QWidget)


@runtime_checkable
class _PositionToolTipItem(Protocol):
    def mapFromScene(self, position: QPointF) -> QPointF:  # noqa: N802
        """Map a scene point into this item's local coordinate system."""

    def tooltip_at(self, position: QPointF) -> str:
        """Return tooltip text for an item-local scene position."""


class ToolTipBubble(QWidget):
    """One compact tooltip surface shared by every GUI entry point."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName(TOOLTIP_BUBBLE_OBJECT_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text = ""
        self.hide()

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.resize(self.sizeHint())
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        text_bounds = self._measure_text()
        return QSize(
            text_bounds.width() + 2 * TOOLTIP_HORIZONTAL_PADDING,
            text_bounds.height() + 2 * TOOLTIP_VERTICAL_PADDING,
        )

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        background = palette.color(QPalette.ColorRole.ToolTipBase)
        background.setAlpha(255)
        border = palette.color(QPalette.ColorRole.Mid)
        border.setAlpha(180)
        surface = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(
            surface,
            TOOLTIP_CORNER_RADIUS,
            TOOLTIP_CORNER_RADIUS,
        )

        painter.setPen(palette.color(QPalette.ColorRole.ToolTipText))
        painter.drawText(
            self.rect().adjusted(
                TOOLTIP_HORIZONTAL_PADDING,
                TOOLTIP_VERTICAL_PADDING,
                -TOOLTIP_HORIZONTAL_PADDING,
                -TOOLTIP_VERTICAL_PADDING,
            ),
            _text_flags(),
            self._text,
        )

    def _measure_text(self) -> QRect:
        lines = self._text.splitlines() or [""]
        natural_width = max(
            self.fontMetrics().horizontalAdvance(line)
            for line in lines
        )
        available_width = max(
            1,
            min(natural_width, TOOLTIP_MAXIMUM_TEXT_WIDTH),
        )
        return self.fontMetrics().boundingRect(
            QRect(0, 0, available_width, 10_000),
            _text_flags(),
            self._text,
        )


class ToolTipService(QObject):
    """Resolve Qt tooltip events and present them through one themed bubble."""

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self.setObjectName(TOOLTIP_SERVICE_OBJECT_NAME)
        self._application = application
        self._bubble = ToolTipBubble()
        self._owner: QWidget | None = None
        self._is_closed = False
        application.installEventFilter(self)
        application.aboutToQuit.connect(self.close)

    @property
    def bubble(self) -> ToolTipBubble:
        return self._bubble

    def show_text(
        self,
        text: str,
        global_position: QPoint,
        *,
        owner: QWidget | None = None,
    ) -> None:
        if self._is_closed:
            return
        normalized_text = text.strip()
        if not normalized_text:
            self.hide()
            return
        QToolTip.hideText()
        self._owner = owner
        self._bubble.set_text(normalized_text)
        self._bubble.move(self._bounded_position(global_position))
        self._bubble.show()
        self._bubble.raise_()
        _schedule_widget_repaint(owner)

    def hide(self) -> None:
        if self._is_closed:
            return
        owner = self._owner
        self._owner = None
        self._bubble.hide()
        _schedule_widget_repaint(owner)

    def close(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        self._application.removeEventFilter(self)
        self._owner = None
        if isValid(self._bubble):
            self._bubble.close()
            self._bubble.deleteLater()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._is_closed:
            return False
        if event.type() is QEvent.Type.ToolTip:
            return self._show_event_tooltip(watched, event)
        if event.type() is QEvent.Type.Leave and watched is self._owner:
            self.hide()
        elif event.type() in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
            QEvent.Type.WindowDeactivate,
        }:
            self.hide()
        return super().eventFilter(watched, event)

    def _show_event_tooltip(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget) or not isinstance(event, QHelpEvent):
            return False
        text = _tooltip_text_at(watched, event.pos())
        if not text:
            self.hide()
            _schedule_widget_repaint(watched)
            event.accept()
            return True
        self.show_text(text, event.globalPos(), owner=watched)
        event.accept()
        return True

    def _bounded_position(self, anchor: QPoint) -> QPoint:
        position = anchor + QPoint(
            TOOLTIP_HORIZONTAL_OFFSET,
            TOOLTIP_VERTICAL_OFFSET,
        )
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        if screen is None:
            return position
        available = screen.availableGeometry()
        width = self._bubble.width()
        height = self._bubble.height()
        if position.x() + width > available.right():
            position.setX(anchor.x() - width - TOOLTIP_HORIZONTAL_OFFSET)
        if position.y() + height > available.bottom():
            position.setY(anchor.y() - height - TOOLTIP_VERTICAL_OFFSET)
        position.setX(max(available.left(), position.x()))
        position.setY(max(available.top(), position.y()))
        return position


def install_tooltip_service(application: QApplication) -> ToolTipService:
    """Install one process-wide tooltip service and return its owner."""
    for child in application.children():
        if (
            isinstance(child, ToolTipService)
            and child.objectName() == TOOLTIP_SERVICE_OBJECT_NAME
        ):
            _APPLICATION_TOOLTIP_SERVICES[id(application)] = child
            return child
    service = ToolTipService(application)
    _APPLICATION_TOOLTIP_SERVICES[id(application)] = service
    return service


def _tooltip_text_at(widget: QWidget, position: QPoint) -> str:
    global_position = widget.mapToGlobal(position)
    graphics_view = _ancestor_of_type(widget, QGraphicsView)
    if graphics_view is not None:
        viewport_position = graphics_view.viewport().mapFromGlobal(global_position)
        scene_position = graphics_view.mapToScene(viewport_position)
        item = graphics_view.itemAt(viewport_position)
        while item is not None:
            if isinstance(item, _PositionToolTipItem):
                text = item.tooltip_at(item.mapFromScene(scene_position))
                if text:
                    return text
            if item.toolTip():
                return item.toolTip()
            item = item.parentItem()

    item_view = _ancestor_of_type(widget, QAbstractItemView)
    if item_view is not None:
        viewport_position = item_view.viewport().mapFromGlobal(global_position)
        index = item_view.indexAt(viewport_position)
        if index.isValid():
            value = index.data(Qt.ItemDataRole.ToolTipRole)
            if isinstance(value, str):
                return value

    return widget.toolTip()


def _ancestor_of_type(
    widget: QWidget,
    expected_type: type[_WidgetType],
) -> _WidgetType | None:
    current: QWidget | None = widget
    while current is not None:
        if isinstance(current, expected_type):
            return current
        current = current.parentWidget()
    return None


def _text_flags() -> int:
    return int(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
    ) | int(
        Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
    )


def _schedule_widget_repaint(widget: QWidget | None) -> None:
    if widget is None or not isValid(widget):
        return
    widget.update()
    QTimer.singleShot(0, lambda: _update_if_valid(widget))


def _update_if_valid(widget: QWidget) -> None:
    """Refresh only if Qt has not destroyed the Python-owned widget meanwhile."""
    if isValid(widget):
        widget.update()

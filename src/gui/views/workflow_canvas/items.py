"""Lightweight custom QGraphics items for the constrained workflow canvas."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ....domain.models import LoopBlock, SequenceEntry, SequenceItem
from .tokens import (
    ACTION_COLORS,
    INSERT_TARGET_SIZE,
    LOOP_CHILD_HEIGHT,
    LOOP_HEADER_HEIGHT,
    MAX_VISIBLE_LOOP_CHILDREN,
    NODE_HEIGHT,
    NODE_WIDTH,
    STATUS_COLORS,
    STATUS_LABELS,
    NODE_RADIUS,
    canvas_colors,
    canvas_font,
    contrasting_text,
)


class WorkflowNodeItem(QGraphicsObject):
    move_requested = Signal(str, float)
    focused = Signal(str, str)
    edit_requested = Signal(str, str)

    def __init__(
        self,
        node_id: str,
        entry: SequenceEntry,
    ) -> None:
        super().__init__()
        self.node_id = node_id
        self.entry = entry
        self.setFlags(
            self.GraphicsItemFlag.ItemIsSelectable
            | self.GraphicsItemFlag.ItemIsMovable
            | self.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setCacheMode(self.CacheMode.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())

    @property
    def node_height(self) -> float:
        if not isinstance(self.entry, LoopBlock):
            return NODE_HEIGHT
        visible_children = min(
            len(self.entry.items),
            MAX_VISIBLE_LOOP_CHILDREN,
        )
        return LOOP_HEADER_HEIGHT + visible_children * LOOP_CHILD_HEIGHT + 12.0

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0.0, 0.0, NODE_WIDTH, self.node_height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = canvas_colors(QApplication.palette())
        is_emphasized = self.isSelected() or self.isUnderMouse()
        border = colors.accent if is_emphasized else colors.border
        painter.setBrush(QBrush(colors.surface))
        painter.setPen(QPen(border, 3.0 if self.isSelected() else 1.5))
        painter.drawRoundedRect(self.boundingRect(), NODE_RADIUS, NODE_RADIUS)

        if isinstance(self.entry, LoopBlock):
            self._paint_loop(painter, colors.text, colors.secondary_text)
        else:
            self._paint_action(
                painter,
                self.entry,
                colors.text,
                colors.secondary_text,
            )

    def itemChange(  # noqa: N802
        self,
        change: QGraphicsItem.GraphicsItemChange,
        value: object,
    ) -> object:
        if change is self.GraphicsItemChange.ItemPositionChange:
            position = value
            if isinstance(position, QPointF):
                return QPointF(0.0, position.y())
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        item_uuid = self._item_uuid_at(event.pos().y())
        self.focused.emit(self.node_id, item_uuid)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.move_requested.emit(self.node_id, self.scenePos().y())

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        item_uuid = self._item_uuid_at(event.pos().y())
        self.edit_requested.emit(self.node_id, item_uuid)
        event.accept()

    def _paint_action(
        self,
        painter: QPainter,
        item: SequenceItem,
        foreground: QColor,
        secondary_text: QColor,
    ) -> None:
        color = ACTION_COLORS.get(item.definition.type, QColor("#64748b"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(0.0, 0.0, 10.0, NODE_HEIGHT), 5.0, 5.0)
        painter.setPen(foreground)
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(24.0, 12.0, NODE_WIDTH - 42.0, 28.0),
            Qt.AlignmentFlag.AlignVCenter,
            item.definition.name,
        )
        painter.setFont(canvas_font(secondary=True))
        painter.setPen(secondary_text)
        painter.drawText(
            QRectF(24.0, 42.0, NODE_WIDTH - 42.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter,
            item.definition.type.value,
        )
        status_color = STATUS_COLORS[item.status]
        painter.setBrush(status_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            QRectF(NODE_WIDTH - 86.0, 58.0, 70.0, 22.0),
            10.0,
            10.0,
        )
        painter.setPen(contrasting_text(status_color))
        painter.drawText(
            QRectF(NODE_WIDTH - 86.0, 58.0, 70.0, 22.0),
            Qt.AlignmentFlag.AlignCenter,
            STATUS_LABELS[item.status],
        )

    def _paint_loop(
        self,
        painter: QPainter,
        foreground: QColor,
        secondary_text: QColor,
    ) -> None:
        loop = self.entry
        if not isinstance(loop, LoopBlock):
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8b5cf6"))
        painter.drawRoundedRect(
            QRectF(0.0, 0.0, NODE_WIDTH, LOOP_HEADER_HEIGHT),
            12.0,
            12.0,
        )
        painter.setPen(QColor("#ffffff"))
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(18.0, 10.0, NODE_WIDTH - 36.0, 26.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"循环 ×{loop.repeat_count}",
        )
        detail_font = canvas_font(secondary=True)
        painter.setFont(detail_font)
        progress = (
            f" · 第 {loop.current_iteration}/{loop.repeat_count} 轮"
            if loop.current_iteration
            else ""
        )
        painter.drawText(
            QRectF(18.0, 38.0, NODE_WIDTH - 36.0, 24.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"{len(loop.items)} 个动作 · {loop.total_steps} 步{progress}",
        )

        painter.setFont(detail_font)
        for index, child in enumerate(loop.items[:MAX_VISIBLE_LOOP_CHILDREN]):
            top = LOOP_HEADER_HEIGHT + index * LOOP_CHILD_HEIGHT
            painter.setPen(QPen(canvas_colors().border, 1.0))
            painter.drawLine(
                QPointF(16.0, top),
                QPointF(NODE_WIDTH - 16.0, top),
            )
            painter.setPen(foreground)
            painter.drawText(
                QRectF(20.0, top, NODE_WIDTH - 110.0, LOOP_CHILD_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                f"{index + 1}. {child.definition.name}",
            )
            painter.setPen(STATUS_COLORS[child.status])
            painter.drawText(
                QRectF(NODE_WIDTH - 88.0, top, 68.0, LOOP_CHILD_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                STATUS_LABELS[child.status],
            )
        if len(loop.items) > MAX_VISIBLE_LOOP_CHILDREN:
            painter.setPen(secondary_text)
            painter.drawText(
                QRectF(
                    20.0,
                    self.node_height - LOOP_CHILD_HEIGHT,
                    NODE_WIDTH - 40.0,
                    LOOP_CHILD_HEIGHT,
                ),
                Qt.AlignmentFlag.AlignCenter,
                f"另有 {len(loop.items) - MAX_VISIBLE_LOOP_CHILDREN} 个动作",
            )

    def _item_uuid_at(self, local_y: float) -> str:
        if not isinstance(self.entry, LoopBlock):
            return self.entry.uuid
        child_index = int((local_y - LOOP_HEADER_HEIGHT) // LOOP_CHILD_HEIGHT)
        if 0 <= child_index < min(
            len(self.entry.items),
            MAX_VISIBLE_LOOP_CHILDREN,
        ):
            return self.entry.items[child_index].uuid
        return self.entry.uuid

    def _tooltip(self) -> str:
        if isinstance(self.entry, LoopBlock):
            return (
                f"循环 {self.entry.repeat_count} 次\n"
                f"{len(self.entry.items)} 个动作，共 {self.entry.total_steps} 步"
            )
        parameters = ", ".join(
            f"{name}={value}"
            for name, value in list(self.entry.definition.parameters.items())[:5]
        )
        return f"{self.entry.definition.name}\n{parameters or '无参数'}"


class StartEndItem(QGraphicsObject):
    def __init__(self, label: str, color: QColor) -> None:
        super().__init__()
        self._label = label
        self._color = color
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0.0, 0.0, 120.0, 42.0)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.boundingRect(), 20.0, 20.0)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, self._label)


class InsertionItem(QGraphicsObject):
    insert_requested = Signal(int)

    def __init__(self, index: int) -> None:
        super().__init__()
        self._index = index
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("在此处插入动作")

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0.0, 0.0, INSERT_TARGET_SIZE, INSERT_TARGET_SIZE)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(canvas_colors().accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.boundingRect().adjusted(7.0, 7.0, -7.0, -7.0))
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        center = self.boundingRect().center()
        painter.drawLine(center.x() - 6.0, center.y(), center.x() + 6.0, center.y())
        painter.drawLine(center.x(), center.y() - 6.0, center.x(), center.y() + 6.0)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.insert_requested.emit(self._index)
        event.accept()

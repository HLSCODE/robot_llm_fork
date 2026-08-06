"""Lightweight custom QGraphics items for the constrained workflow canvas."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
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
    LOOP_CHILD_GAP,
    LOOP_FOOTER_HEIGHT,
    LOOP_FOOTER_WIDTH,
    LOOP_HEADER_HEIGHT,
    LOOP_NODE_WIDTH,
    LOOP_SECTION_GAP,
    MAX_VISIBLE_LOOP_CHILDREN,
    NODE_DRAG_THRESHOLD,
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
    focused = Signal(str, str, bool)
    edit_requested = Signal(str, str)
    loop_insert_requested = Signal(str, int)
    move_requested = Signal(str, float)

    def __init__(
        self,
        node_id: str,
        entry: SequenceEntry,
        *,
        editing_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.node_id = node_id
        self.entry = entry
        self._editing_enabled = editing_enabled
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCacheMode(self.CacheMode.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())
        self._pressed_loop_insert_index: int | None = None
        self._press_scene_y: float | None = None
        self._drag_origin_y = 0.0
        self._is_dragging = False

    @property
    def node_height(self) -> float:
        if not isinstance(self.entry, LoopBlock):
            return NODE_HEIGHT
        visible_children = min(
            len(self.entry.items),
            MAX_VISIBLE_LOOP_CHILDREN,
        )
        children_height = visible_children * LOOP_CHILD_HEIGHT
        children_gaps = max(0, visible_children - 1) * LOOP_CHILD_GAP
        empty_body_height = 44.0 if visible_children == 0 else 0.0
        return (
            LOOP_HEADER_HEIGHT
            + LOOP_SECTION_GAP
            + children_height
            + children_gaps
            + empty_body_height
            + LOOP_SECTION_GAP
            + LOOP_FOOTER_HEIGHT
        )

    @property
    def node_width(self) -> float:
        return LOOP_NODE_WIDTH if isinstance(self.entry, LoopBlock) else NODE_WIDTH

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0.0, 0.0, self.node_width, self.node_height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = canvas_colors(QApplication.palette())
        if isinstance(self.entry, LoopBlock):
            self._paint_loop(
                painter,
                colors.text,
                colors.secondary_text,
                colors.accent,
            )
            return
        is_emphasized = self.isSelected() or self.isUnderMouse()
        self._paint_action(
            painter,
            self.entry,
            colors.text,
            colors.secondary_text,
            emphasized=is_emphasized,
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        loop_insert_index = self._loop_insertion_index_at(event.pos())
        if loop_insert_index is not None:
            self._pressed_loop_insert_index = loop_insert_index
            event.accept()
            return
        item_uuid = self._item_uuid_at(event.pos().y())
        additive = bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        self.focused.emit(self.node_id, item_uuid, additive)
        if self._editing_enabled and not additive:
            self._press_scene_y = event.scenePos().y()
            self._drag_origin_y = self.scenePos().y()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if (
            self._press_scene_y is None
            or not self._editing_enabled
            or not event.buttons() & Qt.MouseButton.LeftButton
            or event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            event.accept()
            return
        delta_y = event.scenePos().y() - self._press_scene_y
        if not self._is_dragging and abs(delta_y) < NODE_DRAG_THRESHOLD:
            event.accept()
            return
        self._is_dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.setOpacity(0.88)
        self.setZValue(20.0)
        self.setY(self._drag_origin_y + delta_y)
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._pressed_loop_insert_index is not None:
            insert_index = self._pressed_loop_insert_index
            self._pressed_loop_insert_index = None
            if self._loop_insertion_index_at(event.pos()) == insert_index:
                self.loop_insert_requested.emit(self.node_id, insert_index)
            event.accept()
            return
        was_dragging = self._is_dragging
        target_center_y = self.scenePos().y() + self.node_height / 2.0
        self._reset_drag_state()
        if was_dragging:
            self.move_requested.emit(self.node_id, target_center_y)
        event.accept()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self._reset_drag_state()
        item_uuid = self._item_uuid_at(event.pos().y())
        self.edit_requested.emit(self.node_id, item_uuid)
        event.accept()

    def _reset_drag_state(self) -> None:
        self._press_scene_y = None
        self._is_dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setOpacity(1.0)
        self.setZValue(0.0)

    def _paint_action(
        self,
        painter: QPainter,
        item: SequenceItem,
        foreground: QColor,
        secondary_text: QColor,
        *,
        emphasized: bool = False,
    ) -> None:
        self._paint_action_card(
            painter,
            item,
            QRectF(0.0, 0.0, NODE_WIDTH, NODE_HEIGHT),
            foreground,
            secondary_text,
            emphasized=emphasized,
        )

    def _paint_action_card(
        self,
        painter: QPainter,
        item: SequenceItem,
        rect: QRectF,
        foreground: QColor,
        secondary_text: QColor,
        *,
        emphasized: bool = False,
    ) -> None:
        colors = canvas_colors()
        painter.setBrush(QBrush(colors.surface))
        painter.setPen(QPen(colors.accent if emphasized else colors.border, 2.5 if emphasized else 1.5))
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        color = ACTION_COLORS.get(item.definition.type, QColor("#64748b"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(rect.left(), rect.top(), 10.0, rect.height()),
            5.0,
            5.0,
        )
        painter.setPen(foreground)
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(rect.left() + 24.0, rect.top() + 10.0, rect.width() - 42.0, 28.0),
            Qt.AlignmentFlag.AlignVCenter,
            item.definition.name,
        )
        painter.setFont(canvas_font(secondary=True))
        painter.setPen(secondary_text)
        painter.drawText(
            QRectF(rect.left() + 24.0, rect.top() + 38.0, rect.width() - 42.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter,
            item.definition.type.value,
        )
        status_color = STATUS_COLORS[item.status]
        painter.setBrush(status_color)
        painter.setPen(Qt.PenStyle.NoPen)
        status_rect = QRectF(
            rect.right() - 86.0,
            rect.bottom() - 28.0,
            70.0,
            22.0,
        )
        painter.drawRoundedRect(
            status_rect,
            10.0,
            10.0,
        )
        painter.setPen(contrasting_text(status_color))
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignCenter,
            STATUS_LABELS[item.status],
        )

    def _paint_loop(
        self,
        painter: QPainter,
        foreground: QColor,
        secondary_text: QColor,
        accent: QColor,
    ) -> None:
        loop = self.entry
        if not isinstance(loop, LoopBlock):
            return
        card_left = (LOOP_NODE_WIDTH - NODE_WIDTH) / 2.0
        header_rect = QRectF(
            card_left,
            0.0,
            NODE_WIDTH,
            LOOP_HEADER_HEIGHT,
        )
        loop_color = QColor("#7c5a16")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(loop_color)
        painter.setPen(QPen(accent if self.isSelected() else loop_color, 3.0))
        painter.drawRoundedRect(header_rect, NODE_RADIUS, NODE_RADIUS)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(card_left + 18.0, 10.0, NODE_WIDTH - 36.0, 26.0),
            Qt.AlignmentFlag.AlignVCenter,
            "循环",
        )
        detail_font = canvas_font(secondary=True)
        painter.setFont(detail_font)
        progress = (
            f" · 第 {loop.current_iteration}/{loop.repeat_count} 轮"
            if loop.current_iteration
            else ""
        )
        painter.drawText(
            QRectF(card_left + 18.0, 38.0, NODE_WIDTH - 36.0, 24.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"循环 {loop.repeat_count} 次 · {len(loop.items)} 个动作{progress}",
        )

        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        painter.setPen(QColor("#fbbf24"))
        painter.drawText(
            QRectF(
                LOOP_NODE_WIDTH / 2.0 + 24.0,
                LOOP_HEADER_HEIGHT + 6.0,
                100.0,
                LOOP_SECTION_GAP - 12.0,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "循环体",
        )
        visible_children = loop.items[:MAX_VISIBLE_LOOP_CHILDREN]
        if self._editing_enabled:
            self._paint_insert_marker(
                painter,
                body_top - LOOP_SECTION_GAP / 2.0,
                accent,
            )
        for index, child in enumerate(visible_children):
            child_top = body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            if self._editing_enabled and index > 0:
                self._paint_insert_marker(
                    painter,
                    child_top - LOOP_CHILD_GAP / 2.0,
                    accent,
                )
            child_rect = QRectF(
                card_left,
                child_top,
                NODE_WIDTH,
                LOOP_CHILD_HEIGHT,
            )
            self._paint_action_card(
                painter,
                child,
                child_rect,
                foreground,
                secondary_text,
            )

        if visible_children:
            children_bottom = (
                body_top
                + len(visible_children) * LOOP_CHILD_HEIGHT
                + max(0, len(visible_children) - 1) * LOOP_CHILD_GAP
            )
            if (
                self._editing_enabled
                and len(loop.items) <= MAX_VISIBLE_LOOP_CHILDREN
            ):
                self._paint_insert_marker(
                    painter,
                    children_bottom + LOOP_SECTION_GAP / 2.0,
                    accent,
                )
        else:
            children_bottom = body_top + 44.0
            painter.setPen(secondary_text)
            painter.drawText(
                QRectF(card_left, body_top, NODE_WIDTH, 44.0),
                Qt.AlignmentFlag.AlignCenter,
                "循环体为空",
            )
        footer_top = children_bottom + LOOP_SECTION_GAP
        footer_left = (LOOP_NODE_WIDTH - LOOP_FOOTER_WIDTH) / 2.0
        footer_rect = QRectF(
            footer_left,
            footer_top,
            LOOP_FOOTER_WIDTH,
            LOOP_FOOTER_HEIGHT,
        )
        painter.setBrush(canvas_colors().border)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(footer_rect, 20.0, 20.0)
        painter.setPen(foreground)
        painter.drawText(
            footer_rect,
            Qt.AlignmentFlag.AlignCenter,
            "循环完成",
        )
        if len(loop.items) > MAX_VISIBLE_LOOP_CHILDREN:
            painter.setPen(secondary_text)
            painter.drawText(
                QRectF(card_left, footer_top - 30.0, NODE_WIDTH, 22.0),
                Qt.AlignmentFlag.AlignCenter,
                f"另有 {len(loop.items) - MAX_VISIBLE_LOOP_CHILDREN} 个动作",
            )
        self._paint_loop_paths(painter, header_rect, footer_rect)

    @staticmethod
    def _paint_insert_marker(
        painter: QPainter,
        center_y: float,
        accent: QColor,
    ) -> None:
        center_x = LOOP_NODE_WIDTH / 2.0
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(center_x, center_y), 15.0, 15.0)
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        painter.drawLine(
            QPointF(center_x - 6.0, center_y),
            QPointF(center_x + 6.0, center_y),
        )
        painter.drawLine(
            QPointF(center_x, center_y - 6.0),
            QPointF(center_x, center_y + 6.0),
        )

    @staticmethod
    def _paint_loop_paths(
        painter: QPainter,
        header_rect: QRectF,
        footer_rect: QRectF,
    ) -> None:
        loop_color = QColor("#fbbf24")
        left_x = 24.0
        right_x = LOOP_NODE_WIDTH - 24.0
        header_center_y = header_rect.center().y()
        footer_center_y = footer_rect.center().y()
        painter.setBrush(Qt.BrushStyle.NoBrush)

        forward_path = QPainterPath()
        forward_path.moveTo(header_rect.right(), header_center_y)
        forward_path.cubicTo(
            right_x,
            header_center_y,
            right_x,
            footer_center_y,
            footer_rect.right(),
            footer_center_y,
        )
        painter.setPen(QPen(loop_color, 2.0))
        painter.drawPath(forward_path)
        painter.drawText(
            QRectF(right_x - 84.0, header_center_y + 18.0, 76.0, 24.0),
            Qt.AlignmentFlag.AlignRight,
            "达到次数",
        )

        return_path = QPainterPath()
        return_path.moveTo(footer_rect.left(), footer_center_y)
        return_path.cubicTo(
            left_x,
            footer_center_y,
            left_x,
            header_center_y,
            header_rect.left(),
            header_center_y,
        )
        return_pen = QPen(loop_color, 2.0, Qt.PenStyle.DashLine)
        painter.setPen(return_pen)
        painter.drawPath(return_path)
        painter.setPen(loop_color)
        painter.drawText(
            QRectF(0.0, header_center_y + 18.0, 68.0, 24.0),
            Qt.AlignmentFlag.AlignRight,
            "下一次",
        )

    def _item_uuid_at(self, local_y: float) -> str:
        if not self._editing_enabled or not isinstance(self.entry, LoopBlock):
            return self.entry.uuid
        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        for index, child in enumerate(
            self.entry.items[:MAX_VISIBLE_LOOP_CHILDREN]
        ):
            child_top = body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            if child_top <= local_y <= child_top + LOOP_CHILD_HEIGHT:
                return child.uuid
        return self.entry.uuid

    def item_uuid_at(self, local_y: float) -> str:
        """Resolve the action represented at a local vertical position."""
        return self._item_uuid_at(local_y)

    def _loop_insertion_index_at(self, position: QPointF) -> int | None:
        if not isinstance(self.entry, LoopBlock):
            return None
        if abs(position.x() - LOOP_NODE_WIDTH / 2.0) > 24.0:
            return None
        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        insertion_centers = [body_top - LOOP_SECTION_GAP / 2.0]
        insertion_centers.extend(
            body_top
            + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            - LOOP_CHILD_GAP / 2.0
            for index in range(1, min(len(self.entry.items), MAX_VISIBLE_LOOP_CHILDREN))
        )
        if 0 < len(self.entry.items) <= MAX_VISIBLE_LOOP_CHILDREN:
            insertion_centers.append(
                body_top
                + len(self.entry.items) * LOOP_CHILD_HEIGHT
                + (len(self.entry.items) - 1) * LOOP_CHILD_GAP
                + LOOP_SECTION_GAP / 2.0
            )
        for index, center_y in enumerate(insertion_centers):
            if abs(position.y() - center_y) <= 20.0:
                return index
        return None

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
        self._is_pressed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
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

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._is_pressed = True
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        should_insert = self._is_pressed and self.boundingRect().contains(
            event.pos()
        )
        self._is_pressed = False
        if should_insert:
            self.insert_requested.emit(self._index)
        event.accept()

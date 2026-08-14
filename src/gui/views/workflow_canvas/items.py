"""Lightweight custom QGraphics items for the constrained workflow canvas."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QEasingCurve,
    QEvent,
    QFile,
    QIODevice,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ....domain.models import (
    LoopBlock,
    ParallelBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)
from ...drag_preview_style import (
    DRAG_CARD_OPACITY,
    DRAG_SOURCE_OPACITY,
    bounded_drag_preview_scale,
)
from ...icons import IconName, action_icon
from .tokens import (
    ACTION_COLORS,
    ControlFlowKind,
    EXECUTION_PULSE_DURATION_MS,
    INSERT_TARGET_SIZE,
    INSERT_TARGET_HINT_WIDTH,
    INSERT_HOVER_TRANSITION_MS,
    INSERT_TARGET_PULSE_DURATION_MS,
    LOOP_CHILD_HEIGHT,
    LOOP_CHILD_GAP,
    LOOP_FOOTER_HEIGHT,
    LOOP_FOOTER_WIDTH,
    LOOP_HEADER_HEIGHT,
    LOOP_NODE_WIDTH,
    LOOP_SECTION_GAP,
    MAX_VISIBLE_LOOP_CHILDREN,
    PARALLEL_BRANCH_GAP,
    PARALLEL_BRANCH_HEADER_HEIGHT,
    PARALLEL_BRANCH_PADDING,
    PARALLEL_BRANCH_WIDTH,
    PARALLEL_CHILD_GAP,
    PARALLEL_CHILD_HEIGHT,
    PARALLEL_FOOTER_HEIGHT,
    PARALLEL_HEADER_HEIGHT,
    PARALLEL_SECTION_GAP,
    NODE_DRAG_THRESHOLD,
    NODE_HEIGHT,
    NODE_WIDTH,
    STATUS_COLORS,
    STATUS_LABELS,
    NODE_RADIUS,
    canvas_colors,
    canvas_font,
    control_flow_colors,
    contrasting_text,
)


_SVG_RENDERERS: dict[tuple[IconName, str], QSvgRenderer] = {}
_INSERT_ACTION_TOOLTIP = "在此处插入动作"


def _draw_svg_icon(
    painter: QPainter,
    icon_name: IconName,
    rect: QRectF,
    color: QColor,
) -> None:
    cache_key = (icon_name, color.name())
    renderer = _SVG_RENDERERS.get(cache_key)
    if renderer is None:
        resource = QFile(f":/icons/{icon_name.value}.svg")
        if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
            return
        try:
            source = bytes(resource.readAll()).replace(
                b"#000000",
                color.name().encode("ascii"),
            )
        finally:
            resource.close()
        renderer = QSvgRenderer(QByteArray(source))
        if not renderer.isValid():
            return
        _SVG_RENDERERS[cache_key] = renderer
    renderer.render(painter, rect)


def _entry_is_running(entry: SequenceEntry) -> bool:
    if isinstance(entry, SequenceItem):
        return entry.status is SequenceItemStatus.RUNNING
    if isinstance(entry, (LoopBlock, SubworkflowBlock)):
        return any(_entry_is_running(child) for child in entry.items)
    return any(
        _entry_is_running(child)
        for branch in entry.branches
        for child in branch.items
    )


def _card_brush(surface: QColor, accent: QColor, emphasized: bool) -> QBrush:
    """Keep selected and hovered cards calm without a thick focus outline."""
    if not emphasized:
        return QBrush(surface)
    # Pre-composite the subtle accent over the card surface. Returning a
    # translucent accent directly lets container-owned connector lines bleed
    # through selected nested cards.
    accent_weight = 24.0 / 255.0
    surface_weight = 1.0 - accent_weight
    highlighted = QColor.fromRgbF(
        surface.redF() * surface_weight + accent.redF() * accent_weight,
        surface.greenF() * surface_weight + accent.greenF() * accent_weight,
        surface.blueF() * surface_weight + accent.blueF() * accent_weight,
        1.0,
    )
    return QBrush(highlighted)


class NodeDragPreviewItem(QGraphicsPixmapItem):
    """Non-interactive thumbnail used while a workflow node is dragged."""

    def __init__(self, node_id: str, pixmap: QPixmap) -> None:
        super().__init__(pixmap)
        self.node_id = node_id
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setOpacity(DRAG_CARD_OPACITY)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setZValue(100.0)


class WorkflowNodeItem(QGraphicsObject):
    focused = Signal(str, str, bool)
    edit_requested = Signal(str, str)
    loop_insert_requested = Signal(str, int)
    parallel_insert_requested = Signal(str, str, int)
    move_requested = Signal(str, float)
    drag_position_changed = Signal(str, float, float)
    drag_ended = Signal(str, bool)
    loop_child_drag_position_changed = Signal(str, str, float, float)
    loop_child_drag_ended = Signal(str, str, bool)
    subworkflow_open_requested = Signal(str)

    def __init__(
        self,
        node_id: str,
        entry: SequenceEntry,
        *,
        editing_enabled: bool = True,
        parallel_branch_states: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.node_id = node_id
        self.entry = entry
        self._editing_enabled = editing_enabled
        self._parallel_branch_states = dict(parallel_branch_states or {})
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCacheMode(self.CacheMode.DeviceCoordinateCache)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._tooltip())
        self._pressed_loop_insert_index: int | None = None
        self._pressed_parallel_insert: tuple[str, int] | None = None
        self._press_scene_y: float | None = None
        self._press_local_position: QPointF | None = None
        self._drag_origin_y = 0.0
        self._drag_target_center_y = 0.0
        self._is_dragging = False
        self._drag_preview: NodeDragPreviewItem | None = None
        self._drag_preview_anchor = QPointF()
        self._dragged_item_uuid = node_id
        self._active_child_uuid = ""
        self._active_loop_drop_index: int | None = None
        self._drop_pulse_phase = 0.0
        self._drop_pulse_animation = QVariantAnimation(self)
        self._drop_pulse_animation.setDuration(INSERT_TARGET_PULSE_DURATION_MS)
        self._drop_pulse_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._drop_pulse_animation.setKeyValueAt(0.0, 0.0)
        self._drop_pulse_animation.setKeyValueAt(0.5, 1.0)
        self._drop_pulse_animation.setKeyValueAt(1.0, 0.0)
        self._drop_pulse_animation.setLoopCount(-1)
        self._drop_pulse_animation.valueChanged.connect(
            self._on_drop_pulse_value_changed
        )
        self._hovered_insert_target: tuple[str, int] | None = None
        self._insert_hover_phase = 0.0
        self._insert_hover_animation = QVariantAnimation(self)
        self._insert_hover_animation.setDuration(INSERT_HOVER_TRANSITION_MS)
        self._insert_hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._insert_hover_animation.valueChanged.connect(
            self._on_insert_hover_value_changed
        )
        self._execution_pulse_phase = 0.0
        self._execution_pulse = QVariantAnimation(self)
        self._execution_pulse.setDuration(EXECUTION_PULSE_DURATION_MS)
        self._execution_pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._execution_pulse.setKeyValueAt(0.0, 0.0)
        self._execution_pulse.setKeyValueAt(0.5, 1.0)
        self._execution_pulse.setKeyValueAt(1.0, 0.0)
        self._execution_pulse.setLoopCount(-1)
        self._execution_pulse.valueChanged.connect(
            self._on_execution_pulse_changed
        )
        if _entry_is_running(entry):
            self._execution_pulse.start()

    @property
    def drag_preview(self) -> NodeDragPreviewItem | None:
        return self._drag_preview

    @property
    def is_dragging(self) -> bool:
        return self._is_dragging

    @property
    def execution_pulse_active(self) -> bool:
        return self._execution_pulse.state() == QAbstractAnimation.State.Running

    @property
    def hovered_insert_target(self) -> tuple[str, int] | None:
        return self._hovered_insert_target

    @property
    def is_loop_drop_pulsing(self) -> bool:
        return self._drop_pulse_animation.state() == QAbstractAnimation.State.Running

    def _on_execution_pulse_changed(self, value: object) -> None:
        if not isinstance(value, (int, float)):
            return
        self._execution_pulse_phase = float(value)
        self.update()

    @property
    def node_height(self) -> float:
        if isinstance(self.entry, ParallelBlock):
            max_children = max(
                (len(branch.items) for branch in self.entry.branches),
                default=0,
            )
            body_height = max(1, max_children) * PARALLEL_CHILD_HEIGHT
            body_height += max(0, max_children - 1) * PARALLEL_CHILD_GAP
            return (
                PARALLEL_HEADER_HEIGHT
                + PARALLEL_SECTION_GAP
                + PARALLEL_BRANCH_HEADER_HEIGHT
                + body_height
                + PARALLEL_SECTION_GAP
                + PARALLEL_FOOTER_HEIGHT
            )
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
        if isinstance(self.entry, ParallelBlock):
            branch_count = max(2, len(self.entry.branches))
            return (
                2 * PARALLEL_BRANCH_PADDING
                + branch_count * PARALLEL_BRANCH_WIDTH
                + (branch_count - 1) * PARALLEL_BRANCH_GAP
            )
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
        if isinstance(self.entry, SubworkflowBlock):
            self._paint_subworkflow(
                painter,
                colors.text,
                colors.secondary_text,
                emphasized=self.isSelected() or self.isUnderMouse(),
            )
            return
        if isinstance(self.entry, ParallelBlock):
            self._paint_parallel(
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
        parallel_insert = self._parallel_insertion_at(event.pos())
        if parallel_insert is not None:
            self._pressed_parallel_insert = parallel_insert
            event.accept()
            return
        item_uuid = self._item_uuid_at(event.pos())
        self._dragged_item_uuid = item_uuid
        self.set_active_child_uuid(item_uuid if item_uuid != self.node_id else "")
        additive = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.focused.emit(self.node_id, item_uuid, additive)
        if self._editing_enabled and not additive:
            self._press_scene_y = event.scenePos().y()
            self._press_local_position = QPointF(event.pos())
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
        if not self._is_dragging:
            self._begin_drag_preview(event.scenePos())
        self._drag_target_center_y = self._drag_origin_y + delta_y + self.node_height / 2.0
        self._position_drag_preview(event.scenePos())
        if self._dragged_item_uuid == self.node_id:
            self.drag_position_changed.emit(
                self.node_id,
                event.scenePos().x(),
                event.scenePos().y(),
            )
        else:
            self.loop_child_drag_position_changed.emit(
                self.node_id,
                self._dragged_item_uuid,
                event.scenePos().x(),
                event.scenePos().y(),
            )
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._pressed_loop_insert_index is not None:
            insert_index = self._pressed_loop_insert_index
            self._pressed_loop_insert_index = None
            if self._loop_insertion_index_at(event.pos()) == insert_index:
                self.loop_insert_requested.emit(self.node_id, insert_index)
            event.accept()
            return
        if self._pressed_parallel_insert is not None:
            target = self._pressed_parallel_insert
            self._pressed_parallel_insert = None
            if self._parallel_insertion_at(event.pos()) == target:
                branch_id, insert_index = target
                self.parallel_insert_requested.emit(
                    self.node_id,
                    branch_id,
                    insert_index,
                )
            event.accept()
            return
        was_dragging = self._is_dragging
        target_center_y = self._drag_target_center_y
        dragged_item_uuid = self._dragged_item_uuid
        self._reset_drag_state()
        if was_dragging:
            if dragged_item_uuid == self.node_id:
                self.drag_ended.emit(self.node_id, True)
                self.move_requested.emit(self.node_id, target_center_y)
            else:
                self.loop_child_drag_ended.emit(
                    self.node_id,
                    dragged_item_uuid,
                    True,
                )
        event.accept()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self._reset_drag_state()
        if isinstance(self.entry, SubworkflowBlock):
            self.subworkflow_open_requested.emit(self.entry.uuid)
            event.accept()
            return
        item_uuid = self._item_uuid_at(event.pos())
        self.edit_requested.emit(self.node_id, item_uuid)
        event.accept()

    def _reset_drag_state(self) -> None:
        self._press_scene_y = None
        self._press_local_position = None
        self._is_dragging = False
        preview = self._drag_preview
        if preview is not None:
            preview_scene = preview.scene()
            if preview_scene is not None:
                preview_scene.removeItem(preview)
        self._drag_preview = None
        self._drag_preview_anchor = QPointF()
        self._dragged_item_uuid = self.node_id
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setOpacity(1.0)
        self.setZValue(0.0)
        self.update()

    def set_active_child_uuid(self, item_uuid: str) -> None:
        """Highlight the nested loop action currently selected by the user."""
        normalized = item_uuid if item_uuid != self.node_id else ""
        if self._active_child_uuid == normalized:
            return
        self._active_child_uuid = normalized
        self.update()

    def set_loop_drop_target(self, child_index: int | None) -> None:
        if self._active_loop_drop_index == child_index:
            return
        self._active_loop_drop_index = child_index
        if child_index is None:
            self._drop_pulse_animation.stop()
            self._drop_pulse_phase = 0.0
        elif self._drop_pulse_animation.state() != QAbstractAnimation.State.Running:
            self._drop_pulse_animation.start()
        self.update()

    def _on_drop_pulse_value_changed(self, value: object) -> None:
        if isinstance(value, (int, float)):
            self._drop_pulse_phase = float(value)
            self.update()

    def cancel_drag(self) -> None:
        """Restore the stationary node and discard transient drag feedback."""
        was_dragging = self._is_dragging
        self._reset_drag_state()
        if was_dragging:
            self.drag_ended.emit(self.node_id, False)

    def refresh_theme(self) -> None:
        """Discard the device-coordinate cache after a palette change."""
        cache_mode = self.cacheMode()
        self.setCacheMode(self.CacheMode.NoCache)
        self.update()
        self.setCacheMode(cache_mode)
        self.update()

    def sceneEvent(self, event: QEvent) -> bool:  # noqa: N802
        if event.type() is QEvent.Type.UngrabMouse:
            self.cancel_drag()
        return super().sceneEvent(event)

    def _begin_drag_preview(self, scene_position: QPointF) -> None:
        preview_entry = self._drag_preview_entry()
        preview_rect = self._drag_preview_rect()
        preview_scale = bounded_drag_preview_scale(
            preview_rect.width(),
            preview_rect.height(),
        )
        preview_width = max(1, round(preview_rect.width() * preview_scale))
        preview_height = max(1, round(preview_rect.height() * preview_scale))
        pixmap = QPixmap(preview_width, preview_height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.scale(preview_scale, preview_scale)
            if preview_entry is self.entry and isinstance(
                preview_entry,
                (LoopBlock, ParallelBlock),
            ):
                self.paint(painter, QStyleOptionGraphicsItem(), None)
            else:
                colors = canvas_colors(QApplication.palette())
                self._paint_compact_entry_card(
                    painter,
                    preview_entry,
                    QRectF(0.0, 0.0, preview_rect.width(), preview_rect.height()),
                    colors.text,
                    colors.secondary_text,
                )
        finally:
            painter.end()

        preview = NodeDragPreviewItem(self._dragged_item_uuid, pixmap)
        scene = self.scene()
        if scene is not None:
            scene.addItem(preview)
        self._drag_preview = preview
        press_position = self._press_local_position or preview_rect.center()
        self._drag_preview_anchor = QPointF(
            (press_position.x() - preview_rect.left()) * preview_scale,
            (press_position.y() - preview_rect.top()) * preview_scale,
        )
        self._is_dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragged_item_uuid == self.node_id:
            self.setOpacity(DRAG_SOURCE_OPACITY)
        self.update()
        self._position_drag_preview(scene_position)

    def _position_drag_preview(self, scene_position: QPointF) -> None:
        if self._drag_preview is None:
            return
        self._drag_preview.setPos(scene_position - self._drag_preview_anchor)

    def _drag_preview_entry(self) -> SequenceEntry:
        return self._entry_at(self._press_local_position or self.boundingRect().center())

    def _drag_preview_rect(self) -> QRectF:
        position = self._press_local_position or self.boundingRect().center()
        if isinstance(self.entry, LoopBlock):
            body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
            card_left = (LOOP_NODE_WIDTH - NODE_WIDTH) / 2.0
            for index, _child in enumerate(self.entry.items[:MAX_VISIBLE_LOOP_CHILDREN]):
                child_rect = QRectF(
                    card_left,
                    body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP),
                    NODE_WIDTH,
                    LOOP_CHILD_HEIGHT,
                )
                if child_rect.contains(position):
                    return child_rect
        if isinstance(self.entry, ParallelBlock):
            target = self.parallel_child_at(position)
            if target is not None:
                branch_id, child_uuid = target
                for branch_index, branch in enumerate(self.entry.branches):
                    if branch.branch_id != branch_id:
                        continue
                    for child_index, child in enumerate(branch.items):
                        if child.uuid == child_uuid:
                            return QRectF(
                                self._parallel_branch_left(branch_index) + 8.0,
                                PARALLEL_HEADER_HEIGHT
                                + PARALLEL_SECTION_GAP
                                + PARALLEL_BRANCH_HEADER_HEIGHT
                                + child_index
                                * (PARALLEL_CHILD_HEIGHT + PARALLEL_CHILD_GAP),
                                PARALLEL_BRANCH_WIDTH - 16.0,
                                PARALLEL_CHILD_HEIGHT,
                            )
        return self.boundingRect()

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

    def _paint_subworkflow(
        self,
        painter: QPainter,
        foreground: QColor,
        secondary_text: QColor,
        *,
        emphasized: bool,
    ) -> None:
        subworkflow = self.entry
        if not isinstance(subworkflow, SubworkflowBlock):
            return
        colors = canvas_colors()
        rect = QRectF(0.0, 0.0, NODE_WIDTH, NODE_HEIGHT)
        painter.setBrush(_card_brush(colors.surface, colors.accent, emphasized))
        border = STATUS_COLORS[SequenceItemStatus.RUNNING] if _entry_is_running(subworkflow) else colors.border
        border_width = 1.5 + self._execution_pulse_phase * 1.5
        painter.setPen(QPen(border, border_width))
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        _draw_svg_icon(
            painter,
            IconName.WORKFLOW,
            QRectF(14.0, 17.0, 22.0, 22.0),
            QColor("#3b82f6"),
        )
        painter.setPen(foreground)
        painter.setFont(canvas_font(emphasis=True))
        subworkflow_name_rect = QRectF(46.0, 5.0, NODE_WIDTH - 58.0, 25.0)
        painter.drawText(
            subworkflow_name_rect,
            Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(
                subworkflow.name,
                Qt.TextElideMode.ElideRight,
                int(subworkflow_name_rect.width()),
            ),
        )
        painter.setPen(secondary_text)
        painter.setFont(canvas_font(secondary=True))
        painter.drawText(
            QRectF(46.0, 29.0, NODE_WIDTH - 58.0, 21.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"{len(subworkflow.items)} 个节点",
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
        del secondary_text
        colors = canvas_colors()
        painter.setBrush(_card_brush(colors.surface, colors.accent, emphasized))
        is_running = item.status is SequenceItemStatus.RUNNING
        border = STATUS_COLORS[item.status] if is_running else colors.border
        border_width = 1.5 + self._execution_pulse_phase * 1.5 if is_running else 1.5
        painter.setPen(QPen(border, border_width))
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        color = ACTION_COLORS.get(item.definition.type, QColor("#64748b"))
        _draw_svg_icon(
            painter,
            action_icon(item.definition),
            QRectF(rect.left() + 14.0, rect.top() + 18.0, 22.0, 22.0),
            color,
        )
        painter.setPen(foreground)
        painter.setFont(canvas_font(emphasis=True))
        action_name_rect = QRectF(
            rect.left() + 46.0,
            rect.top() + 13.0,
            rect.width() - 124.0,
            30.0,
        )
        painter.drawText(
            action_name_rect,
            Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(
                item.definition.name,
                Qt.TextElideMode.ElideRight,
                int(action_name_rect.width()),
            ),
        )
        status_color = STATUS_COLORS[item.status]
        painter.setBrush(status_color)
        painter.setPen(Qt.PenStyle.NoPen)
        status_rect = QRectF(
            rect.right() - 72.0,
            rect.top() + 18.0,
            60.0,
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
        control = control_flow_colors(ControlFlowKind.LOOP)
        painter.setBrush(control.header)
        loop_running = _entry_is_running(loop)
        if loop_running:
            loop_border = STATUS_COLORS[SequenceItemStatus.RUNNING]
            loop_border_width = 1.5 + self._execution_pulse_phase * 1.5
        elif self.isSelected():
            loop_border = accent
            loop_border_width = 2.0
        else:
            loop_border = QColor(control.accent)
            loop_border.setAlpha(180 if self.isUnderMouse() else 96)
            loop_border_width = 1.5 if self.isUnderMouse() else 1.0
        painter.setPen(QPen(loop_border, loop_border_width))
        painter.drawRoundedRect(header_rect, NODE_RADIUS, NODE_RADIUS)

        badge_rect = QRectF(card_left + 14.0, 12.0, 24.0, 24.0)
        badge_fill = QColor(control.accent)
        badge_fill.setAlpha(48 if control.header.lightnessF() < 0.5 else 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_fill)
        painter.drawRoundedRect(badge_rect, 6.0, 6.0)
        _draw_svg_icon(
            painter,
            IconName.LOOP,
            QRectF(card_left + 17.0, 15.0, 18.0, 18.0),
            control.accent,
        )
        painter.setPen(control.header_text)
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(card_left + 48.0, 7.0, NODE_WIDTH - 64.0, 26.0),
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
            QRectF(card_left + 48.0, 31.0, NODE_WIDTH - 64.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"{loop.repeat_count} 次 · {len(loop.items)} 个节点{progress}",
        )

        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        painter.setPen(control.path)
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
        if visible_children:
            children_bottom = (
                body_top
                + len(visible_children) * LOOP_CHILD_HEIGHT
                + max(0, len(visible_children) - 1) * LOOP_CHILD_GAP
            )
        else:
            children_bottom = body_top + 44.0
        footer_top = children_bottom + LOOP_SECTION_GAP
        self._paint_loop_spine(
            painter,
            header_rect.bottom(),
            footer_top,
            canvas_colors().edge,
        )
        if self._editing_enabled:
            self._paint_insert_marker(
                painter,
                body_top - LOOP_SECTION_GAP / 2.0,
                accent,
                active=self._active_loop_drop_index == 0,
                active_phase=self._drop_pulse_phase,
                hover_phase=self._loop_insert_hover_phase(0),
            )
        for index, child in enumerate(visible_children):
            child_top = body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            if self._editing_enabled and index > 0:
                self._paint_insert_marker(
                    painter,
                    child_top - LOOP_CHILD_GAP / 2.0,
                    accent,
                    active=self._active_loop_drop_index == index,
                    active_phase=self._drop_pulse_phase,
                    hover_phase=self._loop_insert_hover_phase(index),
                )
            child_rect = QRectF(
                card_left,
                child_top,
                NODE_WIDTH,
                LOOP_CHILD_HEIGHT,
            )
            is_dragged_child = (
                self._is_dragging
                and self._dragged_item_uuid == child.uuid
            )
            if is_dragged_child:
                self._paint_drag_source_backdrop(painter, child_rect)
                painter.save()
                painter.setOpacity(DRAG_SOURCE_OPACITY)
            self._paint_compact_entry_card(
                painter,
                child,
                child_rect,
                foreground,
                secondary_text,
                emphasized=child.uuid == self._active_child_uuid,
            )
            if is_dragged_child:
                painter.restore()
        if visible_children:
            if self._editing_enabled and len(loop.items) <= MAX_VISIBLE_LOOP_CHILDREN:
                self._paint_insert_marker(
                    painter,
                    children_bottom + LOOP_SECTION_GAP / 2.0,
                    accent,
                    active=self._active_loop_drop_index == len(loop.items),
                    active_phase=self._drop_pulse_phase,
                    hover_phase=self._loop_insert_hover_phase(len(loop.items)),
                )
        else:
            painter.setPen(secondary_text)
            painter.drawText(
                QRectF(card_left, body_top, NODE_WIDTH, 44.0),
                Qt.AlignmentFlag.AlignCenter,
                "循环体为空",
            )
        footer_left = (LOOP_NODE_WIDTH - LOOP_FOOTER_WIDTH) / 2.0
        footer_rect = QRectF(
            footer_left,
            footer_top,
            LOOP_FOOTER_WIDTH,
            LOOP_FOOTER_HEIGHT,
        )
        painter.setBrush(control.footer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(footer_rect, 20.0, 20.0)
        painter.setPen(control.footer_text)
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
        self._paint_loop_paths(painter, header_rect, footer_rect, control.path)

    @staticmethod
    def _paint_loop_spine(
        painter: QPainter,
        start_y: float,
        end_y: float,
        color: QColor,
    ) -> None:
        """Draw the neutral execution path behind loop children and insert markers."""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.5))
        center_x = LOOP_NODE_WIDTH / 2.0
        painter.drawLine(QPointF(center_x, start_y), QPointF(center_x, end_y))

    def _paint_parallel(
        self,
        painter: QPainter,
        foreground: QColor,
        secondary_text: QColor,
        accent: QColor,
    ) -> None:
        parallel = self.entry
        if not isinstance(parallel, ParallelBlock):
            return
        colors = canvas_colors()
        control = control_flow_colors(ControlFlowKind.PARALLEL)
        header_rect = QRectF(0.0, 0.0, self.node_width, PARALLEL_HEADER_HEIGHT)
        painter.setBrush(control.header)
        parallel_running = _entry_is_running(parallel)
        parallel_border = (
            STATUS_COLORS[SequenceItemStatus.RUNNING]
            if parallel_running
            else accent if self.isSelected() else control.accent
        )
        parallel_border_width = (
            1.5 + self._execution_pulse_phase * 1.5
            if parallel_running
            else 1.5
        )
        painter.setPen(QPen(parallel_border, parallel_border_width))
        painter.drawRoundedRect(header_rect, NODE_RADIUS, NODE_RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(control.accent)
        painter.drawRoundedRect(QRectF(0.0, 0.0, 5.0, header_rect.height()), 2.5, 2.5)
        _draw_svg_icon(
            painter,
            IconName.WORKFLOW,
            QRectF(18.0, 12.0, 18.0, 18.0),
            control.accent,
        )
        painter.setPen(control.header_text)
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(
            QRectF(46.0, 8.0, self.node_width - 64.0, 26.0),
            Qt.AlignmentFlag.AlignVCenter,
            "并行",
        )
        painter.setFont(canvas_font(secondary=True))
        painter.drawText(
            QRectF(46.0, 36.0, self.node_width - 64.0, 24.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"{len(parallel.branches)} 个分支 · 全部分支完成后汇合",
        )

        branch_top = PARALLEL_HEADER_HEIGHT + PARALLEL_SECTION_GAP
        body_top = branch_top + PARALLEL_BRANCH_HEADER_HEIGHT
        max_children = max(
            (len(branch.items) for branch in parallel.branches),
            default=0,
        )
        body_height = max(1, max_children) * PARALLEL_CHILD_HEIGHT
        body_height += max(0, max_children - 1) * PARALLEL_CHILD_GAP
        footer_top = body_top + body_height + PARALLEL_SECTION_GAP
        footer_rect = QRectF(
            (self.node_width - LOOP_FOOTER_WIDTH) / 2.0,
            footer_top,
            LOOP_FOOTER_WIDTH,
            PARALLEL_FOOTER_HEIGHT,
        )

        for branch_index, branch in enumerate(parallel.branches):
            branch_left = self._parallel_branch_left(branch_index)
            lane_rect = QRectF(
                branch_left,
                branch_top,
                PARALLEL_BRANCH_WIDTH,
                PARALLEL_BRANCH_HEADER_HEIGHT + body_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.surface)
            painter.drawRoundedRect(lane_rect, NODE_RADIUS, NODE_RADIUS)
            state = self._parallel_branch_states.get(branch.branch_id, "pending")
            state_color, state_label = _parallel_state_style(state)
            painter.setBrush(state_color)
            painter.drawRoundedRect(
                QRectF(
                    branch_left,
                    branch_top,
                    PARALLEL_BRANCH_WIDTH,
                    PARALLEL_BRANCH_HEADER_HEIGHT,
                ),
                NODE_RADIUS,
                NODE_RADIUS,
            )
            painter.setPen(contrasting_text(state_color))
            painter.setFont(canvas_font(emphasis=True, secondary=True))
            painter.drawText(
                QRectF(branch_left + 12.0, branch_top, 105.0, PARALLEL_BRANCH_HEADER_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter,
                f"分支 {branch_index + 1}",
            )
            painter.setFont(canvas_font(secondary=True))
            painter.drawText(
                QRectF(
                    branch_left + 112.0,
                    branch_top,
                    PARALLEL_BRANCH_WIDTH - 124.0,
                    PARALLEL_BRANCH_HEADER_HEIGHT,
                ),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                state_label,
            )
            if self._editing_enabled:
                self._paint_insert_marker(
                    painter,
                    body_top - PARALLEL_CHILD_GAP / 2.0,
                    accent,
                    center_x=branch_left + PARALLEL_BRANCH_WIDTH / 2.0,
                    hover_phase=self._parallel_insert_hover_phase(branch.branch_id, 0),
                )
            for child_index, child in enumerate(branch.items):
                child_top = body_top + child_index * (PARALLEL_CHILD_HEIGHT + PARALLEL_CHILD_GAP)
                if self._editing_enabled and child_index > 0:
                    self._paint_insert_marker(
                        painter,
                        child_top - PARALLEL_CHILD_GAP / 2.0,
                        accent,
                        center_x=branch_left + PARALLEL_BRANCH_WIDTH / 2.0,
                        hover_phase=self._parallel_insert_hover_phase(
                            branch.branch_id,
                            child_index,
                        ),
                    )
                child_rect = QRectF(
                    branch_left + 8.0,
                    child_top,
                    PARALLEL_BRANCH_WIDTH - 16.0,
                    PARALLEL_CHILD_HEIGHT,
                )
                self._paint_compact_entry_card(
                    painter,
                    child,
                    child_rect,
                    foreground,
                    secondary_text,
                )
            if self._editing_enabled and branch.items:
                self._paint_insert_marker(
                    painter,
                    body_top
                    + len(branch.items) * PARALLEL_CHILD_HEIGHT
                    + max(0, len(branch.items) - 1) * PARALLEL_CHILD_GAP
                    + PARALLEL_CHILD_GAP / 2.0,
                    accent,
                    center_x=branch_left + PARALLEL_BRANCH_WIDTH / 2.0,
                    hover_phase=self._parallel_insert_hover_phase(
                        branch.branch_id,
                        len(branch.items),
                    ),
                )

        painter.setBrush(control.footer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(footer_rect, 20.0, 20.0)
        painter.setPen(control.footer_text)
        painter.setFont(canvas_font(emphasis=True, secondary=True))
        painter.drawText(footer_rect, Qt.AlignmentFlag.AlignCenter, "并行汇合")
        self._paint_parallel_paths(painter, header_rect, footer_rect, branch_top)

    def _paint_compact_entry_card(
        self,
        painter: QPainter,
        entry: SequenceEntry,
        rect: QRectF,
        foreground: QColor,
        secondary_text: QColor,
        *,
        emphasized: bool = False,
    ) -> None:
        if isinstance(entry, SequenceItem):
            self._paint_action_card(
                painter,
                entry,
                rect,
                foreground,
                secondary_text,
                emphasized=emphasized,
            )
            return
        colors = canvas_colors()
        painter.setBrush(_card_brush(colors.canvas, colors.accent, emphasized))
        painter.setPen(QPen(colors.accent if emphasized else colors.border, 1.5))
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        painter.setPen(foreground)
        painter.setFont(canvas_font(emphasis=True, secondary=True))
        if isinstance(entry, LoopBlock):
            label = "循环"
            count = f"{entry.repeat_count} 次 · {len(entry.items)} 项"
        elif isinstance(entry, SubworkflowBlock):
            label = f"子流程 · {entry.name}"
            count = f"{len(entry.items)} 个节点"
        else:
            label = "嵌套并行"
            count = f"{len(entry.branches)} 个分支"
        painter.drawText(
            QRectF(rect.left() + 12.0, rect.top() + 6.0, rect.width() - 24.0, 24.0),
            Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.setPen(secondary_text)
        painter.setFont(canvas_font(secondary=True))
        painter.drawText(
            QRectF(rect.left() + 12.0, rect.top() + 32.0, rect.width() - 24.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter,
            count,
        )

    @staticmethod
    def _paint_drag_source_backdrop(painter: QPainter, rect: QRectF) -> None:
        """Occlude container paths before painting a faded nested drag source."""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(canvas_colors().canvas)
        painter.drawRoundedRect(rect, NODE_RADIUS, NODE_RADIUS)
        painter.restore()

    def _paint_parallel_paths(
        self,
        painter: QPainter,
        header_rect: QRectF,
        footer_rect: QRectF,
        branch_top: float,
    ) -> None:
        parallel = self.entry
        if not isinstance(parallel, ParallelBlock):
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#a78bfa"), 2.0))
        for branch_index, _branch in enumerate(parallel.branches):
            branch_center = self._parallel_branch_left(branch_index) + PARALLEL_BRANCH_WIDTH / 2.0
            painter.drawLine(
                QPointF(header_rect.center().x(), header_rect.bottom()),
                QPointF(branch_center, branch_top),
            )
            painter.drawLine(
                QPointF(branch_center, footer_rect.top() - PARALLEL_SECTION_GAP),
                QPointF(footer_rect.center().x(), footer_rect.top()),
            )

    @staticmethod
    def _paint_insert_marker(
        painter: QPainter,
        center_y: float,
        accent: QColor,
        *,
        center_x: float = LOOP_NODE_WIDTH / 2.0,
        active: bool = False,
        active_phase: float = 0.0,
        hover_phase: float = 0.0,
    ) -> None:
        if active:
            glow = QColor("#16a34a")
            glow.setAlphaF(0.20 + active_phase * 0.16)
            painter.setBrush(glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPointF(center_x, center_y),
                20.0 + active_phase * 3.0,
                20.0 + active_phase * 3.0,
            )
        if hover_phase > 0.0 and not active:
            glow = QColor(accent)
            glow.setAlphaF(0.10 + hover_phase * 0.10)
            painter.setBrush(glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPointF(center_x, center_y),
                18.0 + hover_phase * 3.0,
                18.0 + hover_phase * 3.0,
            )
        painter.setBrush(QColor("#16a34a") if active else accent)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = 17.0 + active_phase * 2.0 if active else 15.0 + hover_phase * 2.0
        painter.drawEllipse(QPointF(center_x, center_y), radius, radius)
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
        loop_color: QColor,
    ) -> None:
        left_x = 24.0
        right_x = LOOP_NODE_WIDTH - 24.0
        header_center_y = header_rect.center().y()
        footer_center_y = footer_rect.center().y()
        path_color = QColor(loop_color)
        is_dark = canvas_colors().canvas.lightnessF() < 0.5
        path_color.setAlpha(205 if is_dark else 175)
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
        painter.setPen(QPen(path_color, 1.5))
        painter.drawPath(forward_path)

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
        return_pen = QPen(path_color, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(return_pen)
        painter.drawPath(return_path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(path_color)
        for point in (
            QPointF(header_rect.left(), header_center_y),
            QPointF(header_rect.right(), header_center_y),
            QPointF(footer_rect.left(), footer_center_y),
            QPointF(footer_rect.right(), footer_center_y),
        ):
            painter.drawEllipse(point, 2.5, 2.5)

        left_label_rect = QRectF(
            4.0,
            header_center_y + 15.0,
            header_rect.left() - 14.0,
            24.0,
        )
        right_label_rect = QRectF(
            header_rect.right() + 6.0,
            header_center_y + 15.0,
            right_x - header_rect.right() - 8.0,
            24.0,
        )
        label_surface = QColor(canvas_colors().canvas)
        label_surface.setAlpha(235)
        painter.setBrush(label_surface)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(left_label_rect, 5.0, 5.0)
        painter.drawRoundedRect(right_label_rect, 5.0, 5.0)
        painter.setFont(canvas_font(secondary=True))
        painter.setPen(path_color)
        painter.drawText(
            left_label_rect,
            Qt.AlignmentFlag.AlignCenter,
            "下一次",
        )
        painter.drawText(
            right_label_rect,
            Qt.AlignmentFlag.AlignCenter,
            "达到次数",
        )

    def _item_uuid_at(self, position: QPointF) -> str:
        if not self._editing_enabled:
            return self.entry.uuid
        if isinstance(self.entry, ParallelBlock):
            target = self.parallel_child_at(position)
            return target[1] if target is not None else self.entry.uuid
        if not isinstance(self.entry, LoopBlock):
            return self.entry.uuid
        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        for index, child in enumerate(self.entry.items[:MAX_VISIBLE_LOOP_CHILDREN]):
            child_top = body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            if child_top <= position.y() <= child_top + LOOP_CHILD_HEIGHT:
                return child.uuid
        return self.entry.uuid

    def _entry_at(self, position: QPointF) -> SequenceEntry:
        """Return the concrete card under the pointer, or this container."""
        if isinstance(self.entry, LoopBlock):
            body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
            card_left = (LOOP_NODE_WIDTH - NODE_WIDTH) / 2.0
            for index, child in enumerate(self.entry.items[:MAX_VISIBLE_LOOP_CHILDREN]):
                child_rect = QRectF(
                    card_left,
                    body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP),
                    NODE_WIDTH,
                    LOOP_CHILD_HEIGHT,
                )
                if child_rect.contains(position):
                    return child
        elif isinstance(self.entry, ParallelBlock):
            target = self.parallel_child_at(position)
            if target is not None:
                branch_id, child_uuid = target
                for branch in self.entry.branches:
                    if branch.branch_id == branch_id:
                        return next(
                            child
                            for child in branch.items
                            if child.uuid == child_uuid
                        )
        return self.entry

    def item_uuid_at(self, position: QPointF) -> str:
        """Resolve the entry represented at a local position."""
        return self._item_uuid_at(position)

    def tooltip_at(self, position: QPointF) -> str:
        """Resolve tooltip content for the concrete card below a local pointer."""
        if self._insert_target_at(position) is not None:
            return _INSERT_ACTION_TOOLTIP
        return self._tooltip_for_entry(self._entry_at(position))

    def parallel_child_at(self, position: QPointF) -> tuple[str, str] | None:
        parallel = self.entry
        if not isinstance(parallel, ParallelBlock):
            return None
        branch_index = self._parallel_branch_index_at(position.x())
        if branch_index is None:
            return None
        body_top = PARALLEL_HEADER_HEIGHT + PARALLEL_SECTION_GAP + PARALLEL_BRANCH_HEADER_HEIGHT
        branch = parallel.branches[branch_index]
        for child_index, child in enumerate(branch.items):
            child_top = body_top + child_index * (PARALLEL_CHILD_HEIGHT + PARALLEL_CHILD_GAP)
            if child_top <= position.y() <= child_top + PARALLEL_CHILD_HEIGHT:
                return branch.branch_id, child.uuid
        return None

    def parallel_drop_target(self, position: QPointF) -> tuple[str, int] | None:
        parallel = self.entry
        if not isinstance(parallel, ParallelBlock):
            return None
        branch_index = self._parallel_branch_index_at(position.x())
        if branch_index is None:
            return None
        branch = parallel.branches[branch_index]
        body_top = PARALLEL_HEADER_HEIGHT + PARALLEL_SECTION_GAP + PARALLEL_BRANCH_HEADER_HEIGHT
        insertion_index = 0
        for child_index in range(len(branch.items)):
            child_center = (
                body_top
                + child_index * (PARALLEL_CHILD_HEIGHT + PARALLEL_CHILD_GAP)
                + PARALLEL_CHILD_HEIGHT / 2.0
            )
            if position.y() < child_center:
                break
            insertion_index = child_index + 1
        return branch.branch_id, insertion_index

    def _parallel_insertion_at(
        self,
        position: QPointF,
    ) -> tuple[str, int] | None:
        if not self._editing_enabled:
            return None
        target = self.parallel_drop_target(position)
        if target is None:
            return None
        _branch_id, insertion_index = target
        body_top = PARALLEL_HEADER_HEIGHT + PARALLEL_SECTION_GAP + PARALLEL_BRANCH_HEADER_HEIGHT
        center_y = body_top - PARALLEL_CHILD_GAP / 2.0
        if insertion_index > 0:
            center_y = (
                body_top
                + insertion_index * PARALLEL_CHILD_HEIGHT
                + (insertion_index - 0.5) * PARALLEL_CHILD_GAP
            )
        return target if abs(position.y() - center_y) <= 20.0 else None

    def _parallel_branch_index_at(self, local_x: float) -> int | None:
        parallel = self.entry
        if not isinstance(parallel, ParallelBlock):
            return None
        for index, _branch in enumerate(parallel.branches):
            left = self._parallel_branch_left(index)
            if left <= local_x <= left + PARALLEL_BRANCH_WIDTH:
                return index
        return None

    @staticmethod
    def _parallel_branch_left(branch_index: int) -> float:
        return PARALLEL_BRANCH_PADDING + branch_index * (
            PARALLEL_BRANCH_WIDTH + PARALLEL_BRANCH_GAP
        )

    def _loop_insertion_index_at(self, position: QPointF) -> int | None:
        if not isinstance(self.entry, LoopBlock):
            return None
        if abs(position.x() - LOOP_NODE_WIDTH / 2.0) > 24.0:
            return None
        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        insertion_centers = [body_top - LOOP_SECTION_GAP / 2.0]
        insertion_centers.extend(
            body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP) - LOOP_CHILD_GAP / 2.0
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

    def loop_drop_target(self, position: QPointF) -> int | None:
        """Resolve a pointer position to an insertion index in this loop body."""
        if not isinstance(self.entry, LoopBlock):
            return None
        card_left = (LOOP_NODE_WIDTH - NODE_WIDTH) / 2.0
        if not card_left <= position.x() <= card_left + NODE_WIDTH:
            return None
        body_top = LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP
        footer_top = self.node_height - LOOP_FOOTER_HEIGHT
        if not body_top <= position.y() <= footer_top:
            return None
        child_index = 0
        for index, _child in enumerate(self.entry.items):
            center_y = body_top + index * (LOOP_CHILD_HEIGHT + LOOP_CHILD_GAP)
            center_y += LOOP_CHILD_HEIGHT / 2.0
            if position.y() < center_y:
                break
            child_index = index + 1
        return child_index

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        insert_target = self._insert_target_at(event.pos())
        self._set_hovered_insert_target(insert_target)
        if insert_target is not None:
            self.setToolTip(_INSERT_ACTION_TOOLTIP)
        else:
            self.setToolTip(self._tooltip_for_entry(self._entry_at(event.pos())))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._set_hovered_insert_target(None)
        self.setToolTip(self._tooltip())
        super().hoverLeaveEvent(event)

    def _insert_target_at(self, position: QPointF) -> tuple[str, int] | None:
        loop_index = self._loop_insertion_index_at(position)
        if loop_index is not None:
            return ("", loop_index)
        return self._parallel_insertion_at(position)

    def _set_hovered_insert_target(self, target: tuple[str, int] | None) -> None:
        if self._hovered_insert_target == target:
            return
        self._hovered_insert_target = target
        self._insert_hover_animation.stop()
        self._insert_hover_animation.setStartValue(self._insert_hover_phase)
        self._insert_hover_animation.setEndValue(1.0 if target is not None else 0.0)
        self._insert_hover_animation.start()
        self.update()

    def _on_insert_hover_value_changed(self, value: object) -> None:
        if isinstance(value, (int, float)):
            self._insert_hover_phase = float(value)
            self.update()

    def _loop_insert_hover_phase(self, index: int) -> float:
        return self._insert_hover_phase if self._hovered_insert_target == ("", index) else 0.0

    def _parallel_insert_hover_phase(self, branch_id: str, index: int) -> float:
        target = (branch_id, index)
        return self._insert_hover_phase if self._hovered_insert_target == target else 0.0

    def _tooltip(self) -> str:
        return self._tooltip_for_entry(self.entry)

    @staticmethod
    def _tooltip_for_entry(entry: SequenceEntry) -> str:
        if isinstance(entry, SubworkflowBlock):
            return f"子流程: {entry.name}\n{len(entry.items)} 个节点，双击进入编辑"
        if isinstance(entry, LoopBlock):
            return (
                f"循环 {entry.repeat_count} 次\n"
                f"{len(entry.items)} 个动作，共 {entry.total_steps} 步"
            )
        if isinstance(entry, ParallelBlock):
            action_count = sum(len(branch.items) for branch in entry.branches)
            return f"并行 · {len(entry.branches)} 个分支\n共 {action_count} 个控制流节点"
        parameters = ", ".join(
            f"{name}={value}" for name, value in list(entry.definition.parameters.items())[:5]
        )
        return f"{entry.definition.name}\n{parameters or '无参数'}"


def _parallel_state_style(state: str) -> tuple[QColor, str]:
    return {
        "started": (QColor("#2563eb"), "执行中"),
        "completed": (QColor("#16a34a"), "完成"),
        "failed": (QColor("#dc2626"), "失败"),
        "cancelled": (QColor("#64748b"), "已取消"),
        "pending": (QColor("#475569"), "等待"),
    }.get(state, (QColor("#475569"), "等待"))


class StartEndItem(QGraphicsObject):
    def __init__(self, label: str, color: QColor) -> None:
        super().__init__()
        self._label = label
        self._color = color
        self._is_hovered = False
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(True)

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
        fill = self._color.lighter(108) if self._is_hovered else self._color
        painter.setBrush(fill)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.boundingRect(), 20.0, 20.0)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(canvas_font(emphasis=True))
        painter.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, self._label)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._is_hovered = True
        self.update()
        event.accept()

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._is_hovered = False
        self.update()
        event.accept()


class InsertionItem(QGraphicsObject):
    insert_requested = Signal(int)

    def __init__(self, index: int) -> None:
        super().__init__()
        self._index = index
        self._is_pressed = False
        self._is_drop_target_active = False
        self._target_label = ""
        self._is_hovered = False
        self._hover_phase = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(INSERT_HOVER_TRANSITION_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._on_hover_value_changed)
        self._pulse_phase = 0.0
        self._pulse_animation = QVariantAnimation(self)
        self._pulse_animation.setDuration(INSERT_TARGET_PULSE_DURATION_MS)
        self._pulse_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_animation.setKeyValueAt(0.0, 0.0)
        self._pulse_animation.setKeyValueAt(0.5, 1.0)
        self._pulse_animation.setKeyValueAt(1.0, 0.0)
        self._pulse_animation.setLoopCount(-1)
        self._pulse_animation.valueChanged.connect(self._on_pulse_value_changed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setToolTip(_INSERT_ACTION_TOOLTIP)

    @property
    def index(self) -> int:
        return self._index

    @property
    def is_drop_target_active(self) -> bool:
        return self._is_drop_target_active

    @property
    def target_label(self) -> str:
        return self._target_label

    @property
    def is_pulsing(self) -> bool:
        return self._pulse_animation.state() == QAbstractAnimation.State.Running

    @property
    def is_hovered(self) -> bool:
        return self._is_hovered

    @property
    def hover_phase(self) -> float:
        return self._hover_phase

    def boundingRect(self) -> QRectF:  # noqa: N802
        right_edge = INSERT_TARGET_SIZE + INSERT_TARGET_HINT_WIDTH + 16.0
        total_width = 2.0 * (right_edge - INSERT_TARGET_SIZE / 2.0)
        return QRectF(
            (INSERT_TARGET_SIZE - total_width) / 2.0,
            -8.0,
            total_width,
            INSERT_TARGET_SIZE + 16.0,
        )

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QRectF(0.0, 0.0, INSERT_TARGET_SIZE, INSERT_TARGET_SIZE))
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = canvas_colors()
        center = QPointF(INSERT_TARGET_SIZE / 2.0, INSERT_TARGET_SIZE / 2.0)
        if self._is_drop_target_active or self._hover_phase > 0.0:
            glow = QColor(colors.accent)
            glow.setAlphaF(
                0.08 * self._hover_phase
                + (0.20 + self._pulse_phase * 0.16 if self._is_drop_target_active else 0.0)
            )
            glow_radius = 18.0 + self._hover_phase * 3.0
            if self._is_drop_target_active:
                glow_radius = 20.0 + self._pulse_phase * 3.0
            painter.setBrush(glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, glow_radius, glow_radius)

        core_radius = 15.0
        core_radius += self._hover_phase * 2.0
        if self._is_drop_target_active:
            core_radius += 1.5 + self._pulse_phase * 1.5
        painter.setBrush(colors.accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, core_radius, core_radius)
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        painter.drawLine(
            QPointF(center.x() - 6.0, center.y()),
            QPointF(center.x() + 6.0, center.y()),
        )
        painter.drawLine(
            QPointF(center.x(), center.y() - 6.0),
            QPointF(center.x(), center.y() + 6.0),
        )

        hint_label = self._target_label if self._is_drop_target_active else "添加动作"
        hint_opacity = 1.0 if self._is_drop_target_active else self._hover_phase
        if hint_label and hint_opacity > 0.0:
            hint_rect = QRectF(
                INSERT_TARGET_SIZE + 8.0,
                7.0,
                INSERT_TARGET_HINT_WIDTH,
                30.0,
            )
            painter.save()
            painter.setOpacity(hint_opacity)
            painter.setBrush(colors.surface)
            painter.setPen(QPen(colors.accent, 1.25))
            painter.drawRoundedRect(hint_rect, 8.0, 8.0)
            painter.setPen(colors.text)
            painter.setFont(canvas_font(secondary=True))
            painter.drawText(
                hint_rect.adjusted(10.0, 0.0, -8.0, 0.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                hint_label,
            )
            painter.restore()

    def set_drop_target_active(self, active: bool, label: str = "") -> None:
        normalized_label = label.strip() if active else ""
        if self._is_drop_target_active == active and self._target_label == normalized_label:
            return
        self._is_drop_target_active = active
        self._target_label = normalized_label
        self._refresh_z_value()
        if active:
            self._pulse_animation.start()
        else:
            self._pulse_animation.stop()
            self._pulse_phase = 0.0
        self.update()

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._set_hovered(True)
        event.accept()

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._set_hovered(False)
        event.accept()

    def _set_hovered(self, hovered: bool) -> None:
        if self._is_hovered == hovered:
            return
        self._is_hovered = hovered
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_phase)
        self._hover_animation.setEndValue(1.0 if hovered else 0.0)
        self._hover_animation.start()
        self._refresh_z_value()

    def _refresh_z_value(self) -> None:
        self.setZValue(50.0 if self._is_drop_target_active else 30.0 if self._is_hovered else 0.0)

    def _on_hover_value_changed(self, value: object) -> None:
        if isinstance(value, (int, float)):
            self._hover_phase = float(value)
            self.update()

    def _on_pulse_value_changed(self, value: object) -> None:
        if isinstance(value, (int, float)):
            self._pulse_phase = float(value)
            self.update()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._is_pressed = True
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        should_insert = self._is_pressed and self.shape().contains(event.pos())
        self._is_pressed = False
        if should_insert:
            self.insert_requested.emit(self._index)
        event.accept()

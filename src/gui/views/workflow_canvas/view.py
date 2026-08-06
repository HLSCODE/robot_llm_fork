"""QGraphicsView interaction adapter for mouse, keyboard and touch."""

from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGestureEvent,
    QGraphicsView,
    QScroller,
    QWidget,
)

from ....domain.models import ActionDefinition
from .tokens import FIT_PADDING, MAX_SCALE, MIN_SCALE


class WorkflowCanvasView(QGraphicsView):
    action_dropped = Signal(object, float)
    delete_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()
    select_all_requested = Signal()
    clear_selection_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(self.DragMode.RubberBandDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription(
            "左键选择，Shift 左键多选，Ctrl 左键拖动画布，"
            "滚轮滚动，Ctrl 滚轮缩放，右键打开操作菜单"
        )
        self._is_panning = False
        self._last_pan_position: QPoint | None = None
        self.setViewportUpdateMode(self.ViewportUpdateMode.MinimalViewportUpdate)
        self.setOptimizationFlag(self.OptimizationFlag.DontSavePainterState, True)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(self.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        QScroller.grabGesture(
            self.viewport(),
            QScroller.ScrollerGestureType.TouchGesture,
        )
        self.grabGesture(Qt.GestureType.PinchGesture)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802
        self._accept_action_drop(event)

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:  # noqa: N802
        self._accept_action_drop(event)

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802
        if event is None or event.mimeData() is None:
            return
        mime = event.mimeData()
        if not mime.hasFormat("application/x-action"):
            event.ignore()
            return
        try:
            payload = bytes(mime.data("application/x-action")).decode("utf-8")
            action = ActionDefinition.from_dict(json.loads(payload))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            event.ignore()
            return
        scene_y = self.mapToScene(event.position().toPoint()).y()
        self.action_dropped.emit(action, scene_y)
        event.acceptProposedAction()

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            self._scale_by(factor)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        is_ctrl_left_drag = (
            event.button() is Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        is_middle_drag = event.button() is Qt.MouseButton.MiddleButton
        if is_ctrl_left_drag or is_middle_drag:
            self._is_panning = True
            self._last_pan_position = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if self._is_panning and self._last_pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._last_pan_position
            self._last_pan_position = position
            horizontal = self.horizontalScrollBar()
            vertical = self.verticalScrollBar()
            horizontal.setValue(horizontal.value() - delta.x())
            vertical.setValue(vertical.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if self._is_panning and event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        }:
            self._stop_panning()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo_requested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo_requested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_requested.emit()
            event.accept()
            return
        if event.key() is Qt.Key.Key_Escape:
            self._stop_panning()
            self.clear_selection_requested.emit()
            event.accept()
            return
        if (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and event.key() == Qt.Key.Key_0
        ):
            self.reset_zoom()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _stop_panning(self) -> None:
        self._is_panning = False
        self._last_pan_position = None
        self.viewport().unsetCursor()

    def event(self, event: QEvent | None) -> bool:
        if isinstance(event, QGestureEvent):
            pinch = event.gesture(Qt.GestureType.PinchGesture)
            if pinch is not None:
                change_flags = pinch.changeFlags()
                if change_flags & pinch.ChangeFlag.ScaleFactorChanged:
                    self._scale_by(float(pinch.scaleFactor()))
                return True
        return super().event(event)

    def fit_workflow(self) -> None:
        scene = self.scene()
        if scene is None or scene.itemsBoundingRect().isEmpty():
            return
        self.fitInView(
            scene.itemsBoundingRect().adjusted(
                -FIT_PADDING,
                -FIT_PADDING,
                FIT_PADDING,
                FIT_PADDING,
            ),
            Qt.AspectRatioMode.KeepAspectRatio,
        )

    def reset_zoom(self) -> None:
        self.resetTransform()

    @staticmethod
    def _accept_action_drop(
        event: QDragEnterEvent | QDragMoveEvent | None,
    ) -> None:
        if event is None or event.mimeData() is None:
            return
        if event.mimeData().hasFormat("application/x-action"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _scale_by(self, factor: float) -> None:
        current = self.transform().m11()
        target = current * factor
        if target < MIN_SCALE or target > MAX_SCALE:
            return
        self.scale(factor, factor)

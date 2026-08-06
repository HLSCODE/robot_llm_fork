"""Animated, button-controlled side panel for the main GUI workspace."""

from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QRectF,
    QSize,
    Qt,
    Signal,
    QVariantAnimation,
)
from PySide6.QtGui import QEnterEvent, QMouseEvent, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QSplitter, QStyle


DRAWER_ANIMATION_DURATION_MS = 220
DRAWER_COLLAPSED_WIDTH = 0
DRAWER_MIN_EXPANDED_WIDTH = 220
DRAWER_MAX_EXPANDED_WIDTH = 360
DRAWER_WIDTH_RATIO = 0.36
DRAWER_HANDLE_IDLE_WIDTH = 4
DRAWER_HANDLE_HOVER_WIDTH = 28
DRAWER_DRAG_THRESHOLD_PX = 4


class DrawerHandleButton(QPushButton):
    """Full-height splitter target that stays visually quiet until hovered."""

    resized = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("drawerHandleButton")
        self.setFlat(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self._splitter: QSplitter | None = None
        self._pane_index = 0
        self._press_global_x: float | None = None
        self._press_sizes: tuple[int, ...] = ()
        self._is_dragging = False

    def bind_splitter(self, splitter: QSplitter, pane_index: int) -> None:
        self._splitter = splitter
        self._pane_index = pane_index
        splitter.setHandleWidth(DRAWER_HANDLE_IDLE_WIDTH)

    def enterEvent(self, event: QEnterEvent | None) -> None:  # noqa: N802
        if self._splitter is not None:
            self._splitter.setHandleWidth(DRAWER_HANDLE_HOVER_WIDTH)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent | None) -> None:  # noqa: N802
        if self._splitter is not None and not self._is_dragging:
            self._splitter.setHandleWidth(DRAWER_HANDLE_IDLE_WIDTH)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global_x = event.globalPosition().x()
        self._press_sizes = (
            tuple(self._splitter.sizes())
            if self._splitter is not None
            else ()
        )
        self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if (
            self._splitter is None
            or self._press_global_x is None
            or len(self._press_sizes) != 2
        ):
            super().mouseMoveEvent(event)
            return
        delta_x = event.globalPosition().x() - self._press_global_x
        if abs(delta_x) < DRAWER_DRAG_THRESHOLD_PX and not self._is_dragging:
            super().mouseMoveEvent(event)
            return
        self._is_dragging = True
        total_width = sum(self._press_sizes)
        direction = 1 if self._pane_index == 0 else -1
        pane_width = round(
            self._press_sizes[self._pane_index] + direction * delta_x
        )
        pane_width = max(0, min(pane_width, total_width - 1))
        other_width = total_width - pane_width
        if self._pane_index == 0:
            self._splitter.setSizes((pane_width, other_width))
        else:
            self._splitter.setSizes((other_width, pane_width))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        was_dragging = self._is_dragging
        self._press_global_x = None
        self._press_sizes = ()
        self._is_dragging = False
        if was_dragging:
            self.setDown(False)
            if self._splitter is not None:
                self.resized.emit(self._splitter.sizes()[self._pane_index])
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        center_x = self.rect().center().x()
        if not self.underMouse():
            line_color = (
                palette.highlight().color()
                if self.hasFocus()
                else palette.mid().color()
            )
            painter.setPen(QPen(line_color, 1.0))
            painter.drawLine(center_x, 0, center_x, self.height())
            return
        fill = palette.button().color()
        if self.isDown():
            fill = palette.highlight().color()
        painter.setBrush(fill)
        painter.setPen(QPen(palette.highlight().color(), 1.0))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0),
            5.0,
            5.0,
        )
        self.icon().paint(
            painter,
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
        )


class AnimatedSplitterDrawer(QObject):
    """Animate one QSplitter pane while exposing an obvious toggle button."""

    def __init__(
        self,
        splitter: QSplitter,
        toggle_button: QPushButton,
        *,
        pane_index: int = 0,
    ) -> None:
        super().__init__(splitter)
        self._splitter = splitter
        self._toggle_button = toggle_button
        self._pane_index = pane_index
        self._expanded_width = DRAWER_MIN_EXPANDED_WIDTH
        self._is_expanded = True
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(DRAWER_ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._apply_width)
        self._animation.finished.connect(self._sync_button)
        self._toggle_button.clicked.connect(self.toggle)
        if isinstance(self._toggle_button, DrawerHandleButton):
            self._toggle_button.bind_splitter(splitter, pane_index)
            self._toggle_button.resized.connect(self._on_resized)
        self._sync_button()

    @property
    def is_expanded(self) -> bool:
        return self._is_expanded

    def toggle(self) -> None:
        self.set_expanded(not self._is_expanded)

    def set_expanded(self, expanded: bool, *, animated: bool = True) -> None:
        current_width = self._current_width()
        if expanded:
            target_width = self._target_expanded_width()
        else:
            if current_width > DRAWER_COLLAPSED_WIDTH:
                self._expanded_width = current_width
            target_width = DRAWER_COLLAPSED_WIDTH
        self._is_expanded = expanded
        self._animation.stop()
        if not animated:
            self._apply_width(target_width)
            self._sync_button()
            return
        self._animation.setStartValue(current_width)
        self._animation.setEndValue(target_width)
        self._animation.start()
        self._sync_button()

    def _current_width(self) -> int:
        sizes = self._splitter.sizes()
        return sizes[self._pane_index] if len(sizes) > self._pane_index else 0

    def _target_expanded_width(self) -> int:
        available_width = max(1, sum(self._splitter.sizes()))
        responsive_width = round(available_width * DRAWER_WIDTH_RATIO)
        remembered_width = max(self._expanded_width, responsive_width)
        return min(
            DRAWER_MAX_EXPANDED_WIDTH,
            max(DRAWER_MIN_EXPANDED_WIDTH, remembered_width),
        )

    def _apply_width(self, value: object) -> None:
        if not isinstance(value, (int, float)):
            return
        width = max(DRAWER_COLLAPSED_WIDTH, int(value))
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        total_width = max(sum(sizes), width + 1)
        other_width = max(1, total_width - width)
        if self._pane_index == 0:
            self._splitter.setSizes((width, other_width))
        else:
            self._splitter.setSizes((other_width, width))

    def _sync_button(self) -> None:
        if self._is_expanded:
            arrow = QStyle.StandardPixmap.SP_ArrowLeft
            self._toggle_button.setToolTip(
                "单击收起动作库；水平拖动调整动作库宽度"
            )
            self._toggle_button.setAccessibleName("收起动作库")
        else:
            arrow = QStyle.StandardPixmap.SP_ArrowRight
            self._toggle_button.setToolTip(
                "单击展开动作库；向右拖动可直接调整宽度"
            )
            self._toggle_button.setAccessibleName("展开动作库")
        self._toggle_button.setText("")
        self._toggle_button.setIcon(
            self._toggle_button.style().standardIcon(arrow)
        )
        self._toggle_button.setIconSize(QSize(16, 16))

    def _on_resized(self, width: int) -> None:
        self._animation.stop()
        self._is_expanded = width > DRAWER_COLLAPSED_WIDTH
        if self._is_expanded:
            self._expanded_width = width
        self._sync_button()

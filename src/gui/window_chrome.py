"""Cross-platform main-window title bar and frameless resize behavior."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QToolButton,
    QWidget,
)
from shiboken6 import isValid

from .icons import IconName, themed_icon
from .menus import ApplicationMenuBar
from .toolbars import icon_foreground


TITLE_BAR_HEIGHT = 38
WINDOW_CONTROL_WIDTH = 44
RESIZE_BORDER_WIDTH = 6
WINDOW_CORNER_RADIUS = 10


class RoundedMainWindow(QMainWindow):
    """Own one opaque styled surface behind a translucent top-level window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window_surface = QFrame(self)
        self.window_surface.setObjectName("applicationWindowSurface")
        self.window_surface.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.window_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.window_surface.setGeometry(self.rect())
        self.window_surface.lower()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.window_surface.setGeometry(self.rect())
        self.window_surface.lower()


def _resize_edges(
    frame: QRect,
    global_position: QPoint,
    border_width: int = RESIZE_BORDER_WIDTH,
) -> Qt.Edge:
    """Resolve a global pointer position to one or two window edges."""
    if not frame.adjusted(-border_width, -border_width, border_width, border_width).contains(
        global_position
    ):
        return Qt.Edge(0)
    edges = Qt.Edge(0)
    if abs(global_position.x() - frame.left()) <= border_width:
        edges |= Qt.Edge.LeftEdge
    elif abs(global_position.x() - frame.right()) <= border_width:
        edges |= Qt.Edge.RightEdge
    if abs(global_position.y() - frame.top()) <= border_width:
        edges |= Qt.Edge.TopEdge
    elif abs(global_position.y() - frame.bottom()) <= border_width:
        edges |= Qt.Edge.BottomEdge
    return edges


def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
    if edges in {
        Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
    }:
        return Qt.CursorShape.SizeFDiagCursor
    if edges in {
        Qt.Edge.RightEdge | Qt.Edge.TopEdge,
        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
    }:
        return Qt.CursorShape.SizeBDiagCursor
    if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
        return Qt.CursorShape.SizeHorCursor
    if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


class FramelessResizeController(QObject):
    """Delegate edge resizing to the platform window manager."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._has_cursor_override = False
        self._application = QApplication.instance()
        self._is_installed = self._application is not None
        if self._application is not None:
            self._application.installEventFilter(self)
        window.setMouseTracking(True)
        for child in window.findChildren(QWidget):
            child.setMouseTracking(True)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window and event.type() in {
            QEvent.Type.Close,
            QEvent.Type.Destroy,
        }:
            self.dispose()
            return False
        if watched is self._window and event.type() is QEvent.Type.Hide:
            self._clear_cursor()
            return False
        if (
            not isinstance(watched, QWidget)
            or not isValid(watched)
            or watched.window() is not self._window
        ):
            return False
        if self._window.isMaximized() or self._window.isFullScreen():
            self._clear_cursor()
            return False
        if not isinstance(event, QMouseEvent):
            return False
        if event.type() is QEvent.Type.MouseMove and not event.buttons():
            edges = _resize_edges(
                self._window.frameGeometry(),
                event.globalPosition().toPoint(),
            )
            self._set_cursor(_cursor_for_edges(edges))
            return False
        if (
            event.type() is QEvent.Type.MouseButtonPress
            and event.button() is Qt.MouseButton.LeftButton
        ):
            edges = _resize_edges(
                self._window.frameGeometry(),
                event.globalPosition().toPoint(),
            )
            handle = self._window.windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                event.accept()
                return True
        return False

    def _set_cursor(self, cursor: Qt.CursorShape) -> None:
        if cursor is Qt.CursorShape.ArrowCursor:
            self._clear_cursor()
            return
        if self._has_cursor_override:
            QApplication.changeOverrideCursor(cursor)
            return
        QApplication.setOverrideCursor(cursor)
        self._has_cursor_override = True

    def _clear_cursor(self) -> None:
        if self._has_cursor_override:
            QApplication.restoreOverrideCursor()
            self._has_cursor_override = False

    def dispose(self) -> None:
        """Release the application-wide filter and any cursor it owns."""
        self._clear_cursor()
        if self._is_installed and self._application is not None:
            self._application.removeEventFilter(self)
            self._is_installed = False


class _WindowControlButton(QToolButton):
    def __init__(
        self,
        icon_name: IconName,
        accessible_name: str,
        *,
        parent: QWidget,
        close_button: bool = False,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._close_button = close_button
        self._hovered = False
        self.setObjectName("windowControlButton")
        self.setProperty("windowControl", "close" if close_button else "standard")
        self.setFixedSize(WINDOW_CONTROL_WIDTH, TITLE_BAR_HEIGHT)
        self.setIconSize(QSize(15, 15))
        self.setAutoRaise(True)
        self.setToolTip(accessible_name)
        self.setAccessibleName(accessible_name)
        self._refresh_icon()

    def set_icon_name(self, icon_name: IconName) -> None:
        if icon_name is self._icon_name:
            return
        self._icon_name = icon_name
        self._refresh_icon()

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.EnabledChange,
        }:
            self._refresh_icon()

    def _refresh_icon(self) -> None:
        color = (
            QColor("#ffffff")
            if self._close_button and self._hovered
            else icon_foreground(self)
        )
        self.setIcon(themed_icon(self, self._icon_name, size=15, color=color))


class ApplicationTitleBar(QFrame):
    """Combine application identity, menus and window controls in one row."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("applicationTitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(4)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("applicationTitleIcon")
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAccessibleName("应用程序 Logo")
        layout.addWidget(self.icon_label)

        self.menu_bar = ApplicationMenuBar(window)
        self.menu_bar.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.menu_bar)
        layout.addStretch(1)

        self.minimize_button = _WindowControlButton(
            IconName.WINDOW_MINIMIZE,
            "最小化",
            parent=self,
        )
        self.maximize_button = _WindowControlButton(
            IconName.WINDOW_MAXIMIZE,
            "最大化",
            parent=self,
        )
        self.close_button = _WindowControlButton(
            IconName.CLOSE,
            "关闭",
            parent=self,
            close_button=True,
        )
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self._drag_targets = {self.icon_label}
        window.installEventFilter(self)
        for target in self._drag_targets:
            target.installEventFilter(self)
        self._resize_controller = FramelessResizeController(window)
        self.refresh_icon()
        self.refresh_window_state()
        self.refresh_window_shape()

    def refresh_icon(self) -> None:
        application = QApplication.instance()
        icon = application.windowIcon() if application is not None else self._window.windowIcon()
        self.icon_label.setVisible(not icon.isNull())
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(20, 20))

    def refresh_window_state(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.set_icon_name(
            IconName.WINDOW_RESTORE if maximized else IconName.WINDOW_MAXIMIZE
        )
        description = "还原" if maximized else "最大化"
        self.maximize_button.setToolTip(description)
        self.maximize_button.setAccessibleName(description)

    def refresh_window_shape(self) -> None:
        """Switch the styled surface between restored and edge-to-edge modes."""
        rounded = not (self._window.isMaximized() or self._window.isFullScreen())
        corner_mode = "rounded" if rounded else "square"
        self._window.clearMask()
        self._set_corner_mode(self, corner_mode)
        self._set_corner_mode(self.close_button, corner_mode)
        window_surface = self._window.findChild(QFrame, "applicationWindowSurface")
        if window_surface is not None:
            self._set_corner_mode(window_surface, corner_mode)
        status_bar = self._window.findChild(QFrame, "workbenchStatusBar")
        if status_bar is not None:
            self._set_corner_mode(status_bar, corner_mode)

    @staticmethod
    def _set_corner_mode(widget: QWidget, corner_mode: str) -> None:
        if widget.property("windowCorners") == corner_mode:
            return
        widget.setProperty("windowCorners", corner_mode)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.refresh_window_state()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self._drag_targets and isinstance(event, QMouseEvent):
            if event.type() is QEvent.Type.MouseButtonDblClick:
                self.mouseDoubleClickEvent(event)
                return event.isAccepted()
            if event.type() is QEvent.Type.MouseButtonPress:
                self.mousePressEvent(event)
                return event.isAccepted()
            if event.type() is QEvent.Type.MouseMove:
                self.mouseMoveEvent(event)
                return event.isAccepted()
            if event.type() is QEvent.Type.MouseButtonRelease:
                self.mouseReleaseEvent(event)
                return event.isAccepted()
        if watched is self._window:
            if event.type() is QEvent.Type.WindowStateChange:
                QTimer.singleShot(0, self.refresh_window_state)
                QTimer.singleShot(0, self.refresh_window_shape)
            elif event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
                QTimer.singleShot(0, self.refresh_window_shape)
            elif event.type() in {
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            }:
                QTimer.singleShot(0, self.refresh_icon)
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._drag_offset = (
            event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        )
        handle = self._window.windowHandle()
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

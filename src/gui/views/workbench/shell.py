"""Canvas-first workbench shell with optional side and bottom panels."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...view_models.models import DeviceViewState


ACTIVITY_BAR_WIDTH = 52
SPLITTER_HIT_WIDTH = 7
SIDE_BAR_DEFAULT_WIDTH = 280
SIDE_BAR_MINIMUM_WIDTH = 220
SIDE_BAR_MAXIMUM_WIDTH = 440
BOTTOM_PANEL_DEFAULT_HEIGHT = 220
BOTTOM_PANEL_MINIMUM_HEIGHT = 120


@dataclass(frozen=True, slots=True)
class WorkbenchPage:
    key: str
    title: str
    symbol: str
    widget: QWidget


class _ThinLineSplitterHandle(QSplitterHandle):
    """Keep a generous resize target while painting only a one-pixel line."""

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setPen(QPen(self.palette().mid().color(), 1.0))
        if self.orientation() is Qt.Orientation.Horizontal:
            x = self.rect().center().x()
            painter.drawLine(x, self.rect().top(), x, self.rect().bottom())
        else:
            y = self.rect().center().y()
            painter.drawLine(self.rect().left(), y, self.rect().right(), y)


class _ThinLineSplitter(QSplitter):
    def __init__(
        self,
        orientation: Qt.Orientation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(SPLITTER_HIT_WIDTH)
        self.setChildrenCollapsible(False)

    def createHandle(self) -> QSplitterHandle:  # noqa: N802
        return _ThinLineSplitterHandle(self.orientation(), self)


class _ActivityBar(QFrame):
    page_requested = Signal(str)

    def __init__(
        self,
        pages: tuple[WorkbenchPage, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchActivityBar")
        self.setFixedWidth(ACTIVITY_BAR_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(4)
        self.buttons: dict[str, QToolButton] = {}
        for page in pages:
            button = QToolButton()
            button.setObjectName("activityButton")
            button.setText(page.symbol)
            button.setToolTip(page.title)
            button.setAccessibleName(page.title)
            button.setCheckable(True)
            button.setFixedSize(44, 44)
            button.clicked.connect(
                lambda _checked=False, key=page.key: self.page_requested.emit(key)
            )
            layout.addWidget(button)
            self.buttons[page.key] = button
        layout.addStretch(1)

    def render_active_page(self, page_key: str | None) -> None:
        for key, button in self.buttons.items():
            button.setChecked(key == page_key)


class WorkbenchStatusBar(QFrame):
    panel_requested = Signal(str)

    def __init__(
        self,
        pages: tuple[WorkbenchPage, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchStatusBar")
        self.setFixedHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)
        self.device_summary = QLabel("● 设备状态未知")
        self.device_summary.setAccessibleName("设备状态摘要")
        layout.addWidget(self.device_summary)
        self.message_label = QLabel()
        self.message_label.setObjectName("workbenchStatusMessage")
        layout.addWidget(self.message_label, stretch=1)
        self.buttons: dict[str, QToolButton] = {}
        for page in pages:
            button = QToolButton()
            button.setObjectName("statusPanelButton")
            button.setText(f"{page.symbol} {page.title}")
            button.setToolTip(f"显示或隐藏{page.title}")
            button.setAccessibleName(f"显示或隐藏{page.title}")
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.clicked.connect(
                lambda _checked=False, key=page.key: self.panel_requested.emit(key)
            )
            layout.addWidget(button)
            self.buttons[page.key] = button
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message_label.clear)

    def show_message(self, message: str, timeout_ms: int = 5000) -> None:
        self.message_label.setText(message)
        self._message_timer.start(timeout_ms)

    def render_active_panel(self, page_key: str | None) -> None:
        for key, button in self.buttons.items():
            button.setChecked(key == page_key)

    def render_device_state(self, state: DeviceViewState) -> None:
        ready_count = sum(
            (
                state.robot_ready,
                state.body_ready,
                state.pipette_ready,
                state.relay_ready,
            )
        )
        self.device_summary.setText(f"● 设备 {ready_count}/4")
        role = "success" if ready_count == 4 else "danger"
        self.device_summary.setProperty("themeRole", role)
        style = self.device_summary.style()
        style.unpolish(self.device_summary)
        style.polish(self.device_summary)


class WorkbenchView(QWidget):
    """Own the presentation-only layout state for the main workbench."""

    def __init__(
        self,
        *,
        side_pages: tuple[WorkbenchPage, ...],
        editor: QWidget,
        bottom_pages: tuple[WorkbenchPage, ...],
        parent: QWidget | None = None,
    ) -> None:
        if not side_pages:
            raise ValueError("workbench requires at least one side page")
        if not bottom_pages:
            raise ValueError("workbench requires at least one bottom page")
        super().__init__(parent)
        self._side_pages = {page.key: page for page in side_pages}
        self._bottom_pages = {page.key: page for page in bottom_pages}
        self._active_side_page: str | None = side_pages[0].key
        self._active_bottom_page: str | None = None
        self._last_side_width = SIDE_BAR_DEFAULT_WIDTH
        self._last_bottom_height = BOTTOM_PANEL_DEFAULT_HEIGHT

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.activity_bar = _ActivityBar(side_pages)
        self.activity_bar.page_requested.connect(self.toggle_side_page)
        body_layout.addWidget(self.activity_bar)

        self.side_stack = QStackedWidget()
        self.side_stack.setObjectName("workbenchSideBar")
        self.side_stack.setMinimumWidth(SIDE_BAR_MINIMUM_WIDTH)
        self.side_stack.setMaximumWidth(SIDE_BAR_MAXIMUM_WIDTH)
        for page in side_pages:
            self.side_stack.addWidget(page.widget)

        self.bottom_stack = QStackedWidget()
        self.bottom_stack.setObjectName("workbenchBottomPanel")
        self.bottom_stack.setMinimumHeight(BOTTOM_PANEL_MINIMUM_HEIGHT)
        for page in bottom_pages:
            self.bottom_stack.addWidget(page.widget)
        self.bottom_stack.hide()

        self.bottom_splitter = _ThinLineSplitter(Qt.Orientation.Vertical)
        self.bottom_splitter.setObjectName("workbenchBottomSplitter")
        self.bottom_splitter.addWidget(editor)
        self.bottom_splitter.addWidget(self.bottom_stack)
        self.bottom_splitter.setStretchFactor(0, 1)
        self.bottom_splitter.setStretchFactor(1, 0)

        self.side_splitter = _ThinLineSplitter(Qt.Orientation.Horizontal)
        self.side_splitter.setObjectName("workbenchSideSplitter")
        self.side_splitter.addWidget(self.side_stack)
        self.side_splitter.addWidget(self.bottom_splitter)
        self.side_splitter.setStretchFactor(0, 0)
        self.side_splitter.setStretchFactor(1, 1)
        self.side_splitter.setSizes((SIDE_BAR_DEFAULT_WIDTH, 640))
        self.side_splitter.splitterMoved.connect(self._remember_side_width)
        self.bottom_splitter.splitterMoved.connect(self._remember_bottom_height)
        body_layout.addWidget(self.side_splitter, stretch=1)
        root.addWidget(body, stretch=1)

        self.status_bar = WorkbenchStatusBar(bottom_pages)
        self.status_bar.panel_requested.connect(self.toggle_bottom_page)
        root.addWidget(self.status_bar)
        self._show_side_page(side_pages[0].key)

    @property
    def active_side_page(self) -> str | None:
        return self._active_side_page

    @property
    def active_bottom_page(self) -> str | None:
        return self._active_bottom_page

    def toggle_side_page(self, page_key: str) -> None:
        self._require_page(page_key, self._side_pages, "side")
        if self._active_side_page == page_key and self.side_stack.isVisible():
            self._last_side_width = max(
                SIDE_BAR_MINIMUM_WIDTH,
                self.side_splitter.sizes()[0],
            )
            self.side_stack.hide()
            self._active_side_page = None
            self.activity_bar.render_active_page(None)
            return
        self._show_side_page(page_key)

    def toggle_bottom_page(self, page_key: str) -> None:
        self._require_page(page_key, self._bottom_pages, "bottom")
        if self._active_bottom_page == page_key and self.bottom_stack.isVisible():
            self._last_bottom_height = max(
                BOTTOM_PANEL_MINIMUM_HEIGHT,
                self.bottom_splitter.sizes()[1],
            )
            self.bottom_stack.hide()
            self._active_bottom_page = None
            self.status_bar.render_active_panel(None)
            return
        page = self._bottom_pages[page_key]
        self.bottom_stack.setCurrentWidget(page.widget)
        self.bottom_stack.show()
        available = max(1, sum(self.bottom_splitter.sizes()))
        height = min(self._last_bottom_height, max(1, available // 2))
        self.bottom_splitter.setSizes((available - height, height))
        self._active_bottom_page = page_key
        self.status_bar.render_active_panel(page_key)

    def _show_side_page(self, page_key: str) -> None:
        page = self._side_pages[page_key]
        self.side_stack.setCurrentWidget(page.widget)
        self.side_stack.show()
        available = max(1, sum(self.side_splitter.sizes()))
        width = min(
            max(self._last_side_width, SIDE_BAR_MINIMUM_WIDTH),
            SIDE_BAR_MAXIMUM_WIDTH,
            max(1, available // 2),
        )
        self.side_splitter.setSizes((width, available - width))
        self._active_side_page = page_key
        self.activity_bar.render_active_page(page_key)

    def _remember_side_width(self, _position: int, _index: int) -> None:
        if self.side_stack.isVisible():
            self._last_side_width = self.side_splitter.sizes()[0]

    def _remember_bottom_height(self, _position: int, _index: int) -> None:
        if self.bottom_stack.isVisible():
            self._last_bottom_height = self.bottom_splitter.sizes()[1]

    @staticmethod
    def _require_page(
        page_key: str,
        pages: dict[str, WorkbenchPage],
        region: str,
    ) -> None:
        if page_key not in pages:
            raise KeyError(f"unknown {region} workbench page: {page_key}")

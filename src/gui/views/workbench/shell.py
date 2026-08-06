"""Canvas-first workbench shell with optional side and bottom panels."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
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
from ...icons import IconName, themed_icon
from ...workbench_layout import (
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WorkbenchLayoutState,
    WorkbenchLayoutStore,
)


ACTIVITY_BAR_WIDTH = 52
SPLITTER_HIT_WIDTH = 7
SIDE_BAR_DEFAULT_WIDTH = 280
SIDE_BAR_MINIMUM_WIDTH = 220
SIDE_BAR_MAXIMUM_WIDTH = 440
BOTTOM_PANEL_DEFAULT_HEIGHT = 220
BOTTOM_PANEL_MINIMUM_HEIGHT = 120
BOTTOM_PANEL_MAXIMUM_HEIGHT = 600
LAYOUT_SAVE_DELAY_MS = 250
STATUS_ICON_COLOR = QColor("#ffffff")


@dataclass(frozen=True, slots=True)
class WorkbenchPage:
    key: str
    title: str
    icon: IconName
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
            button.setToolTip(page.title)
            button.setAccessibleName(page.title)
            button.setAccessibleDescription(f"显示或收起{page.title}资源页")
            button.setCheckable(True)
            button.setFixedSize(44, 44)
            button.setIconSize(QSize(22, 22))
            button.clicked.connect(
                lambda _checked=False, key=page.key: self.page_requested.emit(key)
            )
            layout.addWidget(button)
            self.buttons[page.key] = button
        self._pages = {page.key: page for page in pages}
        layout.addStretch(1)
        self._refresh_icons()

    def render_active_page(self, page_key: str | None) -> None:
        for key, button in self.buttons.items():
            button.setChecked(key == page_key)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self._refresh_icons()

    def _refresh_icons(self) -> None:
        for key, button in self.buttons.items():
            button.setIcon(themed_icon(button, self._pages[key].icon, size=22))


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
            button.setText(page.title)
            button.setToolTip(f"显示或隐藏{page.title}")
            button.setAccessibleName(f"显示或隐藏{page.title}")
            button.setAccessibleDescription(f"切换底部{page.title}面板")
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.setIconSize(QSize(16, 16))
            button.clicked.connect(
                lambda _checked=False, key=page.key: self.panel_requested.emit(key)
            )
            layout.addWidget(button)
            self.buttons[page.key] = button
        self._pages = {page.key: page for page in pages}
        self._refresh_icons()
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message_label.clear)

    def show_message(self, message: str, timeout_ms: int = 5000) -> None:
        self.message_label.setText(message)
        self._message_timer.start(timeout_ms)

    def render_active_panel(self, page_key: str | None) -> None:
        for key, button in self.buttons.items():
            button.setChecked(key == page_key)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self._refresh_icons()

    def _refresh_icons(self) -> None:
        for key, button in self.buttons.items():
            button.setIcon(
                themed_icon(
                    button,
                    self._pages[key].icon,
                    size=16,
                    color=STATUS_ICON_COLOR,
                )
            )

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
        layout_store: WorkbenchLayoutStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        if not side_pages:
            raise ValueError("workbench requires at least one side page")
        if not bottom_pages:
            raise ValueError("workbench requires at least one bottom page")
        super().__init__(parent)
        self._side_pages = {page.key: page for page in side_pages}
        self._bottom_pages = {page.key: page for page in bottom_pages}
        self._default_side_page = side_pages[0].key
        self._default_bottom_page = bottom_pages[0].key
        self._selected_side_page = self._default_side_page
        self._selected_bottom_page = self._default_bottom_page
        self._active_side_page: str | None = side_pages[0].key
        self._active_bottom_page: str | None = None
        self._last_side_width = SIDE_BAR_DEFAULT_WIDTH
        self._last_bottom_height = BOTTOM_PANEL_DEFAULT_HEIGHT
        self._layout_store = layout_store
        self.layout_recovery_reason: str | None = None
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.timeout.connect(self.persist_layout)

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
        self.bottom_stack.setMaximumHeight(BOTTOM_PANEL_MAXIMUM_HEIGHT)
        for page in bottom_pages:
            self.bottom_stack.addWidget(page.widget)
        self.bottom_stack.hide()

        self.bottom_splitter = _ThinLineSplitter(Qt.Orientation.Vertical)
        self.bottom_splitter.setObjectName("workbenchBottomSplitter")
        self.bottom_splitter.addWidget(editor)
        self.bottom_splitter.addWidget(self.bottom_stack)
        self.bottom_splitter.setStretchFactor(0, 1)
        self.bottom_splitter.setStretchFactor(1, 0)
        self.bottom_splitter.setSizes((640, BOTTOM_PANEL_DEFAULT_HEIGHT))

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
        self._restore_layout()

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
            self._schedule_layout_save()
            return
        self._show_side_page(page_key)

    def toggle_last_side_page(self) -> None:
        self.toggle_side_page(self._selected_side_page)

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
            self._schedule_layout_save()
            return
        self._show_bottom_page(page_key)

    def reset_layout(self) -> None:
        self._selected_side_page = self._default_side_page
        self._selected_bottom_page = self._default_bottom_page
        self._last_side_width = SIDE_BAR_DEFAULT_WIDTH
        self._last_bottom_height = BOTTOM_PANEL_DEFAULT_HEIGHT
        self.bottom_stack.hide()
        self._active_bottom_page = None
        self.status_bar.render_active_panel(None)
        self._show_side_page(self._default_side_page)
        self.persist_layout()

    def persist_layout(self) -> None:
        if self._layout_store is None:
            return
        self._layout_save_timer.stop()
        self._layout_store.save(self.layout_state())

    def layout_state(self) -> WorkbenchLayoutState:
        return WorkbenchLayoutState(
            schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
            side_page=self._selected_side_page,
            side_visible=self._active_side_page is not None,
            side_width=self._last_side_width,
            bottom_page=self._selected_bottom_page,
            bottom_visible=self._active_bottom_page is not None,
            bottom_height=self._last_bottom_height,
        )

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
        self._selected_side_page = page_key
        self._active_side_page = page_key
        self.activity_bar.render_active_page(page_key)
        self._schedule_layout_save()

    def _show_bottom_page(self, page_key: str) -> None:
        page = self._bottom_pages[page_key]
        self.bottom_stack.setCurrentWidget(page.widget)
        self.bottom_stack.show()
        available = max(1, sum(self.bottom_splitter.sizes()))
        height = min(
            max(self._last_bottom_height, BOTTOM_PANEL_MINIMUM_HEIGHT),
            max(1, available // 2),
        )
        self.bottom_splitter.setSizes((available - height, height))
        self._selected_bottom_page = page_key
        self._active_bottom_page = page_key
        self.status_bar.render_active_panel(page_key)
        self._schedule_layout_save()

    def _remember_side_width(self, _position: int, _index: int) -> None:
        if self.side_stack.isVisible():
            self._last_side_width = min(
                max(self.side_splitter.sizes()[0], SIDE_BAR_MINIMUM_WIDTH),
                SIDE_BAR_MAXIMUM_WIDTH,
            )
            self._schedule_layout_save()

    def _remember_bottom_height(self, _position: int, _index: int) -> None:
        if self.bottom_stack.isVisible():
            self._last_bottom_height = max(
                min(
                    self.bottom_splitter.sizes()[1],
                    BOTTOM_PANEL_MAXIMUM_HEIGHT,
                ),
                BOTTOM_PANEL_MINIMUM_HEIGHT,
            )
            self._schedule_layout_save()

    def _restore_layout(self) -> None:
        if self._layout_store is None:
            self._show_side_page(self._default_side_page)
            return
        result = self._layout_store.load()
        self.layout_recovery_reason = result.reason if result.recovered else None
        state = result.state
        if state is None:
            self._show_side_page(self._default_side_page)
            return
        if (
            state.side_page not in self._side_pages
            or state.bottom_page not in self._bottom_pages
        ):
            self.layout_recovery_reason = "布局偏好引用了已不存在的页面"
            self._layout_store.clear()
            self._show_side_page(self._default_side_page)
            return
        self._selected_side_page = state.side_page
        self._selected_bottom_page = state.bottom_page
        self._last_side_width = min(
            max(state.side_width, SIDE_BAR_MINIMUM_WIDTH),
            SIDE_BAR_MAXIMUM_WIDTH,
        )
        self._last_bottom_height = max(
            min(state.bottom_height, BOTTOM_PANEL_MAXIMUM_HEIGHT),
            BOTTOM_PANEL_MINIMUM_HEIGHT,
        )
        if state.side_visible:
            self._show_side_page(state.side_page)
        else:
            self.side_stack.setCurrentWidget(self._side_pages[state.side_page].widget)
            self.side_stack.hide()
            self._active_side_page = None
            self.activity_bar.render_active_page(None)
        if state.bottom_visible:
            self._show_bottom_page(state.bottom_page)
        else:
            self.bottom_stack.setCurrentWidget(
                self._bottom_pages[state.bottom_page].widget
            )
            self.bottom_stack.hide()
            self._active_bottom_page = None
            self.status_bar.render_active_panel(None)

    def _schedule_layout_save(self) -> None:
        if self._layout_store is not None:
            self._layout_save_timer.start(LAYOUT_SAVE_DELAY_MS)

    @staticmethod
    def _require_page(
        page_key: str,
        pages: dict[str, WorkbenchPage],
        region: str,
    ) -> None:
        if page_key not in pages:
            raise KeyError(f"unknown {region} workbench page: {page_key}")

"""Canvas-first workbench shell with side pages and status-bar detail popovers."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
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
from ...toolbars import IconToolButton, icon_foreground
from ...workbench_layout import (
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WorkbenchLayoutState,
    WorkbenchLayoutStore,
)


ACTIVITY_BAR_WIDTH = 52
ACTIVITY_BUTTON_SIZE = 44
ACTIVITY_ICON_SIZE = 20
STATUS_BUTTON_SIZE = 28
STATUS_ICON_SIZE = 16
SPLITTER_HIT_WIDTH = 7
SIDE_BAR_DEFAULT_WIDTH = 280
SIDE_BAR_MINIMUM_WIDTH = 220
SIDE_BAR_MAXIMUM_WIDTH = 440
DETAIL_PANEL_DEFAULT_WIDTH = 460
DETAIL_PANEL_DEFAULT_HEIGHT = 300
DETAIL_PANEL_MARGIN = 8
LAYOUT_SAVE_DELAY_MS = 250


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
        color = (
            self.palette().highlight().color()
            if self.underMouse()
            else self.palette().mid().color()
        )
        color.setAlpha(150 if self.underMouse() else 72)
        painter.setPen(QPen(color, 1.0))
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
            button.setAccessibleName(page.title)
            button.setAccessibleDescription(f"显示或收起{page.title}资源页")
            button.setToolTip(page.title)
            button.setCheckable(True)
            button.setFixedSize(ACTIVITY_BUTTON_SIZE, ACTIVITY_BUTTON_SIZE)
            button.setIconSize(QSize(ACTIVITY_ICON_SIZE, ACTIVITY_ICON_SIZE))
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
            button.setIcon(
                themed_icon(
                    button,
                    self._pages[key].icon,
                    size=ACTIVITY_ICON_SIZE,
                    color=icon_foreground(button),
                )
            )


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
        self.message_label = QLabel()
        self.message_label.setObjectName("workbenchStatusMessage")
        self.message_label.setAccessibleName("状态消息")
        layout.addWidget(self.message_label, stretch=1)
        self.buttons: dict[str, QToolButton] = {}
        for page in pages:
            button = QToolButton()
            button.setObjectName("statusPanelButton")
            button.setText("")
            button.setToolTip(f"显示或隐藏{page.title}")
            button.setAccessibleName(f"显示或隐藏{page.title}")
            button.setAccessibleDescription(f"切换状态栏{page.title}详情浮层")
            button.setCheckable(True)
            button.setFixedSize(STATUS_BUTTON_SIZE, STATUS_BUTTON_SIZE)
            button.setIconSize(QSize(STATUS_ICON_SIZE, STATUS_ICON_SIZE))
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
            QEvent.Type.DynamicPropertyChange,
            QEvent.Type.EnabledChange,
        }:
            self._refresh_icons()

    def _refresh_icons(self) -> None:
        for key, button in self.buttons.items():
            button.setIcon(
                themed_icon(
                    button,
                    self._pages[key].icon,
                    size=STATUS_ICON_SIZE,
                    color=icon_foreground(button),
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
        device_button = self.buttons["devices"]
        is_ready = ready_count == 4
        role = "statusSuccess" if is_ready else "statusDanger"
        device_button.setProperty("themeRole", role)
        summary = f"设备状态：{ready_count}/4 可用"
        device_button.setToolTip(f"{summary}（打开设备详情）")
        device_button.setAccessibleName(summary)
        style = device_button.style()
        style.unpolish(device_button)
        style.polish(device_button)
        self._refresh_icons()


class _FloatingDetailPanel(QFrame):
    """Own one reusable detail-page stack without participating in body layout."""

    close_requested = Signal()

    def __init__(
        self,
        pages: tuple[WorkbenchPage, ...],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchFloatingPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 88))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title_label = QLabel()
        self.title_label.setObjectName("workbenchFloatingPanelTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)
        close_button = IconToolButton(
            IconName.CLOSE,
            "关闭详情",
            callback=self.close_requested.emit,
            parent=self,
            hit_size=28,
            icon_size=14,
        )
        header.addWidget(close_button)
        layout.addLayout(header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("workbenchDetailStack")
        self._pages = {page.key: page for page in pages}
        for page in pages:
            self.stack.addWidget(page.widget)
        layout.addWidget(self.stack, stretch=1)
        self.hide()

    def select_page(self, page_key: str) -> None:
        page = self._pages[page_key]
        self.title_label.setText(page.title)
        self.stack.setCurrentWidget(page.widget)


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
        self._outside_click_filter_installed = False
        self._last_side_width = SIDE_BAR_DEFAULT_WIDTH
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

        self.side_splitter = _ThinLineSplitter(Qt.Orientation.Horizontal)
        self.side_splitter.setObjectName("workbenchSideSplitter")
        self.side_splitter.addWidget(self.side_stack)
        self.side_splitter.addWidget(editor)
        self.side_splitter.setStretchFactor(0, 0)
        self.side_splitter.setStretchFactor(1, 1)
        self.side_splitter.setSizes((SIDE_BAR_DEFAULT_WIDTH, 640))
        self.side_splitter.splitterMoved.connect(self._remember_side_width)
        body_layout.addWidget(self.side_splitter, stretch=1)
        root.addWidget(body, stretch=1)

        self.status_bar = WorkbenchStatusBar(bottom_pages)
        self.status_bar.panel_requested.connect(self.toggle_bottom_page)
        root.addWidget(self.status_bar)
        self.detail_panel = _FloatingDetailPanel(bottom_pages, self)
        self.detail_panel.close_requested.connect(self.close_bottom_page)
        self.bottom_stack = self.detail_panel.stack
        self._close_panel_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape),
            self,
        )
        self._close_panel_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._close_panel_shortcut.activated.connect(self.close_bottom_page)
        self._close_panel_shortcut.setEnabled(False)
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
        if self._active_bottom_page == page_key:
            self.close_bottom_page()
            return
        self._show_bottom_page(page_key)

    def close_bottom_page(self) -> None:
        self._set_outside_click_filter_enabled(False)
        self._close_panel_shortcut.setEnabled(False)
        if self._active_bottom_page is None:
            return
        self.detail_panel.hide()
        self._active_bottom_page = None
        self.status_bar.render_active_panel(None)
        self._schedule_layout_save()

    def reset_layout(self) -> None:
        self._selected_side_page = self._default_side_page
        self._selected_bottom_page = self._default_bottom_page
        self._last_side_width = SIDE_BAR_DEFAULT_WIDTH
        self.detail_panel.hide()
        self._set_outside_click_filter_enabled(False)
        self._close_panel_shortcut.setEnabled(False)
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
            panel_page=self._selected_bottom_page,
            panel_visible=self._active_bottom_page is not None,
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
        self.detail_panel.select_page(page_key)
        self._position_detail_panel()
        self.detail_panel.show()
        self.detail_panel.raise_()
        self._set_outside_click_filter_enabled(True)
        self._close_panel_shortcut.setEnabled(True)
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
        if state.side_page not in self._side_pages or state.panel_page not in self._bottom_pages:
            self.layout_recovery_reason = "布局偏好引用了已不存在的页面"
            self._layout_store.clear()
            self._show_side_page(self._default_side_page)
            return
        self._selected_side_page = state.side_page
        self._selected_bottom_page = state.panel_page
        self._last_side_width = min(
            max(state.side_width, SIDE_BAR_MINIMUM_WIDTH),
            SIDE_BAR_MAXIMUM_WIDTH,
        )
        if state.side_visible:
            self._show_side_page(state.side_page)
        else:
            self.side_stack.setCurrentWidget(self._side_pages[state.side_page].widget)
            self.side_stack.hide()
            self._active_side_page = None
            self.activity_bar.render_active_page(None)
        if state.panel_visible:
            self._show_bottom_page(state.panel_page)
        else:
            self.detail_panel.select_page(state.panel_page)
            self.detail_panel.hide()
            self._close_panel_shortcut.setEnabled(False)
            self._active_bottom_page = None
            self.status_bar.render_active_panel(None)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_detail_panel()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._set_outside_click_filter_enabled(False)
        super().hideEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            self._active_bottom_page is not None
            and event.type() is QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() is Qt.MouseButton.LeftButton
            and self._is_workbench_outside_detail_click(
                QApplication.widgetAt(event.globalPosition().toPoint())
            )
        ):
            self.close_bottom_page()
        return super().eventFilter(watched, event)

    def _is_workbench_outside_detail_click(
        self,
        target: QWidget | None,
    ) -> bool:
        if target is None:
            return False
        if not self._is_within(target, self):
            return False
        return not (
            self._is_within(target, self.detail_panel)
            or self._is_within(target, self.status_bar)
        )

    @staticmethod
    def _is_within(target: QWidget, container: QWidget) -> bool:
        current: QWidget | None = target
        while current is not None:
            if current is container:
                return True
            current = current.parentWidget()
        return False

    def _set_outside_click_filter_enabled(self, enabled: bool) -> None:
        application = QApplication.instance()
        if application is None or enabled == self._outside_click_filter_installed:
            return
        if enabled:
            application.installEventFilter(self)
        else:
            application.removeEventFilter(self)
        self._outside_click_filter_installed = enabled

    def _position_detail_panel(self) -> None:
        available_width = max(1, self.width() - (2 * DETAIL_PANEL_MARGIN))
        available_height = max(
            1,
            self.height() - self.status_bar.height() - (2 * DETAIL_PANEL_MARGIN),
        )
        width = min(DETAIL_PANEL_DEFAULT_WIDTH, available_width)
        height = min(DETAIL_PANEL_DEFAULT_HEIGHT, available_height)
        x = max(DETAIL_PANEL_MARGIN, self.width() - DETAIL_PANEL_MARGIN - width)
        y = max(
            DETAIL_PANEL_MARGIN,
            self.height() - self.status_bar.height() - DETAIL_PANEL_MARGIN - height,
        )
        self.detail_panel.setGeometry(x, y, width, height)

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

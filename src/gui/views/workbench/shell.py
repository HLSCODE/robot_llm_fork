"""Canvas-first workbench shell with side pages and status-bar detail popovers."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QHideEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollBar,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...bridges.notifications import GuiNotification, GuiNotificationLevel
from ...view_models.models import DeviceViewState
from ...icons import IconName, themed_icon
from ...toolbars import IconToolButton, PaneHeader, icon_foreground
from ..log_widget import LogFilter
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
STATUS_PROBLEM_CONTENT_SPACING = 4
STATUS_PROBLEM_HORIZONTAL_PADDING = 3
NOTIFICATION_TOAST_TIMEOUT_MS = 5000
NOTIFICATION_TOAST_MAXIMUM_WIDTH = 380
NOTIFICATION_TOAST_MARGIN = 12
NOTIFICATION_TOAST_SUMMARY_LIMIT = 180
SPLITTER_HIT_WIDTH = 8
WORKBENCH_CARD_MARGIN = 8
WORKBENCH_CARD_TOP_MARGIN = 6
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
    """Provide an invisible resize target in the gap between content cards."""

    def __init__(
        self,
        orientation: Qt.Orientation,
        parent: QSplitter,
    ) -> None:
        super().__init__(orientation, parent)
        self._is_dragging = False

    @property
    def indicator_visible(self) -> bool:
        return self.underMouse() or self._is_dragging

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() is Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if not self.indicator_visible:
            return
        color = self.palette().highlight().color()
        color.setAlpha(220)
        painter = QPainter(self)
        painter.setPen(QPen(color, 1.0))
        if self.orientation() is Qt.Orientation.Horizontal:
            x = self.rect().center().x()
            painter.drawLine(x, self.rect().top() + 6, x, self.rect().bottom() - 6)
        else:
            y = self.rect().center().y()
            painter.drawLine(self.rect().left() + 6, y, self.rect().right() - 6, y)


class _ThinLineSplitter(QSplitter):
    def __init__(
        self,
        orientation: Qt.Orientation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
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


class _StatusProblemButton(QToolButton):
    """Compact problem counter with platform-independent content geometry."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count_text = "0"
        self.setObjectName("statusProblemButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFixedHeight(STATUS_BUTTON_SIZE)
        self.setProperty("problemLabel", label)

        content_layout = QHBoxLayout(self)
        content_layout.setContentsMargins(
            STATUS_PROBLEM_HORIZONTAL_PADDING,
            0,
            STATUS_PROBLEM_HORIZONTAL_PADDING,
            0,
        )
        content_layout.setSpacing(STATUS_PROBLEM_CONTENT_SPACING)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("statusProblemIcon")
        self.icon_label.setFixedSize(STATUS_ICON_SIZE, STATUS_ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content_layout.addWidget(self.icon_label)

        self.count_label = QLabel("0", self)
        self.count_label.setObjectName("statusProblemCount")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content_layout.addWidget(self.count_label)

        self.set_count(0)

    def set_count(self, count: int) -> None:
        text = str(max(0, count))
        self._count_text = text
        # Keep QAbstractButton's native text empty. Some Windows styles paint it
        # even in IconOnly mode, underneath the explicit content layout.
        super().setText("")
        self.count_label.setText(text)
        self._fit_to_content()

    def text(self) -> str:
        """Return the visible counter text without enabling native painting."""
        return self._count_text

    def set_status_icon(self, icon: QIcon) -> None:
        self.icon_label.setPixmap(icon.pixmap(STATUS_ICON_SIZE, STATUS_ICON_SIZE))
        self._fit_to_content()

    def refresh_content_geometry(self) -> None:
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        text_width = self.count_label.fontMetrics().horizontalAdvance(
            self.count_label.text()
        )
        content_width = (
            STATUS_ICON_SIZE
            + STATUS_PROBLEM_CONTENT_SPACING
            + text_width
            + (2 * STATUS_PROBLEM_HORIZONTAL_PADDING)
        )
        self.setFixedWidth(max(STATUS_BUTTON_SIZE, content_width))


class WorkbenchStatusBar(QFrame):
    panel_requested = Signal(str)
    log_filter_requested = Signal(object)

    def __init__(
        self,
        pages: tuple[WorkbenchPage, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchStatusBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)
        self.buttons: dict[str, QToolButton] = {}
        self.log_problem_buttons: dict[LogFilter, _StatusProblemButton] = {}
        self._log_problem_icons: dict[LogFilter, IconName] = {}
        self._active_log_filter = LogFilter.ALL
        left_pages = tuple(page for page in pages if page.key in {"devices", "poses"})
        right_pages = tuple(page for page in pages if page.key not in {"devices", "poses"})
        for page in left_pages:
            self._add_panel_button(layout, page)
        layout.addStretch(1)
        for page in right_pages:
            if page.key == "logs":
                self._add_log_problem_button(
                    layout,
                    LogFilter.ERRORS,
                    IconName.PROBLEM_ERROR,
                    "错误",
                )
                self._add_log_problem_button(
                    layout,
                    LogFilter.WARNINGS,
                    IconName.PROBLEM_WARNING,
                    "警告",
                )
            self._add_panel_button(layout, page)
        self._pages = {page.key: page for page in pages}
        self.render_log_counts(0, 0)
        self.setFixedHeight(32)

    @property
    def active_log_filter(self) -> LogFilter:
        return self._active_log_filter

    def _add_panel_button(self, layout: QHBoxLayout, page: WorkbenchPage) -> None:
        button = QToolButton()
        button.setObjectName("statusPanelButton")
        button.setText("")
        button.setToolTip(f"显示或隐藏{page.title}")
        button.setAccessibleName(f"显示或隐藏{page.title}")
        button.setAccessibleDescription(f"切换状态栏{page.title}详情浮层")
        button.setCheckable(True)
        button.setFixedSize(STATUS_BUTTON_SIZE, STATUS_BUTTON_SIZE)
        button.setIconSize(QSize(STATUS_ICON_SIZE, STATUS_ICON_SIZE))
        if page.key == "logs":
            button.clicked.connect(
                lambda _checked=False: self.log_filter_requested.emit(LogFilter.ALL)
            )
        else:
            button.clicked.connect(
                lambda _checked=False, key=page.key: self.panel_requested.emit(key)
            )
        layout.addWidget(button)
        self.buttons[page.key] = button

    def _add_log_problem_button(
        self,
        layout: QHBoxLayout,
        log_filter: LogFilter,
        icon: IconName,
        label: str,
    ) -> None:
        button = _StatusProblemButton(label)
        button.clicked.connect(
            lambda _checked=False, target=log_filter: self.log_filter_requested.emit(
                target
            )
        )
        layout.addWidget(button)
        self.log_problem_buttons[log_filter] = button
        self._log_problem_icons[log_filter] = icon

    def render_active_panel(self, page_key: str | None) -> None:
        for key, button in self.buttons.items():
            button.setChecked(
                key == page_key
                and (key != "logs" or self._active_log_filter is LogFilter.ALL)
            )
        for log_filter, button in self.log_problem_buttons.items():
            button.setChecked(
                page_key == "logs" and self._active_log_filter is log_filter
            )

    def render_log_filter(self, log_filter: LogFilter) -> None:
        self._active_log_filter = log_filter

    def render_log_counts(self, error_count: int, warning_count: int) -> None:
        counts = {
            LogFilter.ERRORS: max(0, error_count),
            LogFilter.WARNINGS: max(0, warning_count),
        }
        roles = {
            LogFilter.ERRORS: "statusDanger",
            LogFilter.WARNINGS: "statusWarning",
        }
        for log_filter, count in counts.items():
            button = self.log_problem_buttons.get(log_filter)
            if button is None:
                continue
            label = str(button.property("problemLabel"))
            button.set_count(count)
            button.setToolTip(f"{label}：{count} 条（筛选运行日志）")
            button.setAccessibleName(f"{label}日志 {count} 条")
            role = roles[log_filter] if count else "statusMuted"
            button.setProperty("themeRole", role)
            button.count_label.setProperty("themeRole", role)
            for widget in (button, button.count_label):
                style = widget.style()
                style.unpolish(widget)
                style.polish(widget)
        self._refresh_icons()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.DynamicPropertyChange,
            QEvent.Type.EnabledChange,
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange,
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
        for log_filter, button in self.log_problem_buttons.items():
            icon = self._log_problem_icons[log_filter]
            button.set_status_icon(
                themed_icon(
                    button,
                    icon,
                    size=STATUS_ICON_SIZE,
                    color=icon_foreground(button),
                )
            )
            button.ensurePolished()
            button.refresh_content_geometry()

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


class _NotificationToast(QFrame):
    """Present the latest non-modal warning or error without consuming layout."""

    dismissed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchNotificationToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(NOTIFICATION_TOAST_MAXIMUM_WIDTH)
        self._notification: GuiNotification | None = None

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 72))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(10)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("notificationToastIcon")
        self.icon_label.setFixedSize(20, 20)
        layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        self.title_label = QLabel()
        self.title_label.setObjectName("notificationToastTitle")
        self.message_label = QLabel()
        self.message_label.setObjectName("notificationToastMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.message_label)
        layout.addLayout(text_layout, stretch=1)

        self.close_button = IconToolButton(
            IconName.CLOSE,
            "关闭通知",
            callback=self.dismiss,
            parent=self,
            hit_size=24,
            icon_size=12,
        )
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self.hide()

    def show_notification(
        self,
        notification: GuiNotification,
        *,
        timeout_ms: int = NOTIFICATION_TOAST_TIMEOUT_MS,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("notification toast timeout must be positive")
        self._notification = notification
        role = (
            "statusWarning"
            if notification.level is GuiNotificationLevel.WARNING
            else "statusDanger"
        )
        self.setProperty("themeRole", role)
        self.icon_label.setProperty("themeRole", role)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.title_label.setText(notification.title)
        self.message_label.setText(self._summarize(notification.message))
        self.message_label.setToolTip(notification.message)
        self._refresh_icon()
        self.adjustSize()
        self.show()
        self.raise_()
        self._hide_timer.start(timeout_ms)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        self.hide()
        self.dismissed.emit()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.DynamicPropertyChange,
        }:
            self._refresh_icon()

    def _refresh_icon(self) -> None:
        if self._notification is None:
            return
        icon_name = (
            IconName.PROBLEM_WARNING
            if self._notification.level is GuiNotificationLevel.WARNING
            else IconName.PROBLEM_ERROR
        )
        icon = themed_icon(
            self.icon_label,
            icon_name,
            size=18,
            color=icon_foreground(self.icon_label),
        )
        self.icon_label.setPixmap(icon.pixmap(QSize(18, 18)))

    @staticmethod
    def _summarize(message: str) -> str:
        summary = " ".join(message.split())
        if len(summary) <= NOTIFICATION_TOAST_SUMMARY_LIMIT:
            return summary
        return f"{summary[: NOTIFICATION_TOAST_SUMMARY_LIMIT - 1].rstrip()}…"


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

        self.content_area = QFrame()
        self.content_area.setObjectName("workbenchContentArea")
        self.content_area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QHBoxLayout(self.content_area)
        content_layout.setContentsMargins(
            WORKBENCH_CARD_MARGIN,
            WORKBENCH_CARD_TOP_MARGIN,
            WORKBENCH_CARD_MARGIN,
            WORKBENCH_CARD_MARGIN,
        )
        content_layout.setSpacing(0)

        self.side_stack = QStackedWidget()
        self.side_stack.setObjectName("workbenchSideBar")
        self.side_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.side_stack.setMinimumWidth(SIDE_BAR_MINIMUM_WIDTH)
        self.side_stack.setMaximumWidth(SIDE_BAR_MAXIMUM_WIDTH)
        for page in side_pages:
            self.side_stack.addWidget(page.widget)

        self.side_splitter = _ThinLineSplitter(Qt.Orientation.Horizontal)
        self.side_splitter.setObjectName("workbenchSideSplitter")
        editor.setProperty("workbenchCard", True)
        editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.side_splitter.addWidget(self.side_stack)
        self.side_splitter.addWidget(editor)
        self.side_splitter.setStretchFactor(0, 0)
        self.side_splitter.setStretchFactor(1, 1)
        self.side_splitter.setSizes((SIDE_BAR_DEFAULT_WIDTH, 640))
        self.side_splitter.splitterMoved.connect(self._remember_side_width)
        content_layout.addWidget(self.side_splitter)
        body_layout.addWidget(self.content_area, stretch=1)
        self._scrollbar_cards = (self.side_stack, editor)
        for card in self._scrollbar_cards:
            card.installEventFilter(self)
            self._set_card_hover_state(card, visible=False)
        root.addWidget(body, stretch=1)

        self.status_bar = WorkbenchStatusBar(bottom_pages)
        self.status_bar.panel_requested.connect(self.toggle_bottom_page)
        self.status_bar.log_filter_requested.connect(self.toggle_log_filter)
        root.addWidget(self.status_bar)
        self.detail_panel = _FloatingDetailPanel(bottom_pages, self)
        self.detail_panel.close_requested.connect(self.close_bottom_page)
        self.notification_toast = _NotificationToast(self)
        self.notification_toast.dismissed.connect(self._position_notification_toast)
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

    def show_notification(self, notification: GuiNotification) -> None:
        """Show the latest non-modal problem in one reusable corner toast."""
        self.notification_toast.show_notification(notification)
        self._position_notification_toast()

    def toggle_bottom_page(self, page_key: str) -> None:
        self._require_page(page_key, self._bottom_pages, "bottom")
        if self._active_bottom_page == page_key:
            self.close_bottom_page()
            return
        self._show_bottom_page(page_key)

    def toggle_log_filter(self, log_filter: LogFilter) -> None:
        self._require_page("logs", self._bottom_pages, "bottom")
        if (
            self._active_bottom_page == "logs"
            and self.status_bar.active_log_filter is log_filter
        ):
            self.close_bottom_page()
            return
        self.status_bar.render_log_filter(log_filter)
        self._show_bottom_page("logs")

    def close_bottom_page(self) -> None:
        self._set_outside_click_filter_enabled(False)
        self._close_panel_shortcut.setEnabled(False)
        if self._active_bottom_page is None:
            return
        self.detail_panel.hide()
        self._position_notification_toast()
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
        self._position_notification_toast()
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
        self._position_notification_toast()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._position_detail_panel()
        self._position_notification_toast()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._set_outside_click_filter_enabled(False)
        super().hideEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self._scrollbar_cards:
            if event.type() is QEvent.Type.Enter:
                self._set_card_hover_state(watched, visible=True)
            elif event.type() is QEvent.Type.Leave:
                self._set_card_hover_state(watched, visible=False)
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

    @staticmethod
    def _set_card_hover_state(card: QObject, *, visible: bool) -> None:
        for scroll_bar in card.findChildren(QScrollBar):
            if scroll_bar.property("cardHover") is visible:
                continue
            scroll_bar.setProperty("cardHover", visible)
            style = scroll_bar.style()
            style.unpolish(scroll_bar)
            style.polish(scroll_bar)
            scroll_bar.update()
        for header in card.findChildren(PaneHeader):
            header.set_actions_revealed(visible)

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
            or self._is_within(target, self.notification_toast)
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

    def _position_notification_toast(self) -> None:
        toast = self.notification_toast
        if toast.isHidden():
            return
        available_width = max(1, self.width() - (2 * NOTIFICATION_TOAST_MARGIN))
        width = min(NOTIFICATION_TOAST_MAXIMUM_WIDTH, available_width)
        toast.setFixedWidth(width)
        toast.adjustSize()
        x = max(NOTIFICATION_TOAST_MARGIN, self.width() - width - NOTIFICATION_TOAST_MARGIN)
        lower_anchor = (
            self.detail_panel.y()
            if self.detail_panel.isVisible()
            else self.height() - self.status_bar.height()
        )
        y = max(
            NOTIFICATION_TOAST_MARGIN,
            lower_anchor - toast.height() - NOTIFICATION_TOAST_MARGIN,
        )
        toast.move(x, y)
        toast.raise_()

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

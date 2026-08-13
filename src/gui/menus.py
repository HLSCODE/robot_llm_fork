"""In-window application menus backed by the existing QAction graph."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


MENU_PANEL_GAP = 4
MENU_EDGE_MARGIN = 6
SUBMENU_OPEN_DELAY_MS = 120


class _MenuBarButton(QToolButton):
    hovered = Signal()

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self.hovered.emit()
        super().enterEvent(event)


class _MenuActionRow(QFrame):
    hovered = Signal(object)
    activated = Signal(object)

    def __init__(self, action: QAction, parent: QWidget) -> None:
        super().__init__(parent)
        self.action = action
        self.setObjectName("applicationMenuRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("keyboardFocus", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        self._indicator = QLabel()
        self._indicator.setObjectName("applicationMenuIndicator")
        self._indicator.setFixedWidth(14)
        self._label = QLabel()
        self._label.setObjectName("applicationMenuLabel")
        self._shortcut = QLabel()
        self._shortcut.setObjectName("applicationMenuShortcut")
        self._arrow = QLabel()
        self._arrow.setObjectName("applicationMenuArrow")
        self._arrow.setFixedWidth(12)
        layout.addWidget(self._indicator)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._shortcut)
        layout.addWidget(self._arrow)
        self.action.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        submenu = self.action.menu()
        self._indicator.setText("✓" if self.action.isChecked() else "")
        self._label.setText(self.action.text().replace("&", ""))
        self._shortcut.setText(
            self.action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        )
        self._arrow.setText("›" if submenu is not None else "")
        self.setEnabled(self.action.isEnabled())
        self.setAccessibleName(self._label.text())

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        if self.isEnabled():
            self.hovered.emit(self.action)
        super().enterEvent(event)

    def focusInEvent(self, event: QEvent) -> None:  # noqa: N802
        self.setProperty("keyboardFocus", True)
        self._refresh_style()
        if self.isEnabled():
            self.hovered.emit(self.action)
        super().focusInEvent(event)

    def focusOutEvent(self, event: QEvent) -> None:  # noqa: N802
        self.setProperty("keyboardFocus", False)
        self._refresh_style()
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.isEnabled()
            and event.button() is Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit(self.action)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _refresh_style(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()


class _MenuPanel(QFrame):
    action_hovered = Signal(object, object)
    action_activated = Signal(object, object)

    def __init__(self, menu: QMenu, parent: QWidget) -> None:
        super().__init__(parent)
        self.menu = menu
        self.rows: list[_MenuActionRow] = []
        self.setObjectName("applicationMenuPanel")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(1)

        for action in menu.actions():
            if action.isSeparator():
                separator = QFrame(self)
                separator.setObjectName("applicationMenuSeparator")
                separator.setFixedHeight(1)
                layout.addWidget(separator)
                continue
            row = _MenuActionRow(action, self)
            row.hovered.connect(
                lambda hovered_action, source=row: self.action_hovered.emit(hovered_action, source)
            )
            row.activated.connect(
                lambda activated_action, source=row: self.action_activated.emit(
                    activated_action, source
                )
            )
            layout.addWidget(row)
            self.rows.append(row)

        self.setMinimumWidth(230)
        layout.activate()
        self.adjustSize()

    def focus_first(self) -> None:
        enabled_rows = [row for row in self.rows if row.isEnabled()]
        if enabled_rows:
            enabled_rows[0].setFocus(Qt.FocusReason.PopupFocusReason)

    def focus_relative(self, offset: int) -> None:
        enabled_rows = [row for row in self.rows if row.isEnabled()]
        if not enabled_rows:
            return
        focused = QApplication.focusWidget()
        try:
            index = enabled_rows.index(focused)  # type: ignore[arg-type]
        except ValueError:
            index = -1 if offset > 0 else 0
        enabled_rows[(index + offset) % len(enabled_rows)].setFocus(Qt.FocusReason.PopupFocusReason)

    def focused_row(self) -> _MenuActionRow | None:
        focused = QApplication.focusWidget()
        return focused if isinstance(focused, _MenuActionRow) and focused in self.rows else None


class InWindowMenuController(QObject):
    """Own the lifecycle, placement and keyboard state of menu panels."""

    closed = Signal()

    def __init__(self, owner: QWidget, menu_bar: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner
        self._menu_bar = menu_bar
        self._panels: list[_MenuPanel] = []
        self._parent_rows: list[_MenuActionRow | None] = []
        self._restore_focus: QWidget | None = None
        self._root_menu: QMenu | None = None
        self._root_anchor: QWidget | None = None
        self._pending_submenu: tuple[QAction, _MenuActionRow, int] | None = None
        self._submenu_timer = QTimer(self)
        self._submenu_timer.setSingleShot(True)
        self._submenu_timer.setInterval(SUBMENU_OPEN_DELAY_MS)
        self._submenu_timer.timeout.connect(self._open_pending_submenu)
        self._owner.installEventFilter(self)

    @property
    def is_open(self) -> bool:
        return bool(self._panels)

    @property
    def root_menu(self) -> QMenu | None:
        return self._root_menu

    def toggle(self, menu: QMenu, anchor: QWidget) -> None:
        if self._root_menu is menu and self.is_open:
            self.close()
            return
        self.open(menu, anchor)

    def open(self, menu: QMenu, anchor: QWidget) -> None:
        self.close(restore_focus=False)
        self._restore_focus = QApplication.focusWidget()
        self._root_menu = menu
        self._root_anchor = anchor
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        panel = self._create_panel(menu, None)
        position = anchor.mapTo(
            self._owner,
            QPoint(0, anchor.height() + MENU_PANEL_GAP),
        )
        self._place_panel(panel, position)
        panel.show()
        panel.raise_()

    def switch_root(self, menu: QMenu, anchor: QWidget) -> None:
        if self.is_open and self._root_menu is not menu:
            self.open(menu, anchor)

    def close(self, *, restore_focus: bool = True) -> None:
        self._submenu_timer.stop()
        self._pending_submenu = None
        for panel in self._panels:
            panel.hide()
            panel.deleteLater()
        was_open = bool(self._panels)
        self._panels.clear()
        self._parent_rows.clear()
        self._root_menu = None
        self._root_anchor = None
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        if restore_focus and self._restore_focus is not None:
            self._restore_focus.setFocus(Qt.FocusReason.PopupFocusReason)
        self._restore_focus = None
        if was_open:
            self.closed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self._owner and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Hide,
        }:
            self.close(restore_focus=False)
            return False
        if not self.is_open:
            return False
        if event_type is QEvent.Type.WindowDeactivate:
            self.close(restore_focus=False)
            return False
        if event_type is QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            if not self._is_menu_target(target):
                self.close(restore_focus=False)
            return False
        if event_type is QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self._handle_key_press(event)
        return False

    def _create_panel(
        self,
        menu: QMenu,
        parent_row: _MenuActionRow | None,
    ) -> _MenuPanel:
        panel = _MenuPanel(menu, self._owner)
        panel.action_hovered.connect(self._on_action_hovered)
        panel.action_activated.connect(self._on_action_activated)
        self._panels.append(panel)
        self._parent_rows.append(parent_row)
        return panel

    def _on_action_hovered(self, action: QAction, row: _MenuActionRow) -> None:
        level = self._panel_level(row)
        submenu = action.menu()
        if submenu is None:
            self._submenu_timer.stop()
            self._pending_submenu = None
            self._remove_panels_after(level)
            return
        if level + 1 < len(self._panels) and self._panels[level + 1].menu is submenu:
            return
        self._pending_submenu = (action, row, level)
        self._submenu_timer.start()

    def _on_action_activated(self, action: QAction, row: _MenuActionRow) -> None:
        submenu = action.menu()
        if submenu is not None:
            self._show_submenu(submenu, row, self._panel_level(row), focus=True)
            return
        self.close()
        action.trigger()

    def _open_pending_submenu(self) -> None:
        pending = self._pending_submenu
        self._pending_submenu = None
        if pending is None:
            return
        action, row, level = pending
        submenu = action.menu()
        if submenu is not None and row.isVisible():
            self._show_submenu(submenu, row, level, focus=False)

    def _show_submenu(
        self,
        menu: QMenu,
        row: _MenuActionRow,
        parent_level: int,
        *,
        focus: bool,
    ) -> None:
        self._remove_panels_after(parent_level)
        panel = self._create_panel(menu, row)
        row_top = row.mapTo(self._owner, QPoint(0, 0)).y()
        parent_panel = self._panels[parent_level]
        right_x = parent_panel.x() + parent_panel.width() + MENU_PANEL_GAP
        left_x = parent_panel.x() - panel.width() - MENU_PANEL_GAP
        target_x = (
            right_x if right_x + panel.width() <= self._owner.width() - MENU_EDGE_MARGIN else left_x
        )
        self._place_panel(panel, QPoint(target_x, row_top))
        panel.show()
        panel.raise_()
        if focus:
            panel.focus_first()

    def _place_panel(self, panel: _MenuPanel, requested: QPoint) -> None:
        max_x = max(MENU_EDGE_MARGIN, self._owner.width() - panel.width() - MENU_EDGE_MARGIN)
        max_y = max(MENU_EDGE_MARGIN, self._owner.height() - panel.height() - MENU_EDGE_MARGIN)
        panel.move(
            min(max(requested.x(), MENU_EDGE_MARGIN), max_x),
            min(max(requested.y(), MENU_EDGE_MARGIN), max_y),
        )

    def _remove_panels_after(self, level: int) -> None:
        while len(self._panels) > level + 1:
            panel = self._panels.pop()
            self._parent_rows.pop()
            panel.hide()
            panel.deleteLater()

    def _panel_level(self, row: _MenuActionRow) -> int:
        for level, panel in enumerate(self._panels):
            if row in panel.rows:
                return level
        raise ValueError("menu row is not owned by an active panel")

    def _handle_key_press(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return True
        panel = self._focused_panel()
        if panel is None:
            return False
        if event.key() == Qt.Key.Key_Down:
            panel.focus_relative(1)
            return True
        if event.key() == Qt.Key.Key_Up:
            panel.focus_relative(-1)
            return True
        row = panel.focused_row()
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and row is not None:
            self._on_action_activated(row.action, row)
            return True
        if event.key() == Qt.Key.Key_Right and row is not None and row.action.menu() is not None:
            submenu = row.action.menu()
            assert submenu is not None
            self._show_submenu(submenu, row, self._panel_level(row), focus=True)
            return True
        if event.key() == Qt.Key.Key_Left and len(self._panels) > 1:
            parent_row = self._parent_rows[-1]
            self._remove_panels_after(len(self._panels) - 2)
            if parent_row is not None:
                parent_row.setFocus(Qt.FocusReason.PopupFocusReason)
            return True
        return False

    def _focused_panel(self) -> _MenuPanel | None:
        focused = QApplication.focusWidget()
        for panel in reversed(self._panels):
            if isinstance(focused, QWidget) and self._is_within(focused, panel):
                return panel
        return self._panels[-1] if self._panels else None

    def _is_menu_target(self, target: QWidget | None) -> bool:
        if target is None:
            return False
        if self._is_within(target, self._menu_bar):
            return True
        return any(self._is_within(target, panel) for panel in self._panels)

    @staticmethod
    def _is_within(target: QWidget, container: QWidget) -> bool:
        current: QWidget | None = target
        while current is not None:
            if current is container:
                return True
            current = current.parentWidget()
        return False


class ApplicationMenuBar(QFrame):
    """Compact menu bar whose panels never leave the main-window surface."""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self.setObjectName("applicationMenuBar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self._controller = InWindowMenuController(owner, self)
        self._controller.closed.connect(self._clear_checked_buttons)
        self._buttons: dict[QMenu, _MenuBarButton] = {}

    @property
    def controller(self) -> InWindowMenuController:
        return self._controller

    def addMenu(self, title: str) -> QMenu:  # noqa: N802
        menu = QMenu(title, self)
        button = _MenuBarButton(self)
        button.setObjectName("applicationMenuButton")
        button.setText(title.replace("&", ""))
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.clicked.connect(
            lambda _checked=False, selected=menu, anchor=button: self._toggle_menu(selected, anchor)
        )
        button.hovered.connect(
            lambda selected=menu, anchor=button: self._hover_menu(selected, anchor)
        )
        self._layout.insertWidget(self._layout.count() - 1, button)
        self._buttons[menu] = button
        return menu

    def _toggle_menu(self, menu: QMenu, button: _MenuBarButton) -> None:
        self._controller.toggle(menu, button)
        self._sync_checked_button()

    def _hover_menu(self, menu: QMenu, button: _MenuBarButton) -> None:
        self._controller.switch_root(menu, button)
        self._sync_checked_button()

    def _sync_checked_button(self) -> None:
        active = self._controller.root_menu
        for menu, button in self._buttons.items():
            button.setChecked(menu is active)

    def _clear_checked_buttons(self) -> None:
        for button in self._buttons.values():
            button.setChecked(False)

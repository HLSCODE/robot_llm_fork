"""Palette-aware, icon-only controls shared by workbench toolbars."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QToolButton,
    QWidget,
)

from .icons import IconName, themed_icon


TOOL_BUTTON_HIT_SIZE = 32
TOOL_BUTTON_ICON_SIZE = 18
PANE_HEADER_VERTICAL_MARGIN = 4
PANE_HEADER_MINIMUM_HEIGHT = TOOL_BUTTON_HIT_SIZE + (2 * PANE_HEADER_VERTICAL_MARGIN)


class ElidingComboBox(QComboBox):
    """Keep full item text while eliding only the closed-field presentation."""

    def visible_text(self) -> str:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        edit_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        return self.fontMetrics().elidedText(
            option.currentText,
            Qt.TextElideMode.ElideRight,
            max(0, edit_rect.width()),
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self.visible_text()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


def icon_foreground(widget: QWidget, role: str | None = None) -> QColor:
    """Return a readable semantic icon color for the active application palette."""
    resolved_role = role or widget.property("themeRole")
    if not widget.isEnabled():
        return widget.palette().color(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
        )
    if resolved_role in {"primary", "success", "danger", "dangerStrong"}:
        return QColor("#ffffff")
    if resolved_role == "warning":
        return QColor("#111827")

    is_light_surface = (
        widget.palette().color(QPalette.ColorRole.Window).lightnessF() > 0.5
    )
    if resolved_role == "statusSuccess":
        return QColor("#15803d" if is_light_surface else "#4ade80")
    if resolved_role == "statusDanger":
        return QColor("#dc2626" if is_light_surface else "#f87171")
    if resolved_role == "statusWarning":
        return QColor("#b45309" if is_light_surface else "#fbbf24")
    if resolved_role == "statusMuted":
        return widget.palette().color(QPalette.ColorRole.PlaceholderText)
    return widget.palette().color(QPalette.ColorRole.WindowText)


class IconToolButton(QToolButton):
    """Render one monochrome resource icon and refresh it with the palette."""

    def __init__(
        self,
        icon_name: IconName,
        tooltip: str,
        *,
        callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        object_name: str = "paneToolButton",
        hit_size: int = TOOL_BUTTON_HIT_SIZE,
        icon_size: int = TOOL_BUTTON_ICON_SIZE,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_size = icon_size
        self.setObjectName(object_name)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setAutoRaise(True)
        self.setFixedSize(hit_size, hit_size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        if callback is not None:
            self.clicked.connect(callback)
        self._refresh_icon()

    @property
    def icon_name(self) -> IconName:
        return self._icon_name

    def set_icon_name(self, icon_name: IconName) -> None:
        if icon_name is self._icon_name:
            return
        self._icon_name = icon_name
        self._refresh_icon()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.DynamicPropertyChange,
            QEvent.Type.EnabledChange,
        }:
            self._refresh_icon()

    def _refresh_icon(self) -> None:
        self.setIcon(
            themed_icon(
                self,
                self._icon_name,
                size=self._icon_size,
                color=icon_foreground(self),
            )
        )


class PaneHeader(QWidget):
    """Compact title row with VS Code-style icon actions."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("paneHeader")
        self.setMinimumHeight(PANE_HEADER_MINIMUM_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            8,
            PANE_HEADER_VERTICAL_MARGIN,
            4,
            PANE_HEADER_VERTICAL_MARGIN,
        )
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("paneHeaderTitle")
        self.title_label.setAccessibleName(title)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self._layout = layout
        self._actions_revealed = True
        self._action_buttons: list[IconToolButton] = []

    def replace_title_with(self, widget: QWidget) -> None:
        """Use a compact navigation control in place of the static pane title."""
        self.title_label.clear()
        self.title_label.hide()
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            if item is not None and item.spacerItem() is not None:
                self._layout.takeAt(index)
                break
        widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            widget.sizePolicy().verticalPolicy(),
        )
        self._layout.insertWidget(0, widget)

    def set_actions_revealed(self, revealed: bool) -> None:
        """Reveal commands and release their layout space while hidden."""
        self._actions_revealed = revealed
        for button in self._action_buttons:
            button.setVisible(revealed)
            button.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                not revealed,
            )
            button.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if revealed else Qt.FocusPolicy.NoFocus
            )

    def add_action(
        self,
        icon_name: IconName,
        tooltip: str,
        callback: Callable[[], None],
    ) -> IconToolButton:
        button = IconToolButton(
            icon_name,
            tooltip,
            callback=callback,
            parent=self,
        )
        self._action_buttons.append(button)
        self._layout.addWidget(button)
        self.set_actions_revealed(self._actions_revealed)
        return button

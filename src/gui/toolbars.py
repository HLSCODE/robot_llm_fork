"""Palette-aware, icon-only controls shared by workbench toolbars."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from .icons import IconName, themed_icon


TOOL_BUTTON_HIT_SIZE = 32
TOOL_BUTTON_ICON_SIZE = 18


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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("paneHeaderTitle")
        self.title_label.setAccessibleName(title)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self._layout = layout

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
        self._layout.addWidget(button)
        return button

"""Application-wide light, dark and system-following Qt themes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from . import resources_rc as _resources_rc  # noqa: F401
from .tooltips import ToolTipService, install_tooltip_service
from .widget_style import (
    create_consistent_widget_style,
    install_combo_box_popup_coordinator,
    retain_application_style,
)


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @classmethod
    def parse(cls, value: str) -> ThemeMode:
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            supported = ", ".join(mode.value for mode in cls)
            raise ValueError(f"GUI theme must be one of: {supported}") from exc


_BASE_STYLE_INSTALLED_PROPERTY = "robotLlmConsistentWidgetStyle"


@dataclass(frozen=True, slots=True)
class ThemeColors:
    window: str
    surface: str
    surface_subtle: str
    text: str
    text_muted: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    selection: str
    disabled_surface: str
    disabled_text: str
    success: str
    warning: str
    danger: str
    danger_strong: str
    tooltip: str
    tooltip_text: str


LIGHT_COLORS = ThemeColors(
    window="#f1f5f9",
    surface="#ffffff",
    surface_subtle="#f8fafc",
    text="#1e293b",
    text_muted="#64748b",
    border="#e2e8f0",
    border_strong="#94a3b8",
    accent="#2563eb",
    accent_hover="#1d4ed8",
    selection="#dbeafe",
    disabled_surface="#f1f5f9",
    disabled_text="#94a3b8",
    success="#16a34a",
    warning="#d97706",
    danger="#dc2626",
    danger_strong="#991b1b",
    tooltip="#ffffff",
    tooltip_text="#334155",
)

DARK_COLORS = ThemeColors(
    window="#0f172a",
    surface="#111827",
    surface_subtle="#1e293b",
    text="#f1f5f9",
    text_muted="#94a3b8",
    border="#334155",
    border_strong="#64748b",
    accent="#60a5fa",
    accent_hover="#93c5fd",
    selection="#1e3a5f",
    disabled_surface="#1e293b",
    disabled_text="#64748b",
    success="#4ade80",
    warning="#fbbf24",
    danger="#f87171",
    danger_strong="#ef4444",
    tooltip="#1e293b",
    tooltip_text="#e2e8f0",
)


class ThemeController(QObject):
    """Own and apply the process-wide Qt theme."""

    mode_changed = Signal(object)

    def __init__(
        self,
        application: QApplication,
        mode: ThemeMode,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        apply_consistent_base_style(application)
        self._tooltip_service: ToolTipService = install_tooltip_service(application)
        self._mode = mode
        self._application.styleHints().colorSchemeChanged.connect(
            self._on_system_color_scheme_changed
        )
        self.apply()

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def effective_mode(self) -> ThemeMode:
        if self._mode is not ThemeMode.SYSTEM:
            return self._mode
        scheme = self._application.styleHints().colorScheme()
        return (
            ThemeMode.DARK
            if scheme is Qt.ColorScheme.Dark
            else ThemeMode.LIGHT
        )

    def set_mode(self, mode: ThemeMode) -> None:
        if mode is self._mode:
            return
        self._mode = mode
        self.apply()
        self.mode_changed.emit(mode)

    def apply(self) -> None:
        effective_mode = self.effective_mode
        colors = colors_for_mode(effective_mode)
        self._application.setPalette(build_palette(colors))
        self._application.setStyleSheet(build_stylesheet(colors))
        self._application.setWindowIcon(application_icon_for_mode(effective_mode))

    def _on_system_color_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._mode is ThemeMode.SYSTEM:
            self.apply()


def colors_for_mode(mode: ThemeMode) -> ThemeColors:
    if mode is ThemeMode.DARK:
        return DARK_COLORS
    return LIGHT_COLORS


def application_icon_for_mode(mode: ThemeMode) -> QIcon:
    resource = (
        ":/app/app-icon-dark.png"
        if mode is ThemeMode.DARK
        else ":/app/app-icon-light.png"
    )
    return QIcon(resource)


def apply_consistent_base_style(application: QApplication) -> None:
    """Use one Qt renderer so platform-native subcontrols cannot leak through."""
    install_combo_box_popup_coordinator(application)
    if application.property(_BASE_STYLE_INSTALLED_PROPERTY) is True:
        return
    style = create_consistent_widget_style()
    application.setStyle(style)
    retain_application_style(application, style)
    application.setProperty(_BASE_STYLE_INSTALLED_PROPERTY, True)


def build_palette(colors: ThemeColors) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors.window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.surface_subtle))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.tooltip))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.tooltip_text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.text_muted))
    palette.setColor(QPalette.ColorRole.Mid, QColor(colors.border))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors.disabled_text))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(colors.disabled_text),
    )
    return palette


def build_stylesheet(colors: ThemeColors) -> str:
    combo_arrow = (
        ":/icons/chevron-down-on-dark.svg"
        if colors == DARK_COLORS
        else ":/icons/chevron-down-on-light.svg"
    )
    spin_arrow_up = (
        ":/icons/chevron-up-on-dark.svg"
        if colors == DARK_COLORS
        else ":/icons/chevron-up-on-light.svg"
    )
    spin_arrow_down = combo_arrow
    return f"""
QWidget {{ color: {colors.text}; }}
QMainWindow {{ background: transparent; }}
QDialog {{ background: {colors.window}; }}
QFrame#applicationWindowSurface {{
    background: {colors.window}; border: none;
}}
QFrame#applicationWindowSurface[windowCorners="rounded"] {{
    border-radius: 10px;
}}
QDialog#appDialogWindow {{ background: transparent; }}
QFrame#appDialogSurface {{
    background: {colors.surface}; color: {colors.text};
    border: 1px solid {colors.border_strong}; border-radius: 12px;
}}
QWidget#appDialogTitleBar,
QWidget#appDialogContent {{ background: transparent; border: none; }}
QLabel#appDialogApplicationIcon,
QLabel#appDialogTitle {{ background: transparent; border: none; color: {colors.text}; }}
QLabel#appDialogTitle {{ font-weight: 650; }}
QToolButton#appDialogCloseButton {{
    background: transparent; border: none; border-radius: 6px; padding: 0;
}}
QToolButton#appDialogCloseButton:hover {{ background: {colors.selection}; }}
QLabel#appMessageText {{
    background: transparent; border: none; color: {colors.text};
    font-size: 13px; padding: 4px 0;
}}
QLabel#appMessageIndicator {{
    background: {colors.selection}; color: {colors.accent};
    border: none; border-radius: 15px; font-size: 16px; font-weight: 700;
}}
QLabel#appMessageIndicator[messageKind="warning"] {{
    background: {colors.disabled_surface}; color: {colors.warning};
}}
QLabel#appMessageIndicator[messageKind="error"],
QLabel#appMessageIndicator[messageKind="critical"] {{
    background: {colors.disabled_surface}; color: {colors.danger};
}}
QPushButton {{
    background: {colors.surface_subtle}; border: 1px solid transparent;
    border-radius: 6px; padding: 5px 12px; color: {colors.text};
    font-weight: 500;
}}
QPushButton:hover {{ background: {colors.selection}; }}
QPushButton:pressed {{ background: {colors.selection}; }}
QPushButton:disabled {{
    background: {colors.disabled_surface}; color: {colors.disabled_text};
    border-color: transparent;
}}
QPushButton[themeRole="primary"] {{ background: {colors.accent}; color: {colors.surface}; border-color: transparent; }}
QPushButton[themeRole="primary"]:hover {{ background: {colors.accent_hover}; }}
QPushButton[themeRole="success"] {{ background: {colors.success}; color: #ffffff; border-color: transparent; }}
QPushButton[themeRole="warning"] {{ background: {colors.warning}; color: #111827; border-color: transparent; }}
QPushButton[themeRole="danger"] {{ background: {colors.danger}; color: #ffffff; border-color: transparent; }}
QPushButton[themeRole="dangerStrong"] {{ background: {colors.danger_strong}; color: #ffffff; border-color: transparent; }}
QPushButton[themeRole]:disabled {{
    background: {colors.disabled_surface}; color: {colors.disabled_text};
    border-color: transparent;
}}
QLabel[themeRole="muted"] {{ color: {colors.text_muted}; }}
QLabel[themeRole="success"] {{ color: {colors.success}; font-weight: 700; }}
QLabel[themeRole="warning"] {{ color: {colors.warning}; font-weight: 700; }}
QLabel[themeRole="danger"] {{ color: {colors.danger}; font-weight: 700; }}
QLabel#compensationSectionLabel {{ color: {colors.text}; font-weight: 600; }}
QTabWidget::pane {{ border: none; background: {colors.surface}; }}
QTabBar::tab {{ background: transparent; border: none; border-radius: 6px; padding: 6px 14px; color: {colors.text_muted}; }}
QTabBar::tab:selected {{ background: {colors.selection}; color: {colors.accent}; font-weight: 700; }}
QTabBar::tab:hover:!selected {{ background: {colors.selection}; color: {colors.accent}; }}
QGroupBox {{ font-weight: 700; border: none; margin-top: 14px; padding: 14px 8px 8px; background: {colors.surface}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {colors.accent}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {colors.surface_subtle}; color: {colors.text}; border: 1px solid transparent; border-radius: 6px; }}
QLineEdit[validationState="error"], QSpinBox[validationState="error"],
QDoubleSpinBox[validationState="error"], QComboBox[validationState="error"] {{
    border: 1px solid {colors.danger};
}}
QLineEdit[validationState="error"]:focus, QSpinBox[validationState="error"]:focus,
QDoubleSpinBox[validationState="error"]:focus, QComboBox[validationState="error"]:focus {{
    border: 1px solid {colors.danger};
}}
QTextEdit, QListWidget {{ background: {colors.surface}; color: {colors.text}; border: none; border-radius: 6px; }}
QLineEdit {{ padding: 5px 8px; }}
QSpinBox, QDoubleSpinBox {{ padding: 5px 30px 5px 8px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {colors.accent}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; height: 14px; background: transparent; border: none;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 24px; height: 14px; background: transparent; border: none;
    border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {colors.selection}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({spin_arrow_up}); width: 10px; height: 6px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({spin_arrow_down}); width: 10px; height: 6px; }}
QComboBox {{ padding: 6px 34px 6px 10px; min-height: 18px; }}
QComboBox:hover, QComboBox:focus, QComboBox:on {{ background: {colors.surface}; border-color: {colors.accent}; }}
QComboBox:disabled {{ background: {colors.disabled_surface}; color: {colors.disabled_text}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 30px; background: transparent; border: none;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}}
QComboBox::drop-down:hover {{ background: {colors.selection}; }}
QComboBox::down-arrow {{ image: url({combo_arrow}); width: 12px; height: 8px; }}
QComboBoxPrivateContainer, QWidget#comboBoxPopup {{
    background: {colors.surface}; border: 1px solid {colors.border};
    border-radius: 8px; padding: 4px;
}}
QComboBox QAbstractItemView {{
    background: {colors.surface}; color: {colors.text};
    border: none; border-radius: 5px; padding: 0; outline: 0;
    selection-background-color: {colors.selection};
    selection-color: {colors.text};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 3px 8px; border: none; border-radius: 4px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
    background: {colors.selection}; color: {colors.text}; border: none; outline: none;
}}
QListWidget, QAbstractItemView {{ outline: 0; }}
QListWidget::item {{ padding: 6px 10px; border: none; border-radius: 6px; }}
QListWidget::item:hover {{ background: {colors.surface_subtle}; border: none; }}
QListWidget::item:selected, QListWidget::item:selected:active,
QAbstractItemView::item:selected {{
    background: {colors.selection}; color: {colors.text}; border: none; outline: none;
}}
QListWidget::item:focus, QAbstractItemView::item:focus {{ outline: none; border: none; }}
QFrame[frameShape="6"] {{ border: none; background: {colors.surface}; }}
QCheckBox {{ spacing: 6px; color: {colors.text}; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {colors.border_strong}; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {colors.border_strong}; border-radius: 4px; min-width: 20px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {colors.border}; }}
QSplitter::handle:hover {{ background: {colors.accent}; }}
QSplitter#workbenchSideSplitter::handle, QSplitter#workbenchBottomSplitter::handle {{ background: transparent; }}
QFrame#workbenchActivityBar {{ background: {colors.surface_subtle}; border: none; }}
QStackedWidget#workbenchSideBar, QStackedWidget#workbenchBottomPanel {{ background: {colors.surface}; }}
QWidget#paneHeader, QWidget#workflowCommandBar {{ background: {colors.surface}; border: none; }}
QLabel#paneHeaderTitle {{ color: {colors.text}; font-weight: 700; padding-left: 2px; }}
QToolButton#paneToolButton {{
    background: transparent; border: none; border-radius: 5px;
    color: {colors.text}; padding: 4px;
}}
QToolButton#paneToolButton:hover {{ background: {colors.selection}; }}
QToolButton#paneToolButton:pressed, QToolButton#paneToolButton:checked {{ background: {colors.border}; }}
QToolButton#paneToolButton:disabled {{ background: transparent; color: {colors.disabled_text}; }}
QToolButton:focus {{ outline: none; border: none; }}
QToolButton#paneToolButton[themeRole="primary"] {{ background: {colors.selection}; }}
QToolButton#paneToolButton[themeRole="success"] {{ background: {colors.success}; }}
QToolButton#paneToolButton[themeRole="warning"] {{ background: {colors.warning}; }}
QToolButton#paneToolButton[themeRole="danger"] {{ background: {colors.danger}; }}
QToolButton#paneToolButton[themeRole="dangerStrong"] {{ background: {colors.danger_strong}; }}
QToolButton#paneToolButton[themeRole]:disabled {{ background: {colors.disabled_surface}; }}
QFrame#workbenchFloatingPanel {{
    background: {colors.surface}; color: {colors.text};
    border: 1px solid {colors.border}; border-radius: 10px;
}}
QLabel#workbenchFloatingPanelTitle {{
    background: transparent; border: none; color: {colors.text}; font-weight: 600;
}}
QStackedWidget#workbenchDetailStack {{ background: transparent; border: none; }}
QToolButton#activityButton {{ background: transparent; border: none; border-radius: 6px; color: {colors.text_muted}; padding: 0; }}
QToolButton#activityButton:hover {{ background: {colors.selection}; color: {colors.text}; }}
QToolButton#activityButton:checked {{ background: {colors.selection}; color: {colors.accent}; }}
QFrame#workbenchStatusBar {{
    background: {colors.surface}; border: none; color: {colors.text_muted};
}}
QFrame#workbenchStatusBar[windowCorners="rounded"] {{
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;
}}
QFrame#workbenchStatusBar QLabel {{ color: {colors.text_muted}; }}
QToolButton#statusPanelButton {{
    background: transparent; border: none; border-radius: 5px; color: {colors.text_muted}; padding: 3px;
}}
QToolButton#statusPanelButton:hover, QToolButton#statusPanelButton:checked {{ background: {colors.selection}; }}
QMenuBar {{ background: {colors.surface}; color: {colors.text}; border: none; padding: 2px 4px; spacing: 2px; }}
QMenuBar::item {{ background: transparent; padding: 6px 10px; margin: 1px; border-radius: 5px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {colors.selection}; color: {colors.text}; }}
QMenu {{
    background: {colors.surface}; color: {colors.text};
    border: 1px solid {colors.border}; padding: 6px;
}}
QMenu::item {{ min-width: 150px; padding: 7px 28px; margin: 1px 0; border-radius: 5px; }}
QMenu::item:selected {{ background: {colors.selection}; color: {colors.text}; }}
QMenu::item:disabled {{ color: {colors.disabled_text}; }}
QMenu::separator {{ height: 1px; background: {colors.border}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
QMenu::right-arrow {{ width: 9px; height: 9px; margin-right: 8px; }}
QFrame#applicationMenuBar {{
    background: transparent; border: none;
}}
QFrame#applicationTitleBar {{
    background: {colors.surface}; border: none;
}}
QFrame#applicationTitleBar[windowCorners="rounded"] {{
    border-top-left-radius: 10px; border-top-right-radius: 10px;
}}
QLabel#applicationTitleIcon,
QLabel#applicationTitle {{
    background: transparent; border: none; color: {colors.text};
}}
QLabel#applicationTitle {{ font-weight: 600; padding: 0 6px 0 2px; }}
QToolButton#windowControlButton {{
    background: transparent; border: none; border-radius: 0; padding: 0;
}}
QToolButton#windowControlButton:hover {{ background: {colors.selection}; }}
QToolButton#windowControlButton:pressed {{ background: {colors.border}; }}
QToolButton#windowControlButton[windowControl="close"]:hover,
QToolButton#windowControlButton[windowControl="close"]:pressed {{
    background: {colors.danger_strong};
}}
QToolButton#windowControlButton[windowControl="close"][windowCorners="rounded"] {{
    border-top-right-radius: 10px;
}}
QToolButton#applicationMenuButton {{
    background: transparent; color: {colors.text}; border: none;
    border-radius: 5px; padding: 6px 10px;
}}
QToolButton#applicationMenuButton:hover,
QToolButton#applicationMenuButton:checked {{
    background: {colors.selection}; color: {colors.text};
}}
QFrame#applicationMenuPanel {{
    background: {colors.surface}; color: {colors.text};
    border: 1px solid {colors.border}; border-radius: 9px;
}}
QFrame#applicationMenuRow {{
    background: transparent; border: none; border-radius: 5px;
}}
QFrame#applicationMenuRow:hover,
QFrame#applicationMenuRow[keyboardFocus="true"] {{
    background: {colors.selection};
}}
QFrame#applicationMenuRow:disabled {{ background: transparent; }}
QLabel#applicationMenuIndicator,
QLabel#applicationMenuLabel,
QLabel#applicationMenuShortcut,
QLabel#applicationMenuArrow {{
    background: transparent; border: none; color: {colors.text};
}}
QLabel#applicationMenuLabel {{ color: {colors.text}; font-weight: 500; }}
QLabel#applicationMenuShortcut {{ color: {colors.text}; }}
QFrame#applicationMenuRow:disabled QLabel {{ color: {colors.text_muted}; }}
QFrame#applicationMenuSeparator {{
    background: {colors.border}; border: none; margin: 4px 6px;
}}
QToolButton::menu-indicator {{ width: 9px; height: 9px; subcontrol-position: bottom right; }}
QWidget#aiStatusCard {{ background: {colors.surface_subtle}; border: none; border-radius: 8px; }}
QWidget#startupProgressWindow {{ background: transparent; }}
QFrame#startupCard {{ background: {colors.surface}; border: 1px solid {colors.border}; border-radius: 16px; }}
QLabel#startupTitle {{ color: {colors.text}; font-size: 25px; font-weight: 700; }}
QLabel#startupSubtitle, QLabel#startupDetail {{ color: {colors.text_muted}; }}
QProgressBar#startupProgressBar {{ background: {colors.border}; border: none; border-radius: 4px; }}
QProgressBar#startupProgressBar::chunk {{ background: {colors.success}; border-radius: 4px; }}
QLabel#startupStatus, QLabel#startupPercent {{ color: {colors.accent}; font-weight: 600; }}
QPushButton#startupExitButton {{ background: {colors.danger_strong}; color: #ffffff; border-color: {colors.danger_strong}; }}
"""


def set_theme_role(widget: QWidget, role: str | None) -> None:
    """Apply a semantic role and refresh an already visible Qt widget."""
    widget.setProperty("themeRole", role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()

"""Application-wide light, dark and system-following Qt themes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


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
    tooltip="#1e293b",
    tooltip_text="#f8fafc",
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
    tooltip="#e2e8f0",
    tooltip_text="#0f172a",
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
        colors = colors_for_mode(self.effective_mode)
        self._application.setPalette(build_palette(colors))
        self._application.setStyleSheet(build_stylesheet(colors))

    def _on_system_color_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._mode is ThemeMode.SYSTEM:
            self.apply()


def colors_for_mode(mode: ThemeMode) -> ThemeColors:
    if mode is ThemeMode.DARK:
        return DARK_COLORS
    return LIGHT_COLORS


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
    return f"""
QWidget {{ color: {colors.text}; }}
QMainWindow, QDialog {{ background: {colors.window}; }}
QPushButton {{
    background: {colors.surface}; border: 1px solid {colors.border};
    border-radius: 6px; padding: 5px 12px; color: {colors.text};
    font-weight: 500;
}}
QPushButton:hover {{ background: {colors.surface_subtle}; border-color: {colors.border_strong}; }}
QPushButton:pressed {{ background: {colors.selection}; }}
QPushButton:disabled {{
    background: {colors.disabled_surface}; color: {colors.disabled_text};
    border-color: {colors.border};
}}
QPushButton[themeRole="primary"] {{ background: {colors.accent}; color: {colors.surface}; border-color: {colors.accent}; }}
QPushButton[themeRole="primary"]:hover {{ background: {colors.accent_hover}; }}
QPushButton[themeRole="success"] {{ background: {colors.success}; color: #ffffff; border-color: {colors.success}; }}
QPushButton[themeRole="warning"] {{ background: {colors.warning}; color: #111827; border-color: {colors.warning}; }}
QPushButton[themeRole="danger"] {{ background: {colors.danger}; color: #ffffff; border-color: {colors.danger}; }}
QPushButton[themeRole="dangerStrong"] {{ background: {colors.danger_strong}; color: #ffffff; border-color: {colors.danger_strong}; }}
QPushButton[themeRole]:disabled {{
    background: {colors.disabled_surface}; color: {colors.disabled_text};
    border-color: {colors.border};
}}
QLabel[themeRole="muted"] {{ color: {colors.text_muted}; }}
QLabel[themeRole="success"] {{ color: {colors.success}; font-weight: 700; }}
QLabel[themeRole="warning"] {{ color: {colors.warning}; font-weight: 700; }}
QLabel[themeRole="danger"] {{ color: {colors.danger}; font-weight: 700; }}
QTabWidget::pane {{ border: 1px solid {colors.border}; border-radius: 8px; background: {colors.surface}; top: -1px; }}
QTabBar::tab {{ background: {colors.surface_subtle}; border: 1px solid {colors.border}; border-bottom: none; border-radius: 6px 6px 0 0; padding: 6px 14px; color: {colors.text_muted}; }}
QTabBar::tab:selected {{ background: {colors.surface}; color: {colors.accent}; font-weight: 700; border-bottom: 2px solid {colors.accent}; }}
QTabBar::tab:hover:!selected {{ background: {colors.selection}; color: {colors.accent}; }}
QGroupBox {{ font-weight: 700; border: 1px solid {colors.border}; border-radius: 8px; margin-top: 14px; padding: 14px 8px 8px; background: {colors.surface}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {colors.accent}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QListWidget {{ background: {colors.surface}; color: {colors.text}; border: 1px solid {colors.border}; border-radius: 6px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ padding: 5px 8px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:hover {{ border-color: {colors.accent}; }}
QComboBox QAbstractItemView {{ background: {colors.surface}; color: {colors.text}; selection-background-color: {colors.selection}; selection-color: {colors.text}; }}
QListWidget::item {{ padding: 6px 10px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {colors.surface_subtle}; }}
QListWidget::item:selected {{ background: {colors.selection}; color: {colors.text}; border: 1px solid {colors.accent}; }}
QFrame[frameShape="6"] {{ border: 1px solid {colors.border}; border-radius: 8px; background: {colors.surface}; }}
QCheckBox {{ spacing: 6px; color: {colors.text}; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {colors.border_strong}; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {colors.border_strong}; border-radius: 4px; min-width: 20px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {colors.border}; }}
QSplitter::handle:hover {{ background: {colors.accent}; }}
QSplitter#workspaceSplitter::handle {{ background: transparent; }}
QSplitter#workspaceSplitter::handle:hover {{ background: transparent; }}
QMenuBar, QMenu {{ background: {colors.surface}; color: {colors.text}; }}
QMenuBar {{ border-bottom: 1px solid {colors.border}; padding: 2px; }}
QMenuBar::item, QMenu::item {{ padding: 6px 12px; border-radius: 4px; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {colors.selection}; color: {colors.text}; }}
QToolTip {{ background: {colors.tooltip}; color: {colors.tooltip_text}; border: 1px solid {colors.border_strong}; padding: 6px 10px; }}
QWidget#aiStatusCard {{ background: {colors.surface_subtle}; border: 1px solid {colors.border}; border-radius: 8px; }}
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

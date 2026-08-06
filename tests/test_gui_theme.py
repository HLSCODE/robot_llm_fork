from __future__ import annotations

import unittest

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from src.gui.theme import DARK_COLORS, LIGHT_COLORS, ThemeController, ThemeMode


class GuiThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.original_palette = QApplication.palette()
        self.original_stylesheet = self.application.styleSheet()

    def tearDown(self) -> None:
        QApplication.setPalette(self.original_palette)
        self.application.setStyleSheet(self.original_stylesheet)
        QApplication.processEvents()

    def test_light_and_dark_modes_apply_one_application_palette(self) -> None:
        controller = ThemeController(self.application, ThemeMode.LIGHT)

        self.assertEqual(
            QColor(LIGHT_COLORS.window),
            QApplication.palette().color(QPalette.ColorRole.Window),
        )
        controller.set_mode(ThemeMode.DARK)
        self.assertEqual(ThemeMode.DARK, controller.mode)
        self.assertEqual(
            QColor(DARK_COLORS.window),
            QApplication.palette().color(QPalette.ColorRole.Window),
        )
        self.assertEqual(
            QColor(DARK_COLORS.text),
            QApplication.palette().color(QPalette.ColorRole.Text),
        )
        self.assertEqual(
            QColor(DARK_COLORS.tooltip),
            QApplication.palette().color(QPalette.ColorRole.ToolTipBase),
        )
        self.assertIn(
            'QPushButton[themeRole="danger"]',
            self.application.styleSheet(),
        )

    def test_tooltips_are_compact_rounded_and_follow_each_theme(self) -> None:
        controller = ThemeController(self.application, ThemeMode.LIGHT)
        light_stylesheet = self.application.styleSheet()

        self.assertEqual(
            QColor(LIGHT_COLORS.tooltip),
            QApplication.palette().color(QPalette.ColorRole.ToolTipBase),
        )
        self.assertIn("QToolTip {", light_stylesheet)
        self.assertNotIn("QLabel#activityToolTip", light_stylesheet)
        self.assertIn("border-radius: 6px; padding: 3px 7px", light_stylesheet)
        self.assertIn(f"background: {LIGHT_COLORS.tooltip}", light_stylesheet)

        controller.set_mode(ThemeMode.DARK)
        dark_stylesheet = self.application.styleSheet()
        self.assertIn(f"background: {DARK_COLORS.tooltip}", dark_stylesheet)
        self.assertIn(f"color: {DARK_COLORS.tooltip_text}", dark_stylesheet)

    def test_surface_hierarchy_avoids_repeated_decorative_borders(self) -> None:
        ThemeController(self.application, ThemeMode.DARK)
        stylesheet = self.application.styleSheet()

        self.assertIn("QTabWidget::pane { border: none", stylesheet)
        self.assertIn('QFrame[frameShape="6"] { border: none', stylesheet)
        self.assertIn("QFrame#workbenchActivityBar {", stylesheet)
        self.assertNotIn("border-right", stylesheet)
        self.assertNotIn(
            "QListWidget::item:selected { background: #1e3a5f; "
            "color: #f1f5f9; border:",
            stylesheet,
        )

    def test_mode_change_is_emitted_only_for_an_actual_change(self) -> None:
        controller = ThemeController(self.application, ThemeMode.LIGHT)
        changes: list[ThemeMode] = []
        controller.mode_changed.connect(changes.append)

        controller.set_mode(ThemeMode.LIGHT)
        controller.set_mode(ThemeMode.DARK)

        self.assertEqual([ThemeMode.DARK], changes)

    def test_theme_mode_parser_rejects_unknown_values(self) -> None:
        self.assertIs(ThemeMode.SYSTEM, ThemeMode.parse(" SYSTEM "))
        with self.assertRaisesRegex(ValueError, "system, light, dark"):
            ThemeMode.parse("midnight")

    def test_system_mode_resolves_from_qt_system_color_scheme(self) -> None:
        controller = ThemeController(self.application, ThemeMode.DARK)

        controller.set_mode(ThemeMode.SYSTEM)

        expected = (
            ThemeMode.DARK
            if self.application.styleHints().colorScheme().name == "Dark"
            else ThemeMode.LIGHT
        )
        self.assertIs(expected, controller.effective_mode)


if __name__ == "__main__":
    unittest.main()

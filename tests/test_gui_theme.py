from __future__ import annotations

import unittest

from PySide6.QtCore import QFile, QPoint
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QStyle

from src.gui.theme import (
    DARK_COLORS,
    LIGHT_COLORS,
    ThemeController,
    ThemeMode,
    apply_consistent_base_style,
)
from src.gui.tooltips import TOOLTIP_SERVICE_OBJECT_NAME, ToolTipService
from src.gui.widget_style import COMBO_POPUP_GAP, QT_BASE_STYLE_NAME


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

    def test_theme_uses_fusion_before_stylesheet_decorates_the_style(self) -> None:
        self.application.setStyleSheet("")
        apply_consistent_base_style(self.application)

        self.assertEqual(
            QT_BASE_STYLE_NAME.casefold(),
            self.application.style().objectName().casefold(),
        )

    def test_dropdowns_and_menus_share_complete_subcontrol_styles(self) -> None:
        ThemeController(self.application, ThemeMode.LIGHT)
        light_stylesheet = self.application.styleSheet()

        for selector in (
            "QComboBox::drop-down",
            "QComboBox::down-arrow",
            "QComboBoxPrivateContainer",
            "QComboBox QAbstractItemView::item",
            "QSpinBox::up-arrow",
            "QDoubleSpinBox::down-arrow",
            "QMenuBar::item:pressed",
            "QMenu::separator",
            "QMenu::right-arrow",
            "QToolButton::menu-indicator",
        ):
            self.assertIn(selector, light_stylesheet)
        self.assertIn(f"border: 1px solid {LIGHT_COLORS.border}", light_stylesheet)
        self.assertIn(":/icons/chevron-down-on-light.svg", light_stylesheet)
        self.assertIn(":/icons/chevron-up-on-light.svg", light_stylesheet)
        self.assertTrue(QFile.exists(":/icons/chevron-down-on-light.svg"))
        self.assertTrue(QFile.exists(":/icons/chevron-up-on-light.svg"))

        ThemeController(self.application, ThemeMode.DARK)
        dark_stylesheet = self.application.styleSheet()
        self.assertIn(f"background: {DARK_COLORS.surface}", dark_stylesheet)
        self.assertIn(f"border: 1px solid {DARK_COLORS.border}", dark_stylesheet)
        self.assertIn(":/icons/chevron-down-on-dark.svg", dark_stylesheet)
        self.assertIn(":/icons/chevron-up-on-dark.svg", dark_stylesheet)
        self.assertTrue(QFile.exists(":/icons/chevron-down-on-dark.svg"))
        self.assertTrue(QFile.exists(":/icons/chevron-up-on-dark.svg"))

    def test_combo_popup_uses_dropdown_behavior_below_the_input(self) -> None:
        ThemeController(self.application, ThemeMode.LIGHT)
        combo = QComboBox()
        combo.addItems(["选项一", "选项二", "选项三"])
        combo.resize(240, 32)
        combo.move(100, 100)
        combo.show()
        QApplication.processEvents()

        self.assertEqual(
            0,
            combo.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup, None, combo),
        )
        combo.showPopup()
        QApplication.processEvents()
        input_bottom = combo.mapToGlobal(QPoint(0, combo.height())).y()
        popup = combo.view().window()
        self.assertGreaterEqual(
            popup.geometry().top(),
            input_bottom + COMBO_POPUP_GAP - 1,
        )
        self.assertFalse(popup.mask().isEmpty())
        self.assertFalse(popup.mask().contains(QPoint(0, 0)))
        self.assertTrue(popup.mask().contains(popup.rect().center()))

        combo.hidePopup()
        combo.close()
        combo.deleteLater()

    def test_tooltips_are_compact_rounded_and_follow_each_theme(self) -> None:
        controller = ThemeController(self.application, ThemeMode.LIGHT)
        light_stylesheet = self.application.styleSheet()

        self.assertEqual(
            QColor(LIGHT_COLORS.tooltip),
            QApplication.palette().color(QPalette.ColorRole.ToolTipBase),
        )
        self.assertNotIn("QToolTip", light_stylesheet)
        tooltip_service = self.application.findChild(
            ToolTipService,
            TOOLTIP_SERVICE_OBJECT_NAME,
        )
        self.assertIsNotNone(tooltip_service)

        controller.set_mode(ThemeMode.DARK)
        self.assertEqual(
            QColor(DARK_COLORS.tooltip),
            QApplication.palette().color(QPalette.ColorRole.ToolTipBase),
        )
        self.assertEqual(
            QColor(DARK_COLORS.tooltip_text),
            QApplication.palette().color(QPalette.ColorRole.ToolTipText),
        )

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

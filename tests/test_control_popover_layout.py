import pytest

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.application.end_effectors import ToolPresentation
from src.gui.icons import IconName
from src.gui.theme import ThemeController, ThemeMode
from src.gui.views.device import DeviceControlView
from src.gui.views.end_effector_controls import EndEffectorControls
from src.gui.views.workbench import WorkbenchPage, WorkbenchView


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
@pytest.mark.parametrize("font_pixels", [14, 22])
@pytest.mark.parametrize("height", [700, 300])
def test_actual_control_popover_expands_or_scrolls_without_overlapping(mode, font_pixels, height):
    app = QApplication.instance() or QApplication([])
    original_font, original_palette, original_sheet = app.font(), app.palette(), app.styleSheet()
    theme = ThemeController(app, mode)
    font = QFont(app.font())
    font.setPixelSize(font_pixels)
    app.setFont(font)
    controls = EndEffectorControls(
        lambda kind, arm: (
            [("rgd", "大寰旋转夹爪")]
            if kind == "gripper" and arm == "left"
            else ([("zx", "知行夹爪")] if kind == "gripper" else [])
        ),
        ("left", "right"),
        describe_tool=lambda identity: ToolPresentation(
            "夹爪",
            "",
            ("开", "关", "定位", "初始化", "相对旋转")
            if identity == "rgd"
            else ("开", "关", "定位"),
        ),
    )
    page = DeviceControlView()
    page.use_configured_end_effectors(controls)
    workbench = WorkbenchView(
        side_pages=(WorkbenchPage("actions", "动作", IconName.TASKS, QWidget()),),
        editor=QWidget(),
        bottom_pages=(
            WorkbenchPage("controls", "控制", IconName.CONTROLS, page, fit_content=True),
        ),
    )
    try:
        workbench.resize(880, height)
        workbench.show()
        workbench.toggle_bottom_page("controls")
        settle(app)
        buttons = [controls._buttons.itemAt(i).widget() for i in range(controls._buttons.count())]
        assert all(isinstance(button, QPushButton) for button in buttons)
        selector_bottom = controls.tool.mapTo(controls, QPoint(0, controls.tool.height())).y()
        assert min(button.mapTo(controls, QPoint()).y() for button in buttons) > selector_bottom
        actions_bottom = max(
            button.mapTo(controls, QPoint(0, button.height())).y() for button in buttons
        )
        assert controls._position_row.y() > actions_bottom
        assert controls._rotation_row.y() >= controls._position_row.geometry().bottom()
        assert page._pages["relay"].isHidden()
        assert page._body_page.isHidden()
        assert workbench.rect().contains(workbench.detail_panel.geometry())
        if height == 300:
            assert page.scroll_area.verticalScrollBar().maximum() > 0
            page.scroll_area.verticalScrollBar().setValue(
                page.scroll_area.verticalScrollBar().maximum()
            )
            settle(app)
            assert (
                page.scroll_area.viewport()
                .rect()
                .contains(controls.stop_rotation.mapTo(page.scroll_area.viewport(), QPoint(1, 1)))
            )
        else:
            assert workbench.detail_panel.height() > 300
            assert page.scroll_area.verticalScrollBar().maximum() == 0
            expanded = workbench.detail_panel.height()
            controls.arm.setCurrentIndex(1)
            settle(app)
            assert controls._rotation_row.isHidden()
            assert workbench.detail_panel.height() < expanded
            controls.arm.setCurrentIndex(0)
            settle(app)
            assert workbench.detail_panel.height() == expanded
    finally:
        workbench.close()
        workbench.deleteLater()
        settle(app)
        theme.deleteLater()
        app.setFont(original_font)
        app.setPalette(original_palette)
        app.setStyleSheet(original_sheet)

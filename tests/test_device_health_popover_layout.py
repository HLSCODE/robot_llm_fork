from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.gui.icons import IconName
from src.gui.theme import ThemeController, ThemeMode
from src.gui.views.device import DeviceHealthView
from src.gui.views.workbench import WorkbenchPage, WorkbenchView


def _settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("window_size", [(880, 720), (450, 300)])
@pytest.mark.parametrize("font_pixels", [12, 22])
def test_device_popover_fits_status_rows_without_clipping_or_excess_space(mode, window_size, font_pixels):
    app = QApplication.instance() or QApplication([])
    old_font, old_palette, old_sheet = app.font(), app.palette(), app.styleSheet()
    chinese_font = Path("C:/Windows/Fonts/msyh.ttc")
    if chinese_font.is_file():
        QFontDatabase.addApplicationFont(str(chinese_font))
        font = QFont("Microsoft YaHei")
    else:
        font = QFont(old_font)
    font.setPixelSize(font_pixels)
    app.setFont(font)
    theme = ThemeController(app, mode)
    page = DeviceHealthView()
    workbench = WorkbenchView(
        side_pages=(WorkbenchPage("actions", "动作", IconName.ACTIONS, QWidget()),),
        editor=QWidget(),
        bottom_pages=(WorkbenchPage("devices", "设备", IconName.DEVICES, page, fit_content=True),),
    )
    try:
        workbench.resize(*window_size)
        workbench.show()
        workbench.toggle_bottom_page("devices")
        _settle(app)
        initial_height = workbench.detail_panel.height()
        page.render_cameras((
            {"label": "左臂深度相机", "frame_received": True, "required": True},
            {"label": "右臂深度相机", "error": "未连接", "required": True},
            {"label": "头部深度相机", "error": "未连接"},
            {"label": "胸部深度相机", "error": "连接失败，无法启动视频流，请检查相机连接。"},
        ))
        page.render_end_effectors(
            [{"arm": "left", "mode": "fixed", "tool": "大寰旋转夹爪"},
             {"arm": "right", "mode": "fixed", "tool": "知行夹爪"}],
            [],
        )
        _settle(app)
        cameras = [label for label in page.findChildren(QLabel) if label.text().startswith("●  ")]
        assert len(cameras) == 4
        assert all(label.height() >= label.heightForWidth(label.width()) > 0 for label in cameras)
        for device in ("robot", "body", "relay"):
            label = page.findChild(QLabel, f"{device}_status_text")
            assert label is not None
            assert label.height() >= label.heightForWidth(label.width()) > 0
        assert workbench.rect().contains(workbench.detail_panel.geometry())

        scroll = page.scroll_area
        viewport = scroll.viewport()
        last = next(label for label in page.findChildren(QLabel) if label.text().startswith("末端设备"))
        assert scroll.horizontalScrollBar().maximum() == 0
        if window_size[1] == 300:
            assert scroll.verticalScrollBar().maximum() > 0
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            _settle(app)
        else:
            assert workbench.detail_panel.height() > initial_height
            assert scroll.verticalScrollBar().maximum() == 0
            bottom = last.mapTo(viewport, QPoint(0, last.height())).y()
            assert 0 <= viewport.height() - bottom <= 16
        assert viewport.rect().contains(last.mapTo(viewport, QPoint(1, last.height() - 1)))
        page.render_cameras(())
        page.render_end_effectors([], [])
        _settle(app)
        assert workbench.detail_panel.height() == initial_height
        assert sum(label.isVisible() for label in page.findChildren(QLabel, "cameraStatusEmpty")) == 1
        assert not any(
            label.isVisible() and label.text().startswith("●  ")
            for label in page.findChildren(QLabel)
        )
    finally:
        workbench.close()
        workbench.deleteLater()
        theme.deleteLater()
        app.setFont(old_font)
        app.setPalette(old_palette)
        app.setStyleSheet(old_sheet)
        _settle(app)

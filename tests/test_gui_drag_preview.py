from __future__ import annotations

from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.drag_preview import create_drag_card_preview
from src.gui.theme import DARK_COLORS, LIGHT_COLORS, build_palette
from src.gui.drag_preview_style import (
    DRAG_CARD_MAX_SCALE,
    DRAG_CARD_MIN_SCALE,
    DRAG_CARD_OPACITY,
    DRAG_CARD_RELATIVE_SCALE,
    DRAG_PREVIEW_MAX_HEIGHT,
    DRAG_PREVIEW_MAX_WIDTH,
    bounded_drag_preview_scale,
)


def test_drag_card_preview_tracks_canvas_scale_with_bounded_size() -> None:
    application = QApplication.instance() or QApplication([])
    widget = QWidget()

    def create(scale: float):
        return create_drag_card_preview(
            widget,
            title="ready_to_place",
            subtitle="机械臂移动",
            icon=QIcon(),
            accent=QColor("#6366f1"),
            canvas_scale=scale,
        )
    minimum = create(0.01)
    normal = create(1.0)
    maximum = create(10.0)
    clamped_minimum = create(DRAG_CARD_MIN_SCALE)
    clamped_maximum = create(DRAG_CARD_MAX_SCALE)

    assert minimum.pixmap.size() == clamped_minimum.pixmap.size()
    assert maximum.pixmap.size() == clamped_maximum.pixmap.size()
    assert minimum.pixmap.width() < normal.pixmap.width() < maximum.pixmap.width()
    assert minimum.pixmap.height() < normal.pixmap.height() < maximum.pixmap.height()
    preview_alpha = normal.pixmap.toImage().pixelColor(
        8,
        normal.pixmap.height() // 2,
    ).alpha()
    assert 0 < preview_alpha < 255
    assert 0.0 < DRAG_CARD_OPACITY < 1.0
    assert normal.hotspot.y() > normal.pixmap.height()
    widget.close()
    application.processEvents()


def test_in_canvas_drag_preview_uses_shared_relative_scale_and_bounds() -> None:
    assert bounded_drag_preview_scale(232.0, 58.0) == DRAG_CARD_RELATIVE_SCALE
    large_scale = bounded_drag_preview_scale(500.0, 500.0)
    assert 500.0 * large_scale <= DRAG_PREVIEW_MAX_WIDTH
    assert 500.0 * large_scale <= DRAG_PREVIEW_MAX_HEIGHT


def test_drag_preview_uses_current_application_theme_not_stale_widget_palette() -> None:
    application = QApplication.instance() or QApplication([])
    original_palette = application.palette()
    widget = QWidget()
    try:
        application.setPalette(build_palette(LIGHT_COLORS))
        widget.setPalette(build_palette(DARK_COLORS))
        preview = create_drag_card_preview(
            widget,
            title="ready_to_place",
            subtitle="机械臂移动",
            icon=QIcon(),
            accent=QColor(LIGHT_COLORS.accent),
            canvas_scale=1.0,
        )

        image = preview.pixmap.toImage()
        surface_pixel = image.pixelColor(image.width() - 12, image.height() // 2)
        assert widget.palette().base().color() == QColor(DARK_COLORS.surface)
        assert surface_pixel.lightnessF() > 0.8
        assert 0 < surface_pixel.alpha() < 255
    finally:
        application.setPalette(original_palette)
        widget.close()
        application.processEvents()

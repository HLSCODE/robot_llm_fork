from __future__ import annotations

from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.drag_preview import (
    DRAG_CARD_MAX_SCALE,
    DRAG_CARD_MIN_SCALE,
    DRAG_CARD_OPACITY,
    create_drag_card_preview,
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

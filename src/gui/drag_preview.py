"""Theme-aware drag thumbnails shared by GUI resource libraries."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .drag_preview_style import (
    DRAG_CARD_MAX_SCALE,
    DRAG_CARD_MIN_SCALE,
    DRAG_CARD_OPACITY,
    DRAG_CARD_RELATIVE_SCALE,
)
from .views.workflow_canvas.tokens import NODE_HEIGHT, NODE_WIDTH


_REFERENCE_WIDTH = NODE_WIDTH * DRAG_CARD_RELATIVE_SCALE
_REFERENCE_HEIGHT = NODE_HEIGHT * DRAG_CARD_RELATIVE_SCALE
_REFERENCE_HOTSPOT_X = 24.0
_REFERENCE_HOTSPOT_GAP = 10.0


@dataclass(frozen=True, slots=True)
class DragCardPreview:
    pixmap: QPixmap
    hotspot: QPoint


def create_drag_card_preview(
    widget: QWidget,
    *,
    title: str,
    subtitle: str,
    icon: QIcon,
    accent: QColor,
    canvas_scale: float,
) -> DragCardPreview:
    """Render a node-like drag preview scaled from the live canvas transform."""
    preview_scale = min(
        DRAG_CARD_MAX_SCALE,
        max(DRAG_CARD_MIN_SCALE, canvas_scale),
    )
    width = round(_REFERENCE_WIDTH * preview_scale)
    height = round(_REFERENCE_HEIGHT * preview_scale)
    device_pixel_ratio = max(1.0, widget.devicePixelRatioF())
    pixmap = QPixmap(
        round(width * device_pixel_ratio),
        round(height * device_pixel_ratio),
    )
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    # Drag thumbnails belong to the application chrome, not to a list widget's
    # potentially stale/local palette.  This also matches in-canvas previews.
    palette = QApplication.palette()
    surface = palette.base().color()
    text = palette.text().color()
    secondary_text = palette.placeholderText().color()
    border = palette.mid().color()
    shadow = QColor(0, 0, 0, 70)
    card_rect = QRectF(3.0, 2.0, _REFERENCE_WIDTH - 6.0, _REFERENCE_HEIGHT - 5.0)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(DRAG_CARD_OPACITY)
    painter.scale(width / _REFERENCE_WIDTH, height / _REFERENCE_HEIGHT)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(shadow)
    painter.drawRoundedRect(card_rect.translated(0.0, 1.5), 8.0, 8.0)
    painter.setBrush(surface)
    painter.setPen(QPen(border, 1.0))
    painter.drawRoundedRect(card_rect, 8.0, 8.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(accent)
    painter.drawRoundedRect(
        QRectF(card_rect.left(), card_rect.top(), 4.0, card_rect.height()),
        2.0,
        2.0,
    )
    icon.paint(
        painter,
        15,
        16,
        20,
        20,
        Qt.AlignmentFlag.AlignCenter,
        QIcon.Mode.Normal,
    )

    title_font = widget.font()
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(text)
    title_text = painter.fontMetrics().elidedText(
        title.strip() or "未命名",
        Qt.TextElideMode.ElideRight,
        150,
    )
    painter.drawText(
        QRectF(43.0, 5.0, 150.0, 20.0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        title_text,
    )

    subtitle_font = widget.font()
    if subtitle_font.pointSizeF() > 0:
        subtitle_font.setPointSizeF(max(8.0, subtitle_font.pointSizeF() - 1.0))
    painter.setFont(subtitle_font)
    painter.setPen(secondary_text)
    subtitle_text = painter.fontMetrics().elidedText(
        subtitle.strip(),
        Qt.TextElideMode.ElideRight,
        150,
    )
    painter.drawText(
        QRectF(43.0, 25.0, 150.0, 18.0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        subtitle_text,
    )
    painter.end()
    hotspot_scale = width / _REFERENCE_WIDTH
    hotspot = QPoint(
        round(_REFERENCE_HOTSPOT_X * hotspot_scale),
        height + round(_REFERENCE_HOTSPOT_GAP * hotspot_scale),
    )
    return DragCardPreview(pixmap=pixmap, hotspot=hotspot)

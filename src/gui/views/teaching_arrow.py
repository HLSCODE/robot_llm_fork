"""Palette-aware arrow-shaped teaching buttons, without rectangular chrome."""

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPaintEvent, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QPushButton


class TeachingArrowButton(QPushButton):
    def __init__(self, *, positive: bool) -> None:
        super().__init__("+" if positive else "−")
        self._positive = positive
        self.setAutoRepeat(False)
        self.setMinimumSize(64, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, 40)

    def _arrow(self) -> QPolygonF:
        width = min(72.0, self.width() - 8.0)
        height = min(32.0, self.height() - 8.0)
        left = (self.width() - width) / 2
        top = (self.height() - height) / 2
        points = [(0, .22), (.65, .22), (.65, 0), (1, .5),
                  (.65, 1), (.65, .78), (0, .78)]
        return QPolygonF([
            QPointF(left + (x if self._positive else 1 - x) * width, top + y * height)
            for x, y in points
        ])

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802
        return self._arrow().containsPoint(QPointF(pos), Qt.FillRule.OddEvenFill)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        # Deliberately do not invoke QPushButton's rectangular style painting.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        highlighted = self.isEnabled() and (self.underMouse() or self.hasFocus() or self.isDown())
        fill = palette.color(group, QPalette.ColorRole.Highlight if highlighted else QPalette.ColorRole.ButtonText)
        if self.isDown():
            fill = fill.darker(120)
        painter.setBrush(fill)
        painter.setPen(Qt.PenStyle.NoPen)
        polygon = self._arrow()
        painter.drawPolygon(polygon)
        bounds = polygon.boundingRect()
        center_x = bounds.left() + bounds.width() * (.32 if self._positive else .68)
        ink = palette.color(group, QPalette.ColorRole.HighlightedText if highlighted else QPalette.ColorRole.Window)
        # Draw signs as vectors too, independent of platform glyph availability.
        painter.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(center_x - 4, bounds.center().y()), QPointF(center_x + 4, bounds.center().y()))
        if self._positive:
            painter.drawLine(QPointF(center_x, bounds.center().y() - 4), QPointF(center_x, bounds.center().y() + 4))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ink, 1, Qt.PenStyle.DotLine))
            painter.drawEllipse(QRectF(center_x - 7, bounds.center().y() - 7, 14, 14))
        painter.end()

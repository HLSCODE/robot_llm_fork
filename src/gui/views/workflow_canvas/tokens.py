"""Shared visual and interaction tokens for the workflow canvas.

Neutral colors are derived from the active Qt palette so the canvas follows
light, dark and high-contrast system themes. Fixed colors are reserved for
domain categories and semantic states.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ....domain.models import ActionType, SequenceItemStatus


CANVAS_MARGIN = 24.0
NODE_WIDTH = 300.0
NODE_HEIGHT = 88.0
LOOP_HEADER_HEIGHT = 76.0
LOOP_NODE_WIDTH = 440.0
LOOP_CHILD_HEIGHT = 76.0
LOOP_CHILD_GAP = 52.0
LOOP_SECTION_GAP = 52.0
LOOP_FOOTER_WIDTH = 128.0
LOOP_FOOTER_HEIGHT = 42.0
MAX_VISIBLE_LOOP_CHILDREN = 5
PARALLEL_HEADER_HEIGHT = 76.0
PARALLEL_BRANCH_WIDTH = 244.0
PARALLEL_BRANCH_GAP = 16.0
PARALLEL_BRANCH_PADDING = 16.0
PARALLEL_BRANCH_HEADER_HEIGHT = 42.0
PARALLEL_CHILD_HEIGHT = 64.0
PARALLEL_CHILD_GAP = 28.0
PARALLEL_FOOTER_HEIGHT = 42.0
PARALLEL_SECTION_GAP = 24.0
NODE_GAP = 54.0
INSERT_TARGET_SIZE = 44.0
MIN_SCALE = 0.45
MAX_SCALE = 2.0
FIT_PADDING = 24.0
TOUCH_TARGET_SIZE = 44
TOOLBAR_SPACING = 8
NODE_RADIUS = 12.0
NODE_DRAG_THRESHOLD = 8.0


@dataclass(frozen=True, slots=True)
class CanvasColors:
    surface: QColor
    canvas: QColor
    text: QColor
    secondary_text: QColor
    border: QColor
    accent: QColor
    edge: QColor


def canvas_colors(palette: QPalette | None = None) -> CanvasColors:
    active = QApplication.palette() if palette is None else palette
    return CanvasColors(
        surface=active.color(QPalette.ColorRole.Base),
        canvas=active.color(QPalette.ColorRole.Window),
        text=active.color(QPalette.ColorRole.Text),
        secondary_text=active.color(QPalette.ColorRole.PlaceholderText),
        border=active.color(QPalette.ColorRole.Mid),
        accent=active.color(QPalette.ColorRole.Highlight),
        edge=active.color(QPalette.ColorRole.Mid),
    )


def canvas_font(*, emphasis: bool = False, secondary: bool = False) -> QFont:
    font = QFont(QApplication.font())
    point_size = font.pointSizeF()
    if point_size > 0:
        font.setPointSizeF(max(9.0, point_size - (1.0 if secondary else 0.0)))
    font.setBold(emphasis)
    return font


def contrasting_text(background: QColor) -> QColor:
    """Return readable black/white text for a semantic background color."""
    linear_luminance = (
        0.2126 * background.redF()
        + 0.7152 * background.greenF()
        + 0.0722 * background.blueF()
    )
    return QColor("#111827") if linear_luminance > 0.58 else QColor("#ffffff")

ACTION_COLORS = {
    ActionType.MOVE: QColor("#6366f1"),
    ActionType.BASE_MOVE: QColor("#ef4444"),
    ActionType.MANIPULATE: QColor("#f97316"),
    ActionType.WAIT: QColor("#f59e0b"),
    ActionType.INSPECT: QColor("#10b981"),
    ActionType.CHANGE_GUN: QColor("#8b5cf6"),
    ActionType.VISION_CAPTURE: QColor("#0ea5e9"),
    ActionType.VISION_RELOCALIZE: QColor("#06b6d4"),
    ActionType.TRAJECTORY: QColor("#14b8a6"),
}

STATUS_LABELS = {
    SequenceItemStatus.PENDING: "等待",
    SequenceItemStatus.RUNNING: "执行中",
    SequenceItemStatus.SUCCESS: "完成",
    SequenceItemStatus.FAILED: "失败",
}

STATUS_COLORS = {
    SequenceItemStatus.PENDING: QColor("#64748b"),
    SequenceItemStatus.RUNNING: QColor("#f59e0b"),
    SequenceItemStatus.SUCCESS: QColor("#16a34a"),
    SequenceItemStatus.FAILED: QColor("#dc2626"),
}

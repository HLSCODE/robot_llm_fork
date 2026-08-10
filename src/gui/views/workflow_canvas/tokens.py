"""Shared visual and interaction tokens for the workflow canvas.

Neutral colors are derived from the active Qt palette so the canvas follows
light, dark and high-contrast system themes. Fixed colors are reserved for
domain categories and semantic states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ....domain.models import ActionType, SequenceItemStatus


CANVAS_MARGIN = 24.0
NODE_WIDTH = 232.0
NODE_HEIGHT = 58.0
LOOP_HEADER_HEIGHT = 58.0
LOOP_NODE_WIDTH = 416.0
LOOP_CHILD_HEIGHT = 58.0
LOOP_CHILD_GAP = 36.0
LOOP_SECTION_GAP = 36.0
LOOP_FOOTER_WIDTH = 116.0
LOOP_FOOTER_HEIGHT = 38.0
MAX_VISIBLE_LOOP_CHILDREN = 5
PARALLEL_HEADER_HEIGHT = 66.0
PARALLEL_BRANCH_WIDTH = 220.0
PARALLEL_BRANCH_GAP = 12.0
PARALLEL_BRANCH_PADDING = 12.0
PARALLEL_BRANCH_HEADER_HEIGHT = 38.0
PARALLEL_CHILD_HEIGHT = 58.0
PARALLEL_CHILD_GAP = 24.0
PARALLEL_FOOTER_HEIGHT = 38.0
PARALLEL_SECTION_GAP = 20.0
NODE_GAP = 40.0
INSERT_TARGET_SIZE = 44.0
MIN_SCALE = 0.45
MAX_SCALE = 2.0
FIT_PADDING = 24.0
TOUCH_TARGET_SIZE = 44
TOOLBAR_SPACING = 8
NODE_RADIUS = 10.0
NODE_DRAG_THRESHOLD = 8.0
DRAG_PREVIEW_MAX_WIDTH = 224.0
DRAG_PREVIEW_MAX_HEIGHT = 144.0
DRAG_PREVIEW_OPACITY = 0.94
DRAG_SOURCE_OPACITY = 0.28
INSERT_TARGET_ACTIVATION_DISTANCE = 88.0
INSERT_TARGET_HINT_WIDTH = 142.0
INSERT_TARGET_PULSE_DURATION_MS = 840
INSERT_HOVER_TRANSITION_MS = 140
EXECUTION_PULSE_DURATION_MS = 1100


@dataclass(frozen=True, slots=True)
class CanvasColors:
    surface: QColor
    canvas: QColor
    text: QColor
    secondary_text: QColor
    border: QColor
    accent: QColor
    edge: QColor


class ControlFlowKind(str, Enum):
    """Visual category for nodes that govern execution rather than perform work."""

    LOOP = "loop"
    PARALLEL = "parallel"


@dataclass(frozen=True, slots=True)
class ControlFlowColors:
    header: QColor
    header_text: QColor
    accent: QColor
    path: QColor
    footer: QColor
    footer_text: QColor


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


def control_flow_colors(
    kind: ControlFlowKind,
    palette: QPalette | None = None,
) -> ControlFlowColors:
    """Return theme-aware semantic colors for one control-flow container."""
    active = QApplication.palette() if palette is None else palette
    is_dark = active.color(QPalette.ColorRole.Window).lightnessF() < 0.5
    if kind is ControlFlowKind.LOOP:
        return ControlFlowColors(
            header=QColor("#3b3018" if is_dark else "#fff4cc"),
            header_text=QColor("#fde68a" if is_dark else "#713f12"),
            accent=QColor("#fbbf24" if is_dark else "#d97706"),
            path=QColor("#eab308" if is_dark else "#b45309"),
            footer=QColor("#334155" if is_dark else "#e2e8f0"),
            footer_text=QColor("#f8fafc" if is_dark else "#334155"),
        )
    return ControlFlowColors(
        header=QColor("#302446" if is_dark else "#f3e8ff"),
        header_text=QColor("#e9d5ff" if is_dark else "#581c87"),
        accent=QColor("#a78bfa" if is_dark else "#7c3aed"),
        path=QColor("#8b5cf6" if is_dark else "#6d28d9"),
        footer=QColor("#334155" if is_dark else "#e2e8f0"),
        footer_text=QColor("#f8fafc" if is_dark else "#334155"),
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
        0.2126 * background.redF() + 0.7152 * background.greenF() + 0.0722 * background.blueF()
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

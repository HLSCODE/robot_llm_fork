"""Shared visual and interaction tokens for the workflow canvas."""

from __future__ import annotations

from PySide6.QtGui import QColor

from ....domain.models import ActionType, SequenceItemStatus


CANVAS_MARGIN = 24.0
NODE_WIDTH = 300.0
NODE_HEIGHT = 88.0
LOOP_HEADER_HEIGHT = 76.0
LOOP_CHILD_HEIGHT = 28.0
MAX_VISIBLE_LOOP_CHILDREN = 5
NODE_GAP = 54.0
INSERT_TARGET_SIZE = 44.0
MIN_SCALE = 0.45
MAX_SCALE = 2.0

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

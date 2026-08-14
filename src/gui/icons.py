"""Palette-aware icons loaded exclusively from the compiled Qt resource."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, QFile, QIODevice, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ..domain.models import ActionDefinition, ActionType
from . import resources_rc as _resources_rc  # noqa: F401


class IconName(str, Enum):
    TASKS = "tasks"
    ACTIONS = "actions"
    ACTION_MOVE = "action-move"
    ACTION_ARM_MOVE = "action-arm-move"
    ACTION_ARM_RELATIVE = "action-arm-relative"
    ACTION_BODY_LIFT = "action-body-lift"
    ACTION_BASE_MOVE = "action-base-move"
    ACTION_MANIPULATE = "action-manipulate"
    ACTION_TOOL_CHANGER = "action-tool-changer"
    ACTION_RELAY = "action-relay"
    ACTION_GRIPPER = "action-gripper"
    ACTION_PIPETTE = "action-pipette"
    ACTION_NECK = "action-neck"
    ACTION_CIRCLE_DISPENSE = "action-circle-dispense"
    ACTION_POWDER = "action-powder"
    ACTION_SMART_POWDER = "action-smart-powder"
    ACTION_EXPRESSION = "action-expression"
    ACTION_WAIT = "action-wait"
    ACTION_INSPECT = "action-inspect"
    ACTION_TOOL_CHANGE = "action-tool-change"
    ACTION_VISION = "action-vision"
    ACTION_LOCATE = "action-locate"
    ACTION_TRAJECTORY = "action-trajectory"
    WORKFLOW = "workflow"
    ASSISTANT = "assistant"
    DEVICES = "devices"
    POSES = "poses"
    CONTROLS = "controls"
    LOGS = "logs"
    PROBLEM_ERROR = "problem-error"
    PROBLEM_WARNING = "problem-warning"
    OPEN = "open"
    SAVE = "save"
    INSERT = "insert"
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    CLEAR = "clear"
    CAMERA = "camera"
    FIT = "fit"
    ZOOM_RESET = "zoom-reset"
    UNDO = "undo"
    REDO = "redo"
    MOVE_UP = "move-up"
    MOVE_DOWN = "move-down"
    LOOP = "loop"
    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"
    QUICK_STOP = "quick-stop"
    EMERGENCY = "emergency"
    CLOSE = "close"
    WINDOW_MINIMIZE = "window-minimize"
    WINDOW_MAXIMIZE = "window-maximize"
    WINDOW_RESTORE = "window-restore"
    SHORTCUTS = "shortcuts"


ACTION_TYPE_ICONS: dict[ActionType, IconName] = {
    ActionType.MOVE: IconName.ACTION_MOVE,
    ActionType.BASE_MOVE: IconName.ACTION_BASE_MOVE,
    ActionType.MANIPULATE: IconName.ACTION_MANIPULATE,
    ActionType.WAIT: IconName.ACTION_WAIT,
    ActionType.INSPECT: IconName.ACTION_INSPECT,
    ActionType.CHANGE_GUN: IconName.ACTION_TOOL_CHANGE,
    ActionType.VISION_CAPTURE: IconName.ACTION_VISION,
    ActionType.VISION_RELOCALIZE: IconName.ACTION_LOCATE,
    ActionType.TRAJECTORY: IconName.ACTION_TRAJECTORY,
}


def action_type_icon(action_type: ActionType) -> IconName:
    """Return the semantic SVG icon for one concrete action behavior."""
    return ACTION_TYPE_ICONS.get(action_type, IconName.ACTIONS)


MOVE_TARGET_ICONS: dict[str, IconName] = {
    "机械臂": IconName.ACTION_ARM_MOVE,
    "机械臂相对": IconName.ACTION_ARM_RELATIVE,
    "身体": IconName.ACTION_BODY_LIFT,
}

MANIPULATOR_ICONS: dict[str, IconName] = {
    "快换手": IconName.ACTION_TOOL_CHANGER,
    "继电器": IconName.ACTION_RELAY,
    "夹爪": IconName.ACTION_GRIPPER,
    "吸液枪": IconName.ACTION_PIPETTE,
    "颈部": IconName.ACTION_NECK,
    "右臂转圈注液": IconName.ACTION_CIRCLE_DISPENSE,
    "加粉装置": IconName.ACTION_POWDER,
    "智能加粉": IconName.ACTION_SMART_POWDER,
    "表情屏": IconName.ACTION_EXPRESSION,
    "expression": IconName.ACTION_EXPRESSION,
}


def action_icon(action: ActionDefinition) -> IconName:
    """Resolve the most specific robot behavior icon for an action."""
    if action.type is ActionType.MOVE:
        target = str(action.parameters.get("目标", "")).strip()
        return MOVE_TARGET_ICONS.get(target, IconName.ACTION_ARM_MOVE)
    if action.type is ActionType.MANIPULATE:
        executor = str(
            action.parameters.get("执行器", action.parameters.get("executor", ""))
        ).strip()
        return MANIPULATOR_ICONS.get(executor, IconName.ACTION_MANIPULATE)
    return action_type_icon(action.type)


def themed_icon(
    widget: QWidget,
    name: IconName,
    *,
    size: int = 20,
    color: QColor | None = None,
) -> QIcon:
    """Render a monochrome SVG for common 1x/2x/3x display scales."""
    source = _read_svg(name)
    foreground = color or widget.palette().buttonText().color()
    colored_source = source.replace(b"#000000", foreground.name().encode("ascii"))
    renderer = QSvgRenderer(QByteArray(colored_source))
    if not renderer.isValid():
        raise ValueError(f"invalid GUI SVG resource: {name.value}")

    icon = QIcon()
    for scale in (1, 2, 3):
        physical_size = size * scale
        pixmap = QPixmap(physical_size, physical_size)
        pixmap.fill(QColor(0, 0, 0, 0))
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _read_svg(name: IconName) -> bytes:
    resource = QFile(f":/icons/{name.value}.svg")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise FileNotFoundError(f"missing GUI icon resource: {name.value}")
    try:
        return bytes(resource.readAll())
    finally:
        resource.close()

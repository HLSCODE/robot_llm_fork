"""Palette-aware icons loaded exclusively from the compiled Qt resource."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, QFile, QIODevice, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from . import resources_rc as _resources_rc  # noqa: F401


class IconName(str, Enum):
    TASKS = "tasks"
    ACTIONS = "actions"
    ASSISTANT = "assistant"
    DEVICES = "devices"
    POSES = "poses"
    CONTROLS = "controls"
    LOGS = "logs"
    OPEN = "open"
    INSERT = "insert"
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
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

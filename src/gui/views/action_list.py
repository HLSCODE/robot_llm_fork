"""Action-library list view used by the workflow editor."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from ...domain.models import ActionDefinition, ActionType


class ActionListWidget(QListWidget):
    action_selected = Signal(ActionDefinition)

    _TYPE_STYLE = {
        ActionType.MOVE: ("🦾", QColor(99, 102, 241)),
        ActionType.BASE_MOVE: ("🚗", QColor(239, 68, 68)),
        ActionType.MANIPULATE: ("⚡", QColor(249, 115, 22)),
        ActionType.WAIT: ("⏳", QColor(245, 158, 11)),
        ActionType.INSPECT: ("🔍", QColor(16, 185, 129)),
        ActionType.CHANGE_GUN: ("🔧", QColor(139, 92, 246)),
        ActionType.VISION_CAPTURE: ("👁", QColor(14, 165, 233)),
        ActionType.VISION_RELOCALIZE: ("📍", QColor(6, 182, 212)),
        ActionType.TRAJECTORY: ("📐", QColor(20, 184, 166)),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("动作库")
        self.setDragEnabled(True)
        self.setViewMode(self.ViewMode.ListMode)
        self.setIconSize(QSize(44, 44))
        self.setSpacing(4)
        self.setResizeMode(self.ResizeMode.Adjust)
        self.itemDoubleClicked.connect(self._emit_selected_action)

    def _emit_selected_action(self, item: QListWidgetItem) -> None:
        action = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(action, ActionDefinition):
            self.action_selected.emit(action)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        del supported_actions
        current_item = self.currentItem()
        if current_item is None:
            return
        action = current_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(action, ActionDefinition):
            return
        mime = QMimeData()
        mime.setData(
            "application/x-action",
            json.dumps(action.to_dict()).encode("utf-8"),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(current_item.icon().pixmap(60, 60))
        drag.exec(Qt.DropAction.CopyAction)

    def add_action(self, action: ActionDefinition) -> None:
        item = QListWidgetItem(action.name)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        item.setSizeHint(QSize(100, 52))
        item.setIcon(self._get_icon_for_type(action.type))
        parameters = ", ".join(
            f"{name}={value}"
            for name, value in list(action.parameters.items())[:4]
        )
        item.setToolTip(
            f"{action.name}\n类型：{action.type.value}\n参数：{parameters or '无'}"
        )
        item.setData(Qt.ItemDataRole.UserRole, action)
        self.addItem(item)

    def get_selected_action(self) -> ActionDefinition | None:
        current = self.currentItem()
        if current is None:
            return None
        action = current.data(Qt.ItemDataRole.UserRole)
        return action if isinstance(action, ActionDefinition) else None

    def _get_icon_for_type(self, action_type: ActionType) -> QIcon:
        emoji, color = self._TYPE_STYLE.get(
            action_type,
            ("📋", QColor(148, 163, 184)),
        )
        return self._create_rich_icon(color, emoji)

    @staticmethod
    def _create_rich_icon(color: QColor, emoji: str) -> QIcon:
        size = 44
        pixmap = QPixmap(size, size)
        pixmap.setDevicePixelRatio(1.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, color.lighter(125))
        gradient.setColorAt(1.0, color)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
        font = QFont()
        font.setPointSize(18)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            pixmap.rect(),
            Qt.AlignmentFlag.AlignCenter,
            emoji,
        )
        painter.end()
        return QIcon(pixmap)

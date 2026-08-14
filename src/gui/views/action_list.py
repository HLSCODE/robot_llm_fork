"""Action-library list view used by the workflow editor."""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from ...domain.models import ActionDefinition, ActionType
from ..icons import action_icon, themed_icon
from ..drag_preview import create_drag_card_preview
from .workflow_canvas.tokens import ACTION_COLORS


ACTION_TYPE_LABELS = {
    ActionType.MOVE: "机械臂移动",
    ActionType.BASE_MOVE: "底盘移动",
    ActionType.MANIPULATE: "执行器",
    ActionType.WAIT: "等待",
    ActionType.INSPECT: "检测",
    ActionType.CHANGE_GUN: "换枪",
    ActionType.VISION_CAPTURE: "视觉抓取",
    ActionType.VISION_RELOCALIZE: "视觉重定位",
    ActionType.TRAJECTORY: "轨迹",
}


class ActionListWidget(QListWidget):
    action_selected = Signal(ActionDefinition)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("动作库")
        self.setDragEnabled(True)
        self.setViewMode(self.ViewMode.ListMode)
        self.setIconSize(QSize(28, 28))
        self.setSpacing(2)
        self.setResizeMode(self.ResizeMode.Adjust)
        self._canvas_scale_provider: Callable[[], float] = lambda: 1.0
        self.itemDoubleClicked.connect(self._emit_selected_action)

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        self._canvas_scale_provider = provider

    def _emit_selected_action(self, item: QListWidgetItem) -> None:
        action = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(action, ActionDefinition):
            self.action_selected.emit(action)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() not in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            return
        for index in range(self.count()):
            item = self.item(index)
            action = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(action, ActionDefinition):
                item.setIcon(self._get_icon_for_action(action))

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
        preview = create_drag_card_preview(
            self,
            title=action.name,
            subtitle=ACTION_TYPE_LABELS.get(action.type, action.type.value),
            icon=self._get_icon_for_action(action),
            accent=ACTION_COLORS.get(action.type, QColor("#64748b")),
            canvas_scale=self._canvas_scale_provider(),
        )
        drag.setPixmap(preview.pixmap)
        drag.setHotSpot(preview.hotspot)
        drag.exec(Qt.DropAction.CopyAction)

    def add_action(self, action: ActionDefinition) -> None:
        item = QListWidgetItem(action.name)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        item.setSizeHint(QSize(100, 40))
        item.setIcon(self._get_icon_for_action(action))
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

    def _get_icon_for_action(self, action: ActionDefinition) -> QIcon:
        color = ACTION_COLORS.get(
            action.type,
            QColor("#64748b"),
        )
        return themed_icon(
            self,
            action_icon(action),
            size=20,
            color=color,
        )

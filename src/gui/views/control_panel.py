"""Workflow editing and execution controls."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..icons import IconName
from ..theme import set_theme_role
from ..toolbars import IconToolButton, TOOL_BUTTON_ICON_SIZE


EXECUTION_COMMAND_HIT_SIZE = 44
EXECUTION_COMMAND_ICON_SIZE = 20


class ControlPanel(QWidget):
    save_clicked = Signal()
    clear_clicked = Signal()
    fit_clicked = Signal()
    reset_zoom_clicked = Signal()
    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    quick_stop_clicked = Signal()
    emergency_stop_clicked = Signal()
    move_up_clicked = Signal()
    move_down_clicked = Signal()
    edit_clicked = Signal()
    repeat_clicked = Signal()
    delete_clicked = Signal()
    undo_clicked = Signal()
    redo_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workflowCommandBar")
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 4, 6, 4)
        self.edit_command_row = QHBoxLayout()
        self.edit_command_row.setSpacing(2)
        self.execution_command_row = QHBoxLayout()
        self.execution_command_row.setSpacing(2)
        layout.addLayout(self.edit_command_row)
        layout.addLayout(self.execution_command_row)

        self.save_btn = self._button(
            IconName.SAVE,
            "将当前流程保存为任务 (Ctrl+S)",
            self.save_clicked.emit,
        )
        self.undo_btn = self._button(IconName.UNDO, "撤销", self.undo_clicked.emit)
        self.redo_btn = self._button(IconName.REDO, "重做", self.redo_clicked.emit)
        self.clear_btn = self._button(
            IconName.CLEAR,
            "清空画布",
            self.clear_clicked.emit,
        )
        self.fit_btn = self._button(
            IconName.FIT,
            "画布适合内容",
            self.fit_clicked.emit,
        )
        self.reset_zoom_btn = self._button(
            IconName.ZOOM_RESET,
            "恢复 100% 缩放",
            self.reset_zoom_clicked.emit,
        )
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        for button in (
            self.save_btn,
            self.undo_btn,
            self.redo_btn,
            self.fit_btn,
            self.reset_zoom_btn,
            self._button(IconName.MOVE_UP, "上移节点", self.move_up_clicked.emit),
            self._button(IconName.MOVE_DOWN, "下移节点", self.move_down_clicked.emit),
            self._button(IconName.EDIT, "修改节点", self.edit_clicked.emit),
            self._button(IconName.LOOP, "将选中节点设为循环", self.repeat_clicked.emit),
            self._button(IconName.DELETE, "删除选中节点", self.delete_clicked.emit),
            self.clear_btn,
        ):
            self.edit_command_row.addWidget(button)
        self.edit_command_row.addStretch(1)

        self.start_btn = self._execution_button(
            IconName.PLAY,
            "开始执行",
            self.start_clicked.emit,
        )
        self.pause_btn = self._execution_button(
            IconName.PAUSE,
            "暂停执行",
            self.pause_clicked.emit,
        )
        self.stop_btn = self._execution_button(
            IconName.STOP,
            "停止任务",
            self.stop_clicked.emit,
        )
        self.quick_stop_btn = self._execution_button(
            IconName.QUICK_STOP,
            "快速停止",
            self.quick_stop_clicked.emit,
        )
        self.emergency_stop_btn = self._execution_button(
            IconName.EMERGENCY,
            "设备急停",
            self.emergency_stop_clicked.emit,
        )
        set_theme_role(self.start_btn, "success")
        set_theme_role(self.pause_btn, "warning")
        self.stop_btn.setAccessibleName("停止任务")
        self.stop_btn.setToolTip(
            "请求当前任务在可中断点停止；不会触发设备硬件急停"
        )
        set_theme_role(self.stop_btn, "danger")
        self.quick_stop_btn.setToolTip(
            "向已支持的运动设备发送软件快停；不能替代物理急停"
        )
        self.quick_stop_btn.setAccessibleName("快速停止")
        set_theme_role(self.quick_stop_btn, "warning")
        self.emergency_stop_btn.setToolTip(
            "向已支持的运动设备发送软件急停；不能替代物理急停回路"
        )
        self.emergency_stop_btn.setAccessibleName("设备急停")
        set_theme_role(self.emergency_stop_btn, "dangerStrong")
        for button in (
            self.start_btn,
            self.pause_btn,
            self.stop_btn,
            self.quick_stop_btn,
            self.emergency_stop_btn,
        ):
            self.execution_command_row.addWidget(button)
        self.execution_command_row.addStretch(1)

    @staticmethod
    def _execution_button(
        icon: IconName,
        tooltip: str,
        callback: Callable[[], None],
    ) -> IconToolButton:
        return IconToolButton(
            icon,
            tooltip,
            callback=callback,
            hit_size=EXECUTION_COMMAND_HIT_SIZE,
            icon_size=EXECUTION_COMMAND_ICON_SIZE,
        )

    @staticmethod
    def _button(
        icon: IconName,
        tooltip: str,
        callback: Callable[[], None],
        *,
        hit_size: int = 32,
    ) -> IconToolButton:
        return IconToolButton(
            icon,
            tooltip,
            callback=callback,
            hit_size=hit_size,
            icon_size=20 if hit_size >= 44 else TOOL_BUTTON_ICON_SIZE,
        )

    def set_undo_redo_enabled(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_btn.setEnabled(can_undo)
        self.redo_btn.setEnabled(can_redo)

    def render_execution_state(
        self,
        toggle_text: str,
        can_toggle: bool,
        can_cancel: bool,
    ) -> None:
        normalized = toggle_text.strip() or "暂停执行"
        is_resume = "恢复" in normalized or "继续" in normalized
        self.pause_btn.set_icon_name(
            IconName.PLAY if is_resume else IconName.PAUSE
        )
        self.pause_btn.setToolTip(normalized)
        self.pause_btn.setAccessibleName(normalized)
        self.pause_btn.setEnabled(can_toggle)
        self.stop_btn.setEnabled(can_cancel)

"""Workflow editing and execution controls."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..theme import set_theme_role


class ControlPanel(QWidget):
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
    clear_clicked = Signal()
    save_clicked = Signal()
    load_clicked = Signal()
    undo_clicked = Signal()
    redo_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        history_row = QHBoxLayout()
        history_row.setSpacing(4)
        self.undo_btn = self._button("↶ 撤销", self.undo_clicked.emit)
        self.redo_btn = self._button("↷ 重做", self.redo_clicked.emit)
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        for button in (
            self.undo_btn,
            self.redo_btn,
            self._button("↑ 上移", self.move_up_clicked.emit),
            self._button("↓ 下移", self.move_down_clicked.emit),
        ):
            history_row.addWidget(button)
        layout.addLayout(history_row)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(4)
        for label, callback in (
            ("✏ 修改", self.edit_clicked.emit),
            ("🔁 循环", self.repeat_clicked.emit),
            ("🗑 删除", self.delete_clicked.emit),
            ("✕ 清空", self.clear_clicked.emit),
        ):
            edit_row.addWidget(self._button(label, callback))
        layout.addLayout(edit_row)

        save_row = QHBoxLayout()
        save_row.setSpacing(4)
        self.save_btn = self._button("💾 保存序列", self.save_clicked.emit)
        self.load_btn = self._button("📂 载入序列", self.load_clicked.emit)
        set_theme_role(self.save_btn, "primary")
        set_theme_role(self.load_btn, "primary")
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.load_btn)
        layout.addLayout(save_row)

        execution_row = QHBoxLayout()
        execution_row.setSpacing(4)
        self.start_btn = self._button("▶ 开始执行", self.start_clicked.emit)
        self.pause_btn = self._button("⏸ 暂停", self.pause_clicked.emit)
        set_theme_role(self.start_btn, "success")
        set_theme_role(self.pause_btn, "warning")
        execution_row.addWidget(self.start_btn)
        execution_row.addWidget(self.pause_btn)
        layout.addLayout(execution_row)

        self.stop_btn = self._button("⏹ 停止任务", self.stop_clicked.emit)
        self.stop_btn.setAccessibleName("停止任务")
        self.stop_btn.setToolTip(
            "请求当前任务在可中断点停止；不会触发设备硬件急停"
        )
        set_theme_role(self.stop_btn, "danger")
        layout.addWidget(self.stop_btn)

        safety_row = QHBoxLayout()
        safety_row.setSpacing(4)
        self.quick_stop_btn = self._button(
            "⚡ 快速停止",
            self.quick_stop_clicked.emit,
        )
        self.quick_stop_btn.setToolTip(
            "向已支持的运动设备发送软件快停；不能替代物理急停"
        )
        set_theme_role(self.quick_stop_btn, "warning")
        self.emergency_stop_btn = self._button(
            "🛑 设备急停",
            self.emergency_stop_clicked.emit,
        )
        self.emergency_stop_btn.setToolTip(
            "向已支持的运动设备发送软件急停；不能替代物理急停回路"
        )
        set_theme_role(self.emergency_stop_btn, "dangerStrong")
        safety_row.addWidget(self.quick_stop_btn)
        safety_row.addWidget(self.emergency_stop_btn)
        layout.addLayout(safety_row)

    @staticmethod
    def _button(label: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
        button.setAccessibleName(label.lstrip("↶↷↑↓✏🔁🗑✕💾📂▶⏸⏹⚡🛑 "))
        button.setMinimumHeight(44)
        button.clicked.connect(callback)
        return button

    def set_undo_redo_enabled(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_btn.setEnabled(can_undo)
        self.redo_btn.setEnabled(can_redo)

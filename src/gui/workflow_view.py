from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from PyQt6.QtCore import QMimeData, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application import ApplicationServices
from ..core.models import ActionDefinition, ActionType
from ..device_runtime import StopMode
from ..widgets import ActionListWidget, ControlPanel, SequenceListWidget
from ..widgets.ai_assistant import AIAssistantWidget


class TaskLibraryListWidget(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(28, 28))
        self.setSpacing(2)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        del supported_actions
        current_item = self.currentItem()
        if current_item is None:
            return
        task_name = current_item.data(Qt.ItemDataRole.UserRole)
        if not task_name:
            return

        mime = QMimeData()
        mime.setData("application/x-task-name", task_name.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(current_item.icon().pixmap(50, 50))
        drag.exec(Qt.DropAction.CopyAction)


class TaskComposerListWidget(QListWidget):
    order_changed = pyqtSignal(int, int)
    task_dropped = pyqtSignal(str, int)
    action_dropped = pyqtSignal(object, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setSpacing(12)
        self.setIconSize(QSize(130, 88))
        self.setStyleSheet("""
            QListWidget { background-color: #f8fafc; border: 2px dashed #cbd5e1;
                border-radius: 12px; padding: 4px; }
            QListWidget::item { border: 2px solid transparent; border-radius: 10px;
                padding: 1px; font-size: 11px; font-weight: bold; background: transparent; }
            QListWidget::item:hover { border-color: #93c5fd;
                background: rgba(59, 130, 246, 0.06); }
            QListWidget::item:selected { border: 2px solid #3b82f6;
                background: rgba(59, 130, 246, 0.10); }
        """)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        del supported_actions
        current_item = self.currentItem()
        if current_item is None or not current_item.data(Qt.ItemDataRole.UserRole):
            return
        mime = QMimeData()
        mime.setData(
            "application/x-task-composer-item",
            json.dumps({"row": self.currentRow()}).encode("utf-8"),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(current_item.icon().pixmap(80, 54))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802
        self._accept_supported_drop(event)

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:  # noqa: N802
        self._accept_supported_drop(event)

    @staticmethod
    def _accept_supported_drop(event: QDragEnterEvent | QDragMoveEvent | None) -> None:
        if event is None:
            return
        formats = (
            "application/x-task-name",
            "application/x-action",
            "application/x-task-composer-item",
        )
        mime = event.mimeData()
        if mime is not None and any(mime.hasFormat(value) for value in formats):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        mime = event.mimeData()
        if mime is None:
            event.ignore()
            return
        insert_row = self._drop_row(event)
        if mime.hasFormat("application/x-task-composer-item"):
            payload = json.loads(bytes(mime.data("application/x-task-composer-item")))
            source_row = payload["row"]
            if 0 <= source_row < self.count():
                if source_row < insert_row:
                    insert_row -= 1
                self.order_changed.emit(source_row, min(insert_row, self.count() - 1))
                event.accept()
            return
        if mime.hasFormat("application/x-task-name"):
            task_name = bytes(mime.data("application/x-task-name")).decode("utf-8")
            self.task_dropped.emit(task_name, insert_row)
            event.accept()
            return
        if mime.hasFormat("application/x-action"):
            payload = bytes(mime.data("application/x-action")).decode("utf-8")
            self.action_dropped.emit(ActionDefinition.from_dict(json.loads(payload)), insert_row)
            event.accept()
            return
        event.ignore()

    def _drop_row(self, event: QDropEvent) -> int:
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(position)
        return self.count() if item is None else self.row(item)


class ActionLibraryView(QWidget):
    create_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    camera_test_requested = pyqtSignal()
    task_add_requested = pyqtSignal()

    def __init__(self, services: ApplicationServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.action_tabs = QTabWidget()
        self.action_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.action_tabs.setMovable(False)
        self.action_lists = {
            ActionType.MOVE: ActionListWidget(),
            ActionType.MANIPULATE: ActionListWidget(),
            ActionType.INSPECT: ActionListWidget(),
            ActionType.CHANGE_GUN: ActionListWidget(),
            ActionType.VISION_CAPTURE: ActionListWidget(),
            ActionType.TRAJECTORY: ActionListWidget(),
        }
        for action_type, title in (
            (ActionType.MOVE, "移动类"),
            (ActionType.MANIPULATE, "执行类"),
            (ActionType.INSPECT, "检测类"),
            (ActionType.CHANGE_GUN, "换枪类"),
            (ActionType.VISION_CAPTURE, "视觉类"),
        ):
            self.action_tabs.addTab(self.action_lists[action_type], title)
        self.ai_assistant = AIAssistantWidget(services)
        self.action_tabs.addTab(self.ai_assistant, "🤖 AI助手")
        self.action_tabs.addTab(self.action_lists[ActionType.TRAJECTORY], "轨迹类")
        layout.addWidget(self.action_tabs, stretch=2)

        task_group = QGroupBox("已保存任务")
        task_layout = QVBoxLayout(task_group)
        task_layout.setContentsMargins(6, 6, 6, 6)
        task_layout.setSpacing(4)
        self.task_library_list = TaskLibraryListWidget()
        self.task_library_list.setMinimumHeight(140)
        self.task_library_list.itemDoubleClicked.connect(lambda _: self.task_add_requested.emit())
        task_layout.addWidget(self.task_library_list)
        layout.addWidget(task_group, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        for text, signal in (
            ("新建动作", self.create_requested),
            ("修改动作", self.edit_requested),
            ("删除动作", self.delete_requested),
        ):
            button = QPushButton(text)
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, target=signal: target.emit())
            buttons.addWidget(button)
        self.camera_test_button = QPushButton("📷 测试相机")
        self.camera_test_button.setMinimumHeight(32)
        self.camera_test_button.setStyleSheet(
            "QPushButton { background: #10b981; color: #fff; font-weight: 700; border: none; "
            "border-radius: 6px; padding: 6px 12px; } QPushButton:hover { background: #059669; }"
        )
        self.camera_test_button.clicked.connect(lambda: self.camera_test_requested.emit())
        buttons.addWidget(self.camera_test_button)
        layout.addLayout(buttons)

    def action_list(self, action_type: ActionType) -> ActionListWidget:
        return self.action_lists[action_type]

    def set_camera_test_running(self, running: bool) -> None:
        self.camera_test_button.setEnabled(not running)
        self.camera_test_button.setText("测试中..." if running else "测试相机")


class WorkflowEditorView(QWidget):
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    safety_stop_requested = pyqtSignal(object)
    move_up_requested = pyqtSignal()
    move_down_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    repeat_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    save_requested = pyqtSignal()
    load_requested = pyqtSignal()
    composer_remove_requested = pyqtSignal()
    composer_move_up_requested = pyqtSignal()
    composer_move_down_requested = pyqtSignal()
    composer_repeat_requested = pyqtSignal()
    composer_clear_requested = pyqtSignal()
    composer_refresh_requested = pyqtSignal()
    composer_add_requested = pyqtSignal()
    composer_execute_requested = pyqtSignal()
    composer_save_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)
        tabs.setMovable(False)

        action_page = QWidget()
        action_layout = QVBoxLayout(action_page)
        action_layout.setContentsMargins(2, 2, 2, 2)
        action_layout.setSpacing(2)
        self.sequence_list = SequenceListWidget()
        self.sequence_list.setMinimumHeight(140)
        action_layout.addWidget(self.sequence_list, stretch=2)
        self.control_panel = ControlPanel()
        self._connect_control_panel()
        action_layout.addWidget(self.control_panel)

        tabs.addTab(action_page, "动作编排")
        tabs.addTab(self._create_composer(), "任务组合")
        layout.addWidget(tabs, stretch=1)

    def _connect_control_panel(self) -> None:
        controls = self.control_panel
        for source, target in (
            (controls.start_clicked, self.start_requested),
            (controls.pause_clicked, self.pause_requested),
            (controls.stop_clicked, self.stop_requested),
            (controls.move_up_clicked, self.move_up_requested),
            (controls.move_down_clicked, self.move_down_requested),
            (controls.edit_clicked, self.edit_requested),
            (controls.repeat_clicked, self.repeat_requested),
            (controls.delete_clicked, self.delete_requested),
            (controls.clear_clicked, self.clear_requested),
            (controls.save_clicked, self.save_requested),
            (controls.load_clicked, self.load_requested),
        ):
            source.connect(target.emit)
        controls.quick_stop_clicked.connect(lambda: self.safety_stop_requested.emit(StopMode.QUICK))
        controls.emergency_stop_clicked.connect(
            lambda: self.safety_stop_requested.emit(StopMode.EMERGENCY)
        )

    def _create_composer(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(2)
        panel = QGroupBox("任务组合器")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        title = QLabel("组合计划")
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155;")
        self.task_composer_list = TaskComposerListWidget()
        self.task_composer_list.setMinimumHeight(140)
        layout.addWidget(title)
        layout.addWidget(self.task_composer_list, stretch=1)

        self._add_button_row(
            layout,
            (
                ("↑ 上移", self.composer_move_up_requested),
                ("↓ 下移", self.composer_move_down_requested),
                ("🔁 循环", self.composer_repeat_requested),
                ("🗑 移除", self.composer_remove_requested),
                ("✕ 清空", self.composer_clear_requested),
            ),
            28,
        )
        self._add_button_row(
            layout,
            (
                ("🔄 刷新", self.composer_refresh_requested),
                ("＋ 添加", self.composer_add_requested),
            ),
            28,
        )
        execute = self._add_button_row(
            layout,
            (
                ("▶ 执行当前组合", self.composer_execute_requested),
                ("⏸ 暂停", self.pause_requested),
            ),
            32,
        )
        execute[0].setStyleSheet(self._colored_button("#22c55e", "#16a34a", 14))
        self.composer_pause_button = execute[1]
        self.composer_pause_button.setStyleSheet(self._colored_button("#f59e0b", "#d97706", 14))
        stop_save = self._add_button_row(
            layout,
            (
                ("⏹ 停止任务", self.stop_requested),
                ("💾 保存组合", self.composer_save_requested),
            ),
            32,
        )
        self.composer_stop_button = stop_save[0]
        self.composer_stop_button.setAccessibleName("停止任务")
        self.composer_stop_button.setToolTip("请求当前任务在可中断点停止；不会触发设备硬件急停")
        self.composer_stop_button.setStyleSheet(self._colored_button("#ef4444", "#dc2626", 14))
        stop_save[1].setStyleSheet(self._colored_button("#3b82f6", "#2563eb"))
        page_layout.addWidget(panel, stretch=1)
        return page

    @staticmethod
    def _add_button_row(
        layout: QVBoxLayout,
        specs: Iterable[tuple[str, Any]],
        height: int,
    ) -> list[QPushButton]:
        row = QHBoxLayout()
        row.setSpacing(4)
        buttons = []
        for text, signal in specs:
            button = QPushButton(text)
            button.setMinimumHeight(height)
            button.clicked.connect(lambda _checked=False, target=signal: target.emit())
            row.addWidget(button)
            buttons.append(button)
        layout.addLayout(row)
        return buttons

    @staticmethod
    def _colored_button(color: str, hover: str, font_size: int | None = None) -> str:
        size = f" font-size: {font_size}px;" if font_size else ""
        return (
            f"QPushButton {{ background: {color}; color: #fff; font-weight: 700; border: none; "
            f"border-radius: 6px;{size} }} QPushButton:hover {{ background: {hover}; }}"
        )

    def render_execution_controls(self, text: str, can_toggle: bool, can_cancel: bool) -> None:
        self.control_panel.pause_btn.setText(text)
        self.control_panel.pause_btn.setEnabled(can_toggle)
        self.control_panel.stop_btn.setEnabled(can_cancel)
        self.composer_pause_button.setText(text)
        self.composer_pause_button.setEnabled(can_toggle)
        self.composer_stop_button.setEnabled(can_cancel)

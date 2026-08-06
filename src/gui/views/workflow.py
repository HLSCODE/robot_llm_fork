from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import (
    ActionDefinition,
    ActionType,
)
from ...devices import StopMode
from .action_list import ActionListWidget
from .control_panel import ControlPanel
from .workflow_canvas import WorkflowCanvasWidget
from ..theme import set_theme_role


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
    order_changed = Signal(int, int)
    task_dropped = Signal(str, int)
    action_dropped = Signal(object, int)

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
    create_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()
    camera_test_requested = Signal()
    action_insert_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
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
        for action_list in self.action_lists.values():
            action_list.action_selected.connect(
                self.action_insert_requested.emit
            )
        for action_type, title in (
            (ActionType.MOVE, "移动类"),
            (ActionType.MANIPULATE, "执行类"),
            (ActionType.INSPECT, "检测类"),
            (ActionType.CHANGE_GUN, "换枪类"),
            (ActionType.VISION_CAPTURE, "视觉类"),
        ):
            self.action_tabs.addTab(self.action_lists[action_type], title)
        self.action_tabs.addTab(self.action_lists[ActionType.TRAJECTORY], "轨迹类")
        layout.addWidget(self.action_tabs, stretch=1)

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
        layout.addLayout(buttons)

        self.camera_test_button = QPushButton("测试相机")
        self.camera_test_button.setMinimumHeight(32)
        set_theme_role(self.camera_test_button, "success")
        self.camera_test_button.clicked.connect(lambda: self.camera_test_requested.emit())
        layout.addWidget(self.camera_test_button)

    def action_list(self, action_type: ActionType) -> ActionListWidget:
        return self.action_lists[action_type]

    def set_camera_test_running(self, running: bool) -> None:
        self.camera_test_button.setEnabled(not running)
        self.camera_test_button.setText("测试中..." if running else "测试相机")


class TaskLibraryView(QWidget):
    task_add_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        title = QLabel("已保存任务")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        self.task_library_list = TaskLibraryListWidget()
        self.task_library_list.setMinimumHeight(140)
        self.task_library_list.itemDoubleClicked.connect(lambda _: self.task_add_requested.emit())
        layout.addWidget(self.task_library_list, stretch=1)
        add_button = QPushButton("添加到任务组合")
        add_button.setMinimumHeight(32)
        add_button.clicked.connect(lambda: self.task_add_requested.emit())
        set_theme_role(add_button, "primary")
        layout.addWidget(add_button)


class TaskComposerView(QWidget):
    remove_requested = Signal()
    move_up_requested = Signal()
    move_down_requested = Signal()
    repeat_requested = Signal()
    clear_requested = Signal()
    refresh_requested = Signal()
    add_task_requested = Signal()
    add_action_requested = Signal()
    execute_requested = Signal()
    save_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        title = QLabel("任务组合")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        self.task_composer_list = TaskComposerListWidget()
        self.task_composer_list.setMinimumHeight(140)
        layout.addWidget(title)
        layout.addWidget(self.task_composer_list, stretch=1)

        self._add_button_row(
            layout,
            (
                ("↑ 上移", self.move_up_requested),
                ("↓ 下移", self.move_down_requested),
                ("循环", self.repeat_requested),
            ),
            28,
        )
        self._add_button_row(
            layout,
            (
                ("移除", self.remove_requested),
                ("清空", self.clear_requested),
                ("刷新任务", self.refresh_requested),
            ),
            28,
        )
        self._add_button_row(
            layout,
            (
                ("添加任务", self.add_task_requested),
                ("添加动作", self.add_action_requested),
            ),
            30,
        )
        actions = self._add_button_row(
            layout,
            (
                ("执行当前组合", self.execute_requested),
                ("保存组合", self.save_requested),
            ),
            32,
        )
        set_theme_role(actions[0], "success")
        set_theme_role(actions[1], "primary")

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


class WorkflowEditorView(QWidget):
    start_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    safety_stop_requested = Signal(object)
    move_up_requested = Signal()
    move_down_requested = Signal()
    edit_requested = Signal()
    repeat_requested = Signal()
    delete_requested = Signal()
    insert_action_at_requested = Signal(int)
    insert_action_in_loop_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.sequence_list = WorkflowCanvasWidget()
        self.sequence_list.setMinimumHeight(140)
        layout.addWidget(self.sequence_list, stretch=1)
        self.sequence_list.edit_requested.connect(self.edit_requested)
        self.sequence_list.insert_action_requested.connect(
            self.insert_action_at_requested.emit
        )
        self.sequence_list.insert_loop_action_requested.connect(
            self.insert_action_in_loop_requested.emit
        )
        self.sequence_list.wrap_selection_requested.connect(
            self.repeat_requested.emit
        )
        self.control_panel = ControlPanel()
        self._connect_control_panel()
        layout.addWidget(self.control_panel)

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
        ):
            source.connect(target.emit)
        controls.quick_stop_clicked.connect(lambda: self.safety_stop_requested.emit(StopMode.QUICK))
        controls.emergency_stop_clicked.connect(
            lambda: self.safety_stop_requested.emit(StopMode.EMERGENCY)
        )
        controls.undo_clicked.connect(self.sequence_list.undo)
        controls.redo_clicked.connect(self.sequence_list.redo)
        self.sequence_list.can_undo_changed.connect(
            lambda enabled: controls.set_undo_redo_enabled(
                enabled,
                controls.redo_btn.isEnabled(),
            )
        )
        self.sequence_list.can_redo_changed.connect(
            lambda enabled: controls.set_undo_redo_enabled(
                controls.undo_btn.isEnabled(),
                enabled,
            )
        )

    def render_execution_controls(self, text: str, can_toggle: bool, can_cancel: bool) -> None:
        self.control_panel.pause_btn.setText(text)
        self.control_panel.pause_btn.setEnabled(can_toggle)
        self.control_panel.stop_btn.setEnabled(can_cancel)

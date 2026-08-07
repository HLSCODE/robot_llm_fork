from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QListWidget,
    QMenu,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import ActionType
from ...devices import StopMode
from .action_list import ActionListWidget
from .control_panel import ControlPanel
from .workflow_canvas import WorkflowCanvasWidget
from ..icons import IconName
from ..toolbars import PaneHeader


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


class ActionLibraryView(QWidget):
    create_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()
    camera_test_requested = Signal()
    action_insert_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header = PaneHeader("基础动作")
        self.create_button = header.add_action(
            IconName.ADD,
            "新建动作",
            self.create_requested.emit,
        )
        self.edit_button = header.add_action(
            IconName.EDIT,
            "修改选中动作",
            self.edit_requested.emit,
        )
        self.delete_button = header.add_action(
            IconName.DELETE,
            "删除选中动作",
            self.delete_requested.emit,
        )
        self.camera_test_button = header.add_action(
            IconName.CAMERA,
            "测试相机",
            self.camera_test_requested.emit,
        )
        layout.addWidget(header)

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

    def action_list(self, action_type: ActionType) -> ActionListWidget:
        return self.action_lists[action_type]

    def set_camera_test_running(self, running: bool) -> None:
        self.camera_test_button.setEnabled(not running)
        label = "相机测试运行中" if running else "测试相机"
        self.camera_test_button.setToolTip(label)
        self.camera_test_button.setAccessibleName(label)


class TaskLibraryView(QWidget):
    task_open_requested = Signal()
    task_insert_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = PaneHeader("已保存任务")
        self.open_button = header.add_action(
            IconName.OPEN,
            "打开选中任务",
            self.task_open_requested.emit,
        )
        self.insert_button = header.add_action(
            IconName.INSERT,
            "插入到当前任务",
            self.task_insert_requested.emit,
        )
        layout.addWidget(header)
        self.task_library_list = TaskLibraryListWidget()
        self.task_library_list.setMinimumHeight(140)
        self.task_library_list.itemDoubleClicked.connect(
            lambda _: self.task_open_requested.emit()
        )
        self.task_library_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.task_library_list.customContextMenuRequested.connect(
            self._show_context_menu
        )
        layout.addWidget(self.task_library_list, stretch=1)

    def _show_context_menu(self, position: QPoint) -> None:
        if self.task_library_list.itemAt(position) is None:
            return
        menu = QMenu(self)
        menu.addAction("打开", self.task_open_requested.emit)
        menu.addAction("插入到当前任务", self.task_insert_requested.emit)
        menu.exec(self.task_library_list.viewport().mapToGlobal(position))


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
    insert_action_in_parallel_requested = Signal(str, str, int)
    add_parallel_branch_requested = Signal(str)
    insert_subworkflow_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.control_panel = ControlPanel()
        self.sequence_list = WorkflowCanvasWidget()
        self.sequence_list.setMinimumHeight(140)
        self._connect_control_panel()
        layout.addWidget(self.control_panel)
        layout.addWidget(self.sequence_list, stretch=1)
        self.sequence_list.edit_requested.connect(self.edit_requested)
        self.sequence_list.insert_action_requested.connect(
            self.insert_action_at_requested.emit
        )
        self.sequence_list.insert_loop_action_requested.connect(
            self.insert_action_in_loop_requested.emit
        )
        self.sequence_list.insert_parallel_action_requested.connect(
            self.insert_action_in_parallel_requested.emit
        )
        self.sequence_list.add_parallel_branch_requested.connect(
            self.add_parallel_branch_requested.emit
        )
        self.sequence_list.insert_subworkflow_requested.connect(
            self.insert_subworkflow_requested.emit
        )
        self.sequence_list.wrap_selection_requested.connect(
            self.repeat_requested.emit
        )

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
        self.control_panel.render_execution_state(text, can_toggle, can_cancel)

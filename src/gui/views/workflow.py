from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import ActionDefinition, ActionType
from ...devices import StopMode
from .action_list import ActionListWidget
from .control_panel import ControlPanel
from .workflow_canvas import WorkflowCanvasWidget
from ..icons import IconName, themed_icon
from ..drag_preview import create_drag_card_preview
from ..toolbars import ElidingComboBox, PaneHeader


ACTION_LIBRARY_CATEGORIES: tuple[tuple[ActionType, str], ...] = (
    (ActionType.MOVE, "移动类"),
    (ActionType.MANIPULATE, "执行类"),
    (ActionType.INSPECT, "检测类"),
    (ActionType.CHANGE_GUN, "换枪类"),
    (ActionType.VISION_CAPTURE, "视觉类"),
    (ActionType.TRAJECTORY, "轨迹类"),
)


class TaskLibraryListWidget(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(28, 28))
        self.setSpacing(2)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._canvas_scale_provider: Callable[[], float] = lambda: 1.0

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        self._canvas_scale_provider = provider

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() not in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            return
        color = QApplication.palette().highlight().color()
        for index in range(self.count()):
            self.item(index).setIcon(
                themed_icon(self, IconName.WORKFLOW, size=20, color=color)
            )

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
        accent = QApplication.palette().highlight().color()
        preview = create_drag_card_preview(
            self,
            title=current_item.text(),
            subtitle="已保存任务 · 拖入画布",
            icon=themed_icon(self, IconName.WORKFLOW, size=20, color=accent),
            accent=accent,
            canvas_scale=self._canvas_scale_provider(),
        )
        drag.setPixmap(preview.pixmap)
        drag.setHotSpot(preview.hotspot)
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

        self.category_selector = ElidingComboBox()
        self.category_selector.setObjectName("paneHeaderSelector")
        self.category_selector.setAccessibleName("基础动作分类")
        self.category_selector.setToolTip("选择基础动作分类")
        self.category_selector.setMinimumContentsLength(8)

        self.header = PaneHeader("")
        self.header.replace_title_with(self.category_selector)
        self.create_button = self.header.add_action(
            IconName.ADD,
            "新建动作",
            self.create_requested.emit,
        )
        self.edit_button = self.header.add_action(
            IconName.EDIT,
            "修改选中动作",
            self.edit_requested.emit,
        )
        self.delete_button = self.header.add_action(
            IconName.DELETE,
            "删除选中动作",
            self.delete_requested.emit,
        )
        self.camera_test_button = self.header.add_action(
            IconName.CAMERA,
            "重新检测相机",
            self.camera_test_requested.emit,
        )
        layout.addWidget(self.header)

        self.action_stack = QStackedWidget()
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
            action_list.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            action_list.customContextMenuRequested.connect(
                lambda position, target=action_list: self._show_action_context_menu(
                    target,
                    position,
                )
            )
        for action_type, title in ACTION_LIBRARY_CATEGORIES:
            self.category_selector.addItem(title, action_type)
            self.action_stack.addWidget(self.action_lists[action_type])
        self.category_selector.currentIndexChanged.connect(
            self.action_stack.setCurrentIndex
        )
        layout.addWidget(self.action_stack, stretch=1)

    def action_list(self, action_type: ActionType) -> ActionListWidget:
        return self.action_lists[action_type]

    def current_category_type(self) -> ActionType:
        action_type = self.category_selector.currentData()
        if not isinstance(action_type, ActionType):
            raise RuntimeError("基础动作分类未初始化")
        return action_type

    def current_action_list(self) -> ActionListWidget:
        return self.action_lists[self.current_category_type()]

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        for action_list in self.action_lists.values():
            action_list.set_canvas_scale_provider(provider)

    def set_camera_test_running(self, running: bool) -> None:
        self.camera_test_button.setEnabled(not running)
        label = "正在重新检测相机" if running else "重新检测相机"
        self.camera_test_button.setToolTip(label)
        self.camera_test_button.setAccessibleName(label)

    def _show_action_context_menu(
        self,
        action_list: ActionListWidget,
        position: QPoint,
    ) -> None:
        item = action_list.itemAt(position)
        if item is None:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(action, ActionDefinition):
            return
        action_list.setCurrentItem(item)
        menu = self._create_action_context_menu(action_list, action)
        menu.exec(action_list.viewport().mapToGlobal(position))

    def _create_action_context_menu(
        self,
        action_list: ActionListWidget,
        action: ActionDefinition,
    ) -> QMenu:
        menu = QMenu(self)
        insert = menu.addAction(
            themed_icon(action_list, IconName.INSERT, size=16),
            "插入到画布",
        )
        insert.triggered.connect(
            lambda _checked=False: self.action_insert_requested.emit(action)
        )
        edit = menu.addAction(
            themed_icon(action_list, IconName.EDIT, size=16),
            "修改动作",
        )
        edit.triggered.connect(lambda _checked=False: self.edit_requested.emit())
        menu.addSeparator()
        delete = menu.addAction(
            themed_icon(action_list, IconName.DELETE, size=16),
            "删除动作",
        )
        delete.triggered.connect(
            lambda _checked=False: self.delete_requested.emit()
        )
        return menu


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

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        self.task_library_list.set_canvas_scale_provider(provider)

    def _show_context_menu(self, position: QPoint) -> None:
        if self.task_library_list.itemAt(position) is None:
            return
        menu = QMenu(self)
        menu.addAction("打开", self.task_open_requested.emit)
        menu.addAction("插入到当前任务", self.task_insert_requested.emit)
        menu.exec(self.task_library_list.viewport().mapToGlobal(position))


class WorkflowEditorView(QWidget):
    save_requested = Signal()
    clear_requested = Signal()
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
    insert_subworkflow_in_loop_requested = Signal(str, str, int)

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
        self.sequence_list.insert_subworkflow_in_loop_requested.connect(
            self.insert_subworkflow_in_loop_requested.emit
        )
        self.sequence_list.wrap_selection_requested.connect(
            self.repeat_requested.emit
        )

    def _connect_control_panel(self) -> None:
        controls = self.control_panel
        for source, target in (
            (controls.save_clicked, self.save_requested),
            (controls.clear_clicked, self.clear_requested),
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
        controls.fit_clicked.connect(self.sequence_list.fit_workflow)
        controls.reset_zoom_clicked.connect(self.sequence_list.reset_zoom)
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

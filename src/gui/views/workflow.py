from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QDrag
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import ActionDefinition, ActionType
from ...devices import StopMode
from .action_tree import ACTION_LIBRARY_CATEGORIES, ActionCategoryTree
from .control_panel import ControlPanel
from .workflow_canvas import WorkflowCanvasWidget
from ..icons import IconName, themed_icon
from ..drag_preview import create_drag_card_preview
from ..toolbars import PaneHeader


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
        self._pending_reveal_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.header = PaneHeader("基础动作")
        self.create_button = self.header.add_action(
            IconName.ADD, "新建动作", self._request_create,
        )
        self.edit_button = self.header.add_action(
            IconName.EDIT, "修改选中动作", self.edit_requested.emit,
        )
        self.delete_button = self.header.add_action(
            IconName.DELETE, "删除选中动作", self.delete_requested.emit,
        )
        self.camera_test_button = self.header.add_action(
            IconName.CAMERA, "重新检测相机", self.camera_test_requested.emit,
        )
        self.collapse_button = self.header.add_action(
            IconName.COLLAPSE_ALL, "全部折叠", self._collapse_all,
        )
        layout.addWidget(self.header)
        self.action_tree = ActionCategoryTree()
        self.action_tree.action_selected.connect(self.action_insert_requested.emit)
        self.action_tree.create_requested.connect(self.create_requested.emit)
        self.action_tree.itemSelectionChanged.connect(self._update_commands)
        self.action_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.action_tree.customContextMenuRequested.connect(self._show_action_context_menu)
        self.category_menu = QMenu(self)
        for action_type, title in ACTION_LIBRARY_CATEGORIES:
            command = QAction(title, self.category_menu)
            self.category_menu.addAction(command)
            command.triggered.connect(
                lambda _checked=False, category=action_type: self._create_in_category(category)
            )
        layout.addWidget(self.action_tree, stretch=1)
        self._update_commands()

    def _request_create(self) -> None:
        if self.current_category_type() is None:
            self.category_menu.popup(
                self.create_button.mapToGlobal(QPoint(0, self.create_button.height()))
            )
            return
        self.create_requested.emit()

    def _create_in_category(self, category: ActionType) -> None:
        self.action_tree.select_category(category)
        self.create_requested.emit()

    def current_category_type(self) -> ActionType | None:
        return self.action_tree.current_category()

    def selected_action(self) -> ActionDefinition | None:
        return self.action_tree.selected_action()

    def render_actions(self, actions: Sequence[ActionDefinition]) -> None:
        self.action_tree.render_actions(actions)
        if self._pending_reveal_id is not None:
            self.reveal_action(self._pending_reveal_id)

    def reveal_action(self, action_id: str) -> None:
        self._pending_reveal_id = (
            None if self.action_tree.reveal_action(action_id) else action_id
        )

    def _collapse_all(self) -> None:
        self.action_tree.collapseAll()

    def _update_commands(self) -> None:
        selected = self.selected_action() is not None
        self.edit_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        category = self.current_category_type()
        title = dict(ACTION_LIBRARY_CATEGORIES).get(category) if category is not None else None
        label = f"新建{title}动作" if title else "新建动作（选择分类）"
        self.create_button.setToolTip(label)
        self.create_button.setAccessibleName(label)

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        self.action_tree.set_canvas_scale_provider(provider)

    def set_camera_test_running(self, running: bool) -> None:
        self.camera_test_button.setEnabled(not running)
        label = "正在重新检测相机" if running else "重新检测相机"
        self.camera_test_button.setToolTip(label)
        self.camera_test_button.setAccessibleName(label)

    def _show_action_context_menu(self, position: QPoint) -> None:
        item = self.action_tree.itemAt(position)
        if item is None:
            return
        self.action_tree.setCurrentItem(item)
        action = self.selected_action()
        if action is None:
            return
        menu = self._create_action_context_menu(action)
        menu.exec(self.action_tree.viewport().mapToGlobal(position))

    def _create_action_context_menu(self, action: ActionDefinition) -> QMenu:
        menu = QMenu(self)
        insert = menu.addAction(
            themed_icon(self, IconName.INSERT, size=16), "插入到画布",
        )
        insert.triggered.connect(
            lambda _checked=False: self.action_insert_requested.emit(action)
        )
        edit = menu.addAction(themed_icon(self, IconName.EDIT, size=16), "修改动作")
        edit.triggered.connect(lambda _checked=False: self.edit_requested.emit())
        menu.addSeparator()
        delete = menu.addAction(themed_icon(self, IconName.DELETE, size=16), "删除动作")
        delete.triggered.connect(lambda _checked=False: self.delete_requested.emit())
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

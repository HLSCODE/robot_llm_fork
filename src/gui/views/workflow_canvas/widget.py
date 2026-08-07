"""Constrained workflow canvas with snapshot-based undo commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import uuid4

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainterPath, QPen, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsScene,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....application import CompiledWorkflow
from ....domain.models import (
    ActionDefinition,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceEntry,
    SequenceItem,
    SubworkflowBlock,
)
from ....domain.workflow import (
    CanvasPosition,
    WorkflowDocument,
    clone_sequence_entry,
)
from .items import InsertionItem, StartEndItem, WorkflowNodeItem
from .tokens import (
    CANVAS_MARGIN,
    NODE_GAP,
    NODE_WIDTH,
    LOOP_NODE_WIDTH,
    PARALLEL_BRANCH_GAP,
    PARALLEL_BRANCH_PADDING,
    PARALLEL_BRANCH_WIDTH,
    TOOLBAR_SPACING,
    TOUCH_TARGET_SIZE,
    canvas_colors,
)
from .view import WorkflowCanvasView


class _ReplaceEntriesCommand(QUndoCommand):
    def __init__(
        self,
        canvas: WorkflowCanvasWidget,
        before: tuple[SequenceEntry, ...],
        after: tuple[SequenceEntry, ...],
        text: str,
        selected_node_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(text)
        self._canvas = canvas
        self._before = before
        self._after = after
        self._selected_node_ids = selected_node_ids

    def redo(self) -> None:
        self._canvas._apply_entries(  # noqa: SLF001
            self._after,
            selected_node_ids=self._selected_node_ids,
            publish=True,
        )

    def undo(self) -> None:
        self._canvas._apply_entries(self._before, publish=True)  # noqa: SLF001


class WorkflowCanvasWidget(QWidget):
    sequence_changed = Signal()
    edit_requested = Signal()
    insert_action_requested = Signal(int)
    insert_loop_action_requested = Signal(str, int)
    insert_parallel_action_requested = Signal(str, str, int)
    add_parallel_branch_requested = Signal(str)
    insert_subworkflow_requested = Signal(str, int)
    wrap_selection_requested = Signal()
    can_undo_changed = Signal(bool)
    can_redo_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root_entries: tuple[SequenceEntry, ...] = ()
        self._entries: tuple[SequenceEntry, ...] = ()
        self._scope_path: tuple[str, ...] = ()
        self._node_items: dict[str, WorkflowNodeItem] = {}
        self._endpoint_items: tuple[StartEndItem, ...] = ()
        self._current_item_uuid = ""
        self._compiled: CompiledWorkflow | None = None
        self._parallel_branch_states: dict[tuple[str, str], str] = {}
        self._editing_enabled = True
        self._layout_center_x = LOOP_NODE_WIDTH / 2.0
        self._undo_stack = QUndoStack(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(TOOLBAR_SPACING)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(TOOLBAR_SPACING)
        self.fit_button = QPushButton("适合内容")
        self.zoom_button = QPushButton("100%")
        self.root_scope_button = QPushButton("当前任务")
        self.root_scope_button.setAccessibleName("返回工作流根作用域")
        self.root_scope_button.clicked.connect(self.leave_scope)
        self.root_scope_button.setVisible(False)
        for button, accessible_name in (
            (self.fit_button, "画布适合内容"),
            (self.zoom_button, "画布恢复百分之百缩放"),
        ):
            button.setAccessibleName(accessible_name)
            button.setMinimumSize(96, TOUCH_TARGET_SIZE)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        toolbar.insertWidget(0, self.root_scope_button)
        layout.addLayout(toolbar)

        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)
        self.view = WorkflowCanvasView(self)
        self.view.setScene(self.scene)
        self.view.setAccessibleName("任务工作流画布")
        self.view.setMinimumHeight(220)
        self.view.setMinimumWidth(240)
        layout.addWidget(self.view, stretch=1)

        self.fit_button.clicked.connect(self.view.fit_workflow)
        self.zoom_button.clicked.connect(self.view.reset_zoom)
        self.view.action_dropped.connect(self._on_action_dropped)
        self.view.task_dropped.connect(self._on_task_dropped)
        self.view.delete_requested.connect(self.delete_selected)
        self.view.undo_requested.connect(self.undo)
        self.view.redo_requested.connect(self.redo)
        self.view.select_all_requested.connect(self._select_all_nodes)
        self.view.clear_selection_requested.connect(self.scene.clearSelection)
        self.view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.view.customContextMenuRequested.connect(
            self._show_context_menu
        )
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self._undo_stack.canUndoChanged.connect(self.can_undo_changed)
        self._undo_stack.canRedoChanged.connect(self.can_redo_changed)
        self._apply_palette()
        self._rebuild_scene()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self._apply_palette()
            self.scene.update()

    def render_entries(
        self,
        entries: Sequence[SequenceEntry],
        *,
        clear_history: bool = True,
    ) -> None:
        self._root_entries = _clone_entries(entries)
        self._scope_path = ()
        self._entries = self._root_entries
        self.root_scope_button.setVisible(False)
        self._compiled = None
        self._parallel_branch_states.clear()
        if clear_history:
            self._undo_stack.clear()
        self._rebuild_scene()

    def get_entries(self) -> list[SequenceEntry]:
        return list(_clone_entries(self._root_entries))

    def document(
        self,
        *,
        workflow_id: str,
        name: str,
        revision: int,
    ) -> WorkflowDocument:
        return WorkflowDocument.from_entries(
            workflow_id=workflow_id,
            name=name,
            revision=revision,
            entries=self._root_entries,
            positions=(
                {
                    entry.uuid: CanvasPosition(
                        0.0,
                        self._node_items[entry.uuid].scenePos().y(),
                    )
                    for entry in self._root_entries
                }
                if not self._scope_path
                else {}
            ),
        )

    def insert_entry(
        self,
        entry: SequenceEntry,
        index: int | None = None,
    ) -> None:
        insertion_index = (
            self.entry_count()
            if index is None
            else _require_insert_index(index, self.entry_count())
        )
        updated = list(_clone_entries(self._entries))
        inserted = _clone_canvas_entry(entry)
        updated.insert(insertion_index, inserted)
        self._push(updated, "插入节点", (inserted.uuid,))

    def enter_subworkflow(self, subworkflow_uuid: str) -> bool:
        subworkflow = next(
            (
                entry
                for entry in self._entries
                if isinstance(entry, SubworkflowBlock)
                and entry.uuid == subworkflow_uuid
            ),
            None,
        )
        if subworkflow is None:
            return False
        self._scope_path = (*self._scope_path, subworkflow_uuid)
        self._entries = _scope_entries(self._root_entries, self._scope_path)
        self.root_scope_button.setText(f"← {subworkflow.name}")
        self.root_scope_button.setVisible(True)
        self._rebuild_scene()
        return True

    def leave_scope(self) -> None:
        if not self._scope_path:
            return
        self._scope_path = self._scope_path[:-1]
        self._entries = _scope_entries(self._root_entries, self._scope_path)
        self.root_scope_button.setVisible(bool(self._scope_path))
        if self._scope_path:
            self.root_scope_button.setText(f"← {self._scope_path[-1]}")
        self._rebuild_scene()

    def insert_action(
        self,
        action: ActionDefinition,
        index: int | None = None,
    ) -> None:
        insertion_index = (
            self.entry_count()
            if index is None
            else _require_insert_index(index, self.entry_count())
        )
        updated = list(_clone_entries(self._entries))
        item = SequenceItem.from_definition(action)
        updated.insert(insertion_index, item)
        self._push(updated, "插入动作", (item.uuid,))

    def insert_action_into_loop(
        self,
        loop_uuid: str,
        child_index: int,
        action: ActionDefinition,
    ) -> bool:
        """Insert an action at an explicit position inside a loop block."""
        updated = list(_clone_entries(self._entries))
        for entry in updated:
            if not isinstance(entry, LoopBlock) or entry.uuid != loop_uuid:
                continue
            insertion_index = _require_insert_index(
                child_index,
                len(entry.items),
            )
            item = SequenceItem.from_definition(action)
            entry.items.insert(insertion_index, item)
            self._push(updated, "向循环插入动作", (loop_uuid,))
            return True
        return False

    def insert_action_into_parallel(
        self,
        parallel_uuid: str,
        branch_id: str,
        child_index: int,
        action: ActionDefinition,
    ) -> bool:
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        branch = next(
            (item for item in parallel.branches if item.branch_id == branch_id),
            None,
        )
        if branch is None:
            return False
        insertion_index = _require_insert_index(child_index, len(branch.items))
        item = SequenceItem.from_definition(action)
        branch.items.insert(insertion_index, item)
        self._push(updated, "向并行分支插入动作", (parallel_uuid,))
        return True

    def add_parallel_branch(
        self,
        parallel_uuid: str,
        action: ActionDefinition,
    ) -> bool:
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        if len(parallel.branches) >= 8:
            raise ValueError("并行节点最多支持 8 个分支")
        parallel.branches.append(ParallelBranch(
            branch_id=str(uuid4()),
            items=[SequenceItem.from_definition(action)],
        ))
        self._push(updated, "新增并行分支", (parallel_uuid,))
        return True

    def wrap_selected_in_parallel(self) -> ParallelBlock:
        rows = self.selected_entry_rows()
        if len(rows) < 2:
            raise ValueError("创建并行至少需要选择两个节点")
        if len(rows) > 8:
            raise ValueError("并行节点最多支持 8 个分支")
        if rows != list(range(rows[0], rows[-1] + 1)):
            raise ValueError("只能将连续节点创建为并行分支")
        selected_entries = [
            _clone_canvas_entry(self._entries[row]) for row in rows
        ]
        parallel = ParallelBlock(
            uuid=str(uuid4()),
            branches=[
                ParallelBranch(str(uuid4()), [entry])
                for entry in selected_entries
            ],
        )
        updated = list(_clone_entries(self._entries))
        updated[rows[0] : rows[-1] + 1] = [parallel]
        self._push(updated, "创建并行", (parallel.uuid,))
        cloned = _clone_canvas_entry(parallel)
        if not isinstance(cloned, ParallelBlock):
            raise AssertionError("parallel clone changed entry type")
        return cloned

    def unwrap_selected_parallel(self) -> bool:
        current_row = self.current_entry_row()
        if not 0 <= current_row < self.entry_count():
            return False
        updated = list(_clone_entries(self._entries))
        parallel = updated[current_row]
        if not isinstance(parallel, ParallelBlock):
            return False
        children = [
            _clone_canvas_entry(child)
            for branch in parallel.branches
            for child in branch.items
        ]
        updated[current_row : current_row + 1] = children
        selected = (children[0].uuid,) if children else ()
        self._push(updated, "展开并行", selected)
        return True

    def move_current_parallel_branch(self, offset: int) -> bool:
        target = self._current_parallel_child()
        if target is None:
            return False
        parallel_uuid, branch_id, _item_uuid = target
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        source = next(
            index
            for index, branch in enumerate(parallel.branches)
            if branch.branch_id == branch_id
        )
        destination = source + offset
        if not 0 <= destination < len(parallel.branches):
            return False
        branch = parallel.branches.pop(source)
        parallel.branches.insert(destination, branch)
        self._push(updated, "移动并行分支", (parallel_uuid,))
        return True

    def move_current_parallel_item(self, branch_offset: int) -> bool:
        target = self._current_parallel_child()
        if target is None:
            return False
        parallel_uuid, branch_id, item_uuid = target
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        source_index = next(
            index
            for index, branch in enumerate(parallel.branches)
            if branch.branch_id == branch_id
        )
        destination_index = source_index + branch_offset
        if not 0 <= destination_index < len(parallel.branches):
            return False
        source_branch = parallel.branches[source_index]
        if len(source_branch.items) <= 1:
            raise ValueError("并行分支至少需要保留一个节点")
        item_index = next(
            index
            for index, item in enumerate(source_branch.items)
            if item.uuid == item_uuid
        )
        moved = source_branch.items.pop(item_index)
        parallel.branches[destination_index].items.append(moved)
        self._push(updated, "移动节点到相邻分支", (parallel_uuid,))
        return True

    def delete_current_parallel_item(self) -> bool:
        target = self._current_parallel_child()
        if target is None:
            return False
        parallel_uuid, branch_id, item_uuid = target
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        branch = next(
            item for item in parallel.branches if item.branch_id == branch_id
        )
        if len(branch.items) <= 1:
            raise ValueError("并行分支至少需要保留一个节点")
        branch.items = [item for item in branch.items if item.uuid != item_uuid]
        self._push(updated, "删除并行分支节点", (parallel_uuid,))
        return True

    def remove_current_parallel_branch(self) -> bool:
        target = self._current_parallel_child()
        if target is None:
            return False
        parallel_uuid, branch_id, _item_uuid = target
        updated = list(_clone_entries(self._entries))
        parallel = _find_parallel(updated, parallel_uuid)
        if parallel is None:
            return False
        if len(parallel.branches) <= 2:
            raise ValueError("并行节点至少需要保留两个分支")
        parallel.branches = [
            branch for branch in parallel.branches
            if branch.branch_id != branch_id
        ]
        self._push(updated, "删除并行分支", (parallel_uuid,))
        return True

    def move_selected(self, offset: int) -> bool:
        current = self.current_entry_row()
        target = current + offset
        if current < 0 or not 0 <= target < self.entry_count():
            return False
        updated = list(_clone_entries(self._entries))
        entry = updated.pop(current)
        updated.insert(target, entry)
        self._push(updated, "移动节点", (entry.uuid,))
        return True

    def delete_selected(self) -> bool:
        rows = self.selected_entry_rows()
        if not rows:
            current = self.current_entry_row()
            rows = [] if current < 0 else [current]
        if not rows:
            return False
        selected = set(rows)
        updated = [
            entry
            for index, entry in enumerate(_clone_entries(self._entries))
            if index not in selected
        ]
        self._push(updated, "删除节点")
        return True

    def wrap_selected_in_loop(self, repeat_count: int) -> int:
        if repeat_count < 2:
            raise ValueError("循环次数必须至少为 2")
        rows = self.selected_entry_rows()
        if not rows:
            raise ValueError("请选择要循环的连续动作")
        if rows != list(range(rows[0], rows[-1] + 1)):
            raise ValueError("只能循环连续选中的项目")
        selected_items: list[SequenceItem] = []
        for row in rows:
            entry = self._entries[row]
            if isinstance(entry, LoopBlock):
                if not all(isinstance(child, SequenceItem) for child in entry.items):
                    raise ValueError("包含嵌套控制流的循环不能再次扁平包装")
                selected_items.extend(
                    SequenceItem.from_dict(child.to_dict())
                    for child in entry.items
                    if isinstance(child, SequenceItem)
                )
                continue
            if not isinstance(entry, SequenceItem):
                raise ValueError("并行节点不能直接包装为循环")
            selected_items.append(SequenceItem.from_dict(entry.to_dict()))
        if not selected_items:
            raise ValueError("未找到可循环的动作")
        loop = LoopBlock.from_sequence_items(selected_items, repeat_count)
        updated = list(_clone_entries(self._entries))
        updated[rows[0] : rows[-1] + 1] = [loop]
        self._push(updated, "创建循环块", (loop.uuid,))
        return loop.total_steps

    def current_sequence_item(self) -> SequenceItem | None:
        for entry in self._entries:
            item = _find_sequence_item(entry, self._current_item_uuid)
            if item is not None:
                cloned = clone_sequence_entry(item)
                return cloned if isinstance(cloned, SequenceItem) else None
        return None

    def update_current_action(self, definition: ActionDefinition) -> bool:
        if not self._current_item_uuid:
            return False
        updated = list(_clone_entries(self._entries))
        for entry in updated:
            item = _find_sequence_item(entry, self._current_item_uuid)
            if item is None:
                continue
            item.definition = definition
            self._push(updated, "修改动作参数")
            return True
        return False

    def entry_count(self) -> int:
        return len(self._entries)

    def current_entry_row(self) -> int:
        selected_rows = self.selected_entry_rows()
        if selected_rows:
            return selected_rows[0]
        for index, entry in enumerate(self._entries):
            if _entry_contains_uuid(entry, self._current_item_uuid):
                return index
        return -1

    def current_loop_block(self) -> LoopBlock | None:
        current_row = self.current_entry_row()
        if not 0 <= current_row < self.entry_count():
            return None
        entry = self._entries[current_row]
        if not isinstance(entry, LoopBlock):
            return None
        cloned = _clone_canvas_entry(entry)
        return cloned if isinstance(cloned, LoopBlock) else None

    def update_current_loop_count(self, repeat_count: int) -> bool:
        if repeat_count < 2:
            raise ValueError("循环次数必须至少为 2")
        current_row = self.current_entry_row()
        if not 0 <= current_row < self.entry_count():
            return False
        updated = list(_clone_entries(self._entries))
        entry = updated[current_row]
        if not isinstance(entry, LoopBlock):
            return False
        entry.repeat_count = repeat_count
        entry.current_iteration = 0
        self._push(updated, "修改循环次数", (entry.uuid,))
        return True

    def unwrap_selected_loop(self) -> bool:
        current_row = self.current_entry_row()
        if not 0 <= current_row < self.entry_count():
            return False
        updated = list(_clone_entries(self._entries))
        entry = updated[current_row]
        if not isinstance(entry, LoopBlock):
            return False
        children = [clone_sequence_entry(child) for child in entry.items]
        updated[current_row : current_row + 1] = children
        selected = (children[0].uuid,) if children else ()
        self._push(updated, "展开循环", selected)
        return True

    def selected_entry_rows(self) -> list[int]:
        selected_ids = {
            item.node_id
            for item in self.scene.selectedItems()
            if isinstance(item, WorkflowNodeItem)
        }
        return [
            index
            for index, entry in enumerate(self._entries)
            if entry.uuid in selected_ids
        ]

    def set_current_entry_row(self, index: int) -> None:
        if not 0 <= index < self.entry_count():
            return
        self.scene.clearSelection()
        item = self._node_items[self._entries[index].uuid]
        item.setSelected(True)
        self._current_item_uuid = self._entries[index].uuid
        self.view.centerOn(item)

    def set_selected_entry_rows(self, rows: Sequence[int]) -> None:
        selected = set(rows)
        if any(not 0 <= row < self.entry_count() for row in selected):
            raise IndexError("selected workflow row is outside the document")
        self.scene.clearSelection()
        for index, entry in enumerate(self._entries):
            self._node_items[entry.uuid].setSelected(index in selected)
        if selected:
            first = min(selected)
            self._current_item_uuid = self._entries[first].uuid

    def undo(self) -> None:
        if self._editing_enabled:
            self._undo_stack.undo()

    def redo(self) -> None:
        if self._editing_enabled:
            self._undo_stack.redo()

    def begin_execution(self, compiled: CompiledWorkflow) -> None:
        self._compiled = compiled
        self._parallel_branch_states.clear()
        self._editing_enabled = False
        self._root_entries = _clone_entries(compiled.entries)
        self._scope_path = ()
        self._entries = self._root_entries
        self.root_scope_button.setVisible(False)
        self.view.setAcceptDrops(False)
        self.can_undo_changed.emit(False)
        self.can_redo_changed.emit(False)
        self._rebuild_scene()

    @property
    def execution_mapping_active(self) -> bool:
        return self._compiled is not None

    def update_execution_step(
        self,
        runtime_index: int,
        item: SequenceItem,
    ) -> None:
        if self._compiled is None:
            return
        node_id = self._compiled.node_id_for_step(runtime_index)
        if node_id is None:
            return
        updated = list(_clone_entries(self._entries))
        for entry in updated:
            if entry.uuid != node_id:
                continue
            rendered_item = _find_sequence_item(entry, item.uuid)
            if rendered_item is not None:
                rendered_item.status = item.status
            break
        self._root_entries = tuple(updated)
        self._entries = self._root_entries
        self._rebuild_scene(selected_node_ids=(node_id,))
        node_item = self._node_items.get(node_id)
        if node_item is not None:
            self.view.centerOn(node_item)

    def update_loop_progress(
        self,
        loop_uuid: str,
        current_iteration: int,
    ) -> None:
        if self._compiled is None:
            return
        node_id = self._compiled.node_id_for_loop(loop_uuid)
        if node_id is None:
            return
        updated = list(_clone_entries(self._entries))
        for entry in updated:
            loop = _find_loop(entry, loop_uuid)
            if loop is not None:
                loop.current_iteration = current_iteration
                break
        self._root_entries = tuple(updated)
        self._entries = self._root_entries
        self._rebuild_scene(selected_node_ids=(node_id,))

    def update_parallel_branch_state(
        self,
        parallel_uuid: str,
        branch_id: str,
        state: str,
    ) -> None:
        if self._compiled is None:
            return
        node_id = self._compiled.node_id_for_parallel(parallel_uuid)
        if node_id is None:
            return
        self._parallel_branch_states[(parallel_uuid, branch_id)] = state
        self._rebuild_scene(selected_node_ids=(node_id,))

    def finish_execution(self) -> None:
        self._editing_enabled = True
        self._compiled = None
        self._parallel_branch_states.clear()
        self.view.setAcceptDrops(True)
        self._rebuild_scene()
        self.can_undo_changed.emit(self._undo_stack.canUndo())
        self.can_redo_changed.emit(self._undo_stack.canRedo())

    def _push(
        self,
        entries: Sequence[SequenceEntry],
        text: str,
        selected_node_ids: tuple[str, ...] = (),
    ) -> None:
        if not self._editing_enabled:
            return
        command = _ReplaceEntriesCommand(
            self,
            _clone_entries(self._root_entries),
            _replace_scope(
                self._root_entries,
                self._scope_path,
                entries,
            ),
            text,
            selected_node_ids,
        )
        self._undo_stack.push(command)

    def _apply_entries(
        self,
        entries: Sequence[SequenceEntry],
        *,
        selected_node_ids: tuple[str, ...] = (),
        publish: bool,
    ) -> None:
        self._root_entries = _clone_entries(entries)
        if not _scope_exists(self._root_entries, self._scope_path):
            self._scope_path = ()
            self.root_scope_button.setVisible(False)
        self._entries = _scope_entries(self._root_entries, self._scope_path)
        self._compiled = None
        self._rebuild_scene(selected_node_ids=selected_node_ids)
        if publish:
            self.sequence_changed.emit()

    def _rebuild_scene(
        self,
        *,
        selected_node_ids: tuple[str, ...] = (),
    ) -> None:
        self.scene.blockSignals(True)
        self._endpoint_items = ()
        self.scene.clear()
        self._node_items.clear()
        content_widths = (
            LOOP_NODE_WIDTH,
            *(_entry_node_width(entry) for entry in self._entries),
        )
        self._layout_center_x = max(content_widths) / 2.0
        center_x = self._layout_center_x
        y = CANVAS_MARGIN
        start = StartEndItem("开始", QColor("#16a34a"))
        start.setPos(center_x - 60.0, y)
        self.scene.addItem(start)
        previous_bottom = y + start.boundingRect().height()
        y = previous_bottom + NODE_GAP / 2.0

        for index, entry in enumerate(self._entries):
            if self._editing_enabled:
                insertion = InsertionItem(index)
                insertion.setPos(center_x - 22.0, y - 22.0)
                insertion.insert_requested.connect(
                    self.insert_action_requested.emit
                )
                self.scene.addItem(insertion)
            node_y = y + NODE_GAP / 2.0
            node = WorkflowNodeItem(
                entry.uuid,
                _clone_canvas_entry(entry),
                editing_enabled=self._editing_enabled,
                parallel_branch_states={
                    branch_id: state
                    for (parallel_id, branch_id), state
                    in self._parallel_branch_states.items()
                    if parallel_id == entry.uuid
                },
            )
            node.setPos(center_x - node.node_width / 2.0, node_y)
            node.setSelected(entry.uuid in selected_node_ids)
            node.focused.connect(self._on_node_focused)
            node.edit_requested.connect(self._on_node_edit_requested)
            node.subworkflow_open_requested.connect(self.enter_subworkflow)
            node.move_requested.connect(self._on_node_moved)
            node.loop_insert_requested.connect(
                self.insert_loop_action_requested.emit
            )
            node.parallel_insert_requested.connect(
                self.insert_parallel_action_requested.emit
            )
            self.scene.addItem(node)
            self._node_items[entry.uuid] = node
            self._add_edge(previous_bottom, node_y)
            previous_bottom = node_y + node.node_height
            y = previous_bottom + NODE_GAP / 2.0

        if self._editing_enabled:
            insertion = InsertionItem(len(self._entries))
            insertion.setPos(center_x - 22.0, y - 22.0)
            insertion.insert_requested.connect(
                self.insert_action_requested.emit
            )
            self.scene.addItem(insertion)
        end_y = y + NODE_GAP / 2.0
        end = StartEndItem("结束", QColor("#64748b"))
        end.setPos(center_x - 60.0, end_y)
        self.scene.addItem(end)
        self._endpoint_items = (start, end)
        self._add_edge(previous_bottom, end_y)
        bounds = self.scene.itemsBoundingRect().adjusted(
            -CANVAS_MARGIN,
            -CANVAS_MARGIN,
            CANVAS_MARGIN,
            CANVAS_MARGIN,
        )
        self.scene.setSceneRect(bounds)
        self.view.setSceneRect(bounds)
        self.scene.blockSignals(False)
        self._on_selection_changed()

    def _add_edge(self, source_y: float, target_y: float) -> None:
        center_x = self._layout_center_x
        path = QPainterPath()
        path.moveTo(center_x, source_y)
        midpoint = (source_y + target_y) / 2.0
        path.cubicTo(center_x, midpoint, center_x, midpoint, center_x, target_y)
        edge = QGraphicsPathItem(path)
        edge.setPen(QPen(canvas_colors().edge, 2.0))
        edge.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        edge.setZValue(-10.0)
        self.scene.addItem(edge)

    def _apply_palette(self) -> None:
        self.scene.setBackgroundBrush(canvas_colors(self.palette()).canvas)

    def _on_action_dropped(
        self,
        action: ActionDefinition,
        scene_x: float,
        scene_y: float,
    ) -> None:
        for index, entry in enumerate(self._entries):
            node = self._node_items[entry.uuid]
            top = node.scenePos().y()
            if (
                isinstance(entry, ParallelBlock)
                and top <= scene_y <= top + node.node_height
            ):
                target = node.parallel_drop_target(
                    node.mapFromScene(QPointF(scene_x, scene_y))
                )
                if target is not None:
                    branch_id, child_index = target
                    self.insert_action_into_parallel(
                        entry.uuid,
                        branch_id,
                        child_index,
                        action,
                    )
                return
            if (
                isinstance(entry, LoopBlock)
                and top <= scene_y <= top + node.node_height
            ):
                updated = list(_clone_entries(self._entries))
                loop = updated[index]
                if isinstance(loop, LoopBlock):
                    loop.items.append(SequenceItem.from_definition(action))
                    self._push(updated, "向循环添加动作", (loop.uuid,))
                return
        self.insert_action(action, self._insertion_index(scene_y))

    def _on_task_dropped(
        self,
        task_name: str,
        scene_x: float,
        scene_y: float,
    ) -> None:
        del scene_x
        self.insert_subworkflow_requested.emit(
            task_name,
            self._insertion_index(scene_y),
        )

    def _insertion_index(self, scene_y: float) -> int:
        for index, entry in enumerate(self._entries):
            node = self._node_items[entry.uuid]
            if scene_y < node.scenePos().y() + node.node_height / 2.0:
                return index
        return self.entry_count()

    def _on_node_moved(self, node_id: str, target_center_y: float) -> None:
        source_index = next(
            (
                index
                for index, entry in enumerate(self._entries)
                if entry.uuid == node_id
            ),
            None,
        )
        if source_index is None:
            return
        target_index = sum(
            target_center_y
            > self._node_items[entry.uuid].scenePos().y()
            + self._node_items[entry.uuid].node_height / 2.0
            for entry in self._entries
            if entry.uuid != node_id
        )
        if target_index == source_index:
            self._rebuild_scene(selected_node_ids=(node_id,))
            return
        updated = list(_clone_entries(self._entries))
        moved = updated.pop(source_index)
        updated.insert(target_index, moved)
        self._push(updated, "拖动排序", (node_id,))

    def _on_node_focused(
        self,
        node_id: str,
        item_uuid: str,
        additive: bool,
    ) -> None:
        self._current_item_uuid = item_uuid
        item = self._node_items.get(node_id)
        if item is None:
            return
        if additive:
            item.setSelected(not item.isSelected())
        else:
            self.scene.clearSelection()
            item.setSelected(True)

    def _on_node_edit_requested(self, node_id: str, item_uuid: str) -> None:
        self._on_node_focused(node_id, item_uuid, False)
        self.edit_requested.emit()

    def _select_all_nodes(self) -> None:
        if not self._editing_enabled:
            return
        for item in self._node_items.values():
            item.setSelected(True)

    def _show_context_menu(self, view_position: QPoint) -> None:
        if not self._editing_enabled:
            return
        pointed_item = self.view.itemAt(view_position)
        if isinstance(pointed_item, WorkflowNodeItem):
            if not pointed_item.isSelected():
                self.scene.clearSelection()
                pointed_item.setSelected(True)
            local_position = pointed_item.mapFromScene(
                self.view.mapToScene(view_position)
            )
            self._current_item_uuid = pointed_item.item_uuid_at(
                local_position
            )
        menu = self._create_context_menu()
        if menu is None:
            return
        menu.exec(self.view.viewport().mapToGlobal(view_position))

    def _create_context_menu(self) -> QMenu | None:
        selected_rows = self.selected_entry_rows()
        if not selected_rows:
            return None
        menu = QMenu(self.view)
        if len(selected_rows) == 1:
            entry = self._entries[selected_rows[0]]
            if self.current_sequence_item() is not None:
                self._add_menu_action(menu, "编辑参数", self.edit_requested.emit)
            self._add_menu_action(menu, "上移", lambda: self.move_selected(-1))
            self._add_menu_action(menu, "下移", lambda: self.move_selected(1))
            if isinstance(entry, LoopBlock):
                self._add_menu_action(menu, "展开循环", self.unwrap_selected_loop)
            if isinstance(entry, ParallelBlock):
                self._add_parallel_context_actions(menu, entry)
            if isinstance(entry, SubworkflowBlock):
                self._add_menu_action(
                    menu,
                    "进入子流程",
                    lambda: self.enter_subworkflow(entry.uuid),
                )
        selected_entries = [self._entries[row] for row in selected_rows]
        if all(not isinstance(entry, ParallelBlock) for entry in selected_entries):
            self._add_menu_action(
                menu,
                "创建循环",
                self.wrap_selection_requested.emit,
            )
        if len(selected_rows) >= 2:
            self._add_menu_action(menu, "创建并行", self.wrap_selected_in_parallel)
        menu.addSeparator()
        self._add_menu_action(menu, "删除所选", self.delete_selected)
        return menu

    def _add_parallel_context_actions(
        self,
        menu: QMenu,
        parallel: ParallelBlock,
    ) -> None:
        if len(parallel.branches) < 8:
            self._add_menu_action(
                menu,
                "新增并行分支",
                lambda: self.add_parallel_branch_requested.emit(parallel.uuid),
            )
        target = self._current_parallel_child()
        if target is not None:
            _parallel_uuid, branch_id, _item_uuid = target
            branch_index = next(
                index for index, branch in enumerate(parallel.branches)
                if branch.branch_id == branch_id
            )
            branch = parallel.branches[branch_index]
            if branch_index > 0:
                self._add_menu_action(
                    menu,
                    "分支左移",
                    lambda: self.move_current_parallel_branch(-1),
                )
                if len(branch.items) > 1:
                    self._add_menu_action(
                        menu,
                        "节点移至左侧分支",
                        lambda: self.move_current_parallel_item(-1),
                    )
            if branch_index < len(parallel.branches) - 1:
                self._add_menu_action(
                    menu,
                    "分支右移",
                    lambda: self.move_current_parallel_branch(1),
                )
                if len(branch.items) > 1:
                    self._add_menu_action(
                        menu,
                        "节点移至右侧分支",
                        lambda: self.move_current_parallel_item(1),
                    )
            if len(branch.items) > 1:
                self._add_menu_action(
                    menu,
                    "删除当前分支节点",
                    self.delete_current_parallel_item,
                )
            if len(parallel.branches) > 2:
                self._add_menu_action(
                    menu,
                    "删除当前分支",
                    self.remove_current_parallel_branch,
                )
        self._add_menu_action(menu, "展开并行", self.unwrap_selected_parallel)

    @staticmethod
    def _add_menu_action(
        menu: QMenu,
        text: str,
        callback: Callable[[], object],
    ) -> None:
        action = QAction(text, menu)
        action.triggered.connect(callback)
        menu.addAction(action)

    def _on_selection_changed(self) -> None:
        selected = self.scene.selectedItems()
        if selected:
            node = next(
                (item for item in selected if isinstance(item, WorkflowNodeItem)),
                None,
            )
            if node is not None and not self._current_item_uuid:
                self._current_item_uuid = node.entry.uuid
        else:
            self._current_item_uuid = ""

    def _current_parallel_child(self) -> tuple[str, str, str] | None:
        for entry in self._entries:
            if not isinstance(entry, ParallelBlock):
                continue
            for branch in entry.branches:
                if any(
                    _entry_contains_uuid(child, self._current_item_uuid)
                    for child in branch.items
                ):
                    return entry.uuid, branch.branch_id, self._current_item_uuid
        return None


def _clone_entries(
    entries: Sequence[SequenceEntry],
) -> tuple[SequenceEntry, ...]:
    return tuple(_clone_canvas_entry(entry) for entry in entries)


def _clone_canvas_entry(entry: SequenceEntry) -> SequenceEntry:
    """Clone editor state while preserving transient execution presentation."""
    if isinstance(entry, LoopBlock):
        return LoopBlock(
            uuid=entry.uuid,
            items=[_clone_canvas_entry(child) for child in entry.items],
            repeat_count=entry.repeat_count,
            current_iteration=entry.current_iteration,
        )
    if isinstance(entry, ParallelBlock):
        return ParallelBlock(
            uuid=entry.uuid,
            branches=[
                ParallelBranch(
                    branch_id=branch.branch_id,
                    items=[_clone_canvas_entry(child) for child in branch.items],
                )
                for branch in entry.branches
            ],
            join_policy=entry.join_policy,
            failure_policy=entry.failure_policy,
        )
    if isinstance(entry, SubworkflowBlock):
        return SubworkflowBlock(
            uuid=entry.uuid,
            name=entry.name,
            items=[_clone_canvas_entry(child) for child in entry.items],
            source_workflow_id=entry.source_workflow_id,
            source_revision=entry.source_revision,
        )
    if isinstance(entry, SequenceItem):
        return SequenceItem.from_dict(entry.to_dict())
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _entry_node_width(entry: SequenceEntry) -> float:
    if not isinstance(entry, ParallelBlock):
        return LOOP_NODE_WIDTH if isinstance(entry, LoopBlock) else NODE_WIDTH
    branch_count = max(2, len(entry.branches))
    return (
        2 * PARALLEL_BRANCH_PADDING
        + branch_count * PARALLEL_BRANCH_WIDTH
        + (branch_count - 1) * PARALLEL_BRANCH_GAP
    )


def _entry_contains_uuid(entry: SequenceEntry, item_uuid: str) -> bool:
    if entry.uuid == item_uuid:
        return True
    if isinstance(entry, LoopBlock):
        return any(_entry_contains_uuid(child, item_uuid) for child in entry.items)
    if isinstance(entry, ParallelBlock):
        return any(
            _entry_contains_uuid(child, item_uuid)
            for branch in entry.branches
            for child in branch.items
        )
    if isinstance(entry, SubworkflowBlock):
        return any(_entry_contains_uuid(child, item_uuid) for child in entry.items)
    return False


def _find_sequence_item(
    entry: SequenceEntry,
    item_uuid: str,
) -> SequenceItem | None:
    if isinstance(entry, SequenceItem):
        return entry if entry.uuid == item_uuid else None
    if isinstance(entry, (LoopBlock, SubworkflowBlock)):
        children = entry.items
    else:
        children = [child for branch in entry.branches for child in branch.items]
    return next(
        (
            found
            for child in children
            if (found := _find_sequence_item(child, item_uuid)) is not None
        ),
        None,
    )


def _find_loop(entry: SequenceEntry, loop_uuid: str) -> LoopBlock | None:
    if isinstance(entry, LoopBlock) and entry.uuid == loop_uuid:
        return entry
    if isinstance(entry, SequenceItem):
        return None
    if isinstance(entry, (LoopBlock, SubworkflowBlock)):
        children = entry.items
    else:
        children = [child for branch in entry.branches for child in branch.items]
    return next(
        (
            found
            for child in children
            if (found := _find_loop(child, loop_uuid)) is not None
        ),
        None,
    )


def _find_parallel(
    entries: Sequence[SequenceEntry],
    parallel_uuid: str,
) -> ParallelBlock | None:
    for entry in entries:
        if isinstance(entry, ParallelBlock):
            if entry.uuid == parallel_uuid:
                return entry
            found = _find_parallel(
                [child for branch in entry.branches for child in branch.items],
                parallel_uuid,
            )
            if found is not None:
                return found
        elif isinstance(entry, LoopBlock):
            found = _find_parallel(entry.items, parallel_uuid)
            if found is not None:
                return found
        elif isinstance(entry, SubworkflowBlock):
            found = _find_parallel(entry.items, parallel_uuid)
            if found is not None:
                return found
    return None


def _scope_entries(
    root: Sequence[SequenceEntry],
    path: Sequence[str],
) -> tuple[SequenceEntry, ...]:
    current = _clone_entries(root)
    for subworkflow_uuid in path:
        subworkflow = next(
            (
                entry
                for entry in current
                if isinstance(entry, SubworkflowBlock)
                and entry.uuid == subworkflow_uuid
            ),
            None,
        )
        if subworkflow is None:
            return _clone_entries(root)
        current = _clone_entries(subworkflow.items)
    return current


def _scope_exists(
    root: Sequence[SequenceEntry],
    path: Sequence[str],
) -> bool:
    current = _clone_entries(root)
    for subworkflow_uuid in path:
        subworkflow = next(
            (
                entry
                for entry in current
                if isinstance(entry, SubworkflowBlock)
                and entry.uuid == subworkflow_uuid
            ),
            None,
        )
        if subworkflow is None:
            return False
        current = _clone_entries(subworkflow.items)
    return True


def _replace_scope(
    root: Sequence[SequenceEntry],
    path: Sequence[str],
    replacement: Sequence[SequenceEntry],
) -> tuple[SequenceEntry, ...]:
    if not path:
        return _clone_entries(replacement)
    updated = list(_clone_entries(root))
    target_uuid = path[0]
    for index, entry in enumerate(updated):
        if not isinstance(entry, SubworkflowBlock) or entry.uuid != target_uuid:
            continue
        child_items = _replace_scope(entry.items, path[1:], replacement)
        updated[index] = SubworkflowBlock(
            uuid=entry.uuid,
            name=entry.name,
            items=list(child_items),
            source_workflow_id=entry.source_workflow_id,
            source_revision=entry.source_revision,
        )
        return tuple(updated)
    raise ValueError(f"subworkflow scope does not exist: {target_uuid}")


def _require_insert_index(index: int, length: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index <= length:
        raise IndexError(f"index {index} is outside insertion range 0..{length}")
    return index

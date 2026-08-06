"""Constrained workflow canvas with snapshot-based undo commands."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsScene,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....application import CompiledWorkflow
from ....domain.models import (
    ActionDefinition,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
)
from ....domain.workflow import (
    CanvasPosition,
    WorkflowDocument,
    WorkflowNode,
    clone_sequence_entry,
)
from .items import InsertionItem, StartEndItem, WorkflowNodeItem
from .tokens import CANVAS_MARGIN, NODE_GAP, NODE_WIDTH
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
    selection_summary_changed = Signal(object)
    can_undo_changed = Signal(bool)
    can_redo_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: tuple[SequenceEntry, ...] = ()
        self._node_items: dict[str, WorkflowNodeItem] = {}
        self._current_item_uuid = ""
        self._compiled: CompiledWorkflow | None = None
        self._editing_enabled = True
        self._undo_stack = QUndoStack(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.fit_button = QPushButton("适合内容")
        self.zoom_button = QPushButton("100%")
        for button in (self.fit_button, self.zoom_button):
            button.setMinimumSize(88, 36)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.scene = QGraphicsScene(self)
        self.view = WorkflowCanvasView(self)
        self.view.setScene(self.scene)
        self.view.setAccessibleName("任务工作流画布")
        self.view.setMinimumHeight(220)
        layout.addWidget(self.view, stretch=1)

        self.fit_button.clicked.connect(self.view.fit_workflow)
        self.zoom_button.clicked.connect(self.view.reset_zoom)
        self.view.action_dropped.connect(self._on_action_dropped)
        self.view.delete_requested.connect(self.delete_selected)
        self.view.undo_requested.connect(self.undo)
        self.view.redo_requested.connect(self.redo)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self._undo_stack.canUndoChanged.connect(self.can_undo_changed)
        self._undo_stack.canRedoChanged.connect(self.can_redo_changed)
        self._rebuild_scene()

    def render_entries(
        self,
        entries: Sequence[SequenceEntry],
        *,
        clear_history: bool = True,
    ) -> None:
        self._entries = _clone_entries(entries)
        self._compiled = None
        if clear_history:
            self._undo_stack.clear()
        self._rebuild_scene()

    def get_entries(self) -> list[SequenceEntry]:
        return list(_clone_entries(self._entries))

    def document(
        self,
        *,
        workflow_id: str,
        name: str,
        revision: int,
    ) -> WorkflowDocument:
        nodes = tuple(
            WorkflowNode(
                node_id=entry.uuid,
                entry=_clone_canvas_entry(entry),
                position=CanvasPosition(
                    0.0,
                    self._node_items[entry.uuid].scenePos().y(),
                ),
            )
            for entry in self._entries
        )
        return WorkflowDocument(
            workflow_id=workflow_id,
            name=name,
            revision=revision,
            nodes=nodes,
            order=tuple(entry.uuid for entry in self._entries),
        )

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
                selected_items.extend(
                    clone_sequence_entry(child)
                    for child in entry.items
                )
            else:
                selected_items.append(clone_sequence_entry(entry))
        typed_items = [
            item for item in selected_items if isinstance(item, SequenceItem)
        ]
        if not typed_items:
            raise ValueError("未找到可循环的动作")
        loop = LoopBlock.from_sequence_items(typed_items, repeat_count)
        updated = list(_clone_entries(self._entries))
        updated[rows[0] : rows[-1] + 1] = [loop]
        self._push(updated, "创建循环块", (loop.uuid,))
        return loop.total_steps

    def current_sequence_item(self) -> SequenceItem | None:
        for entry in self._entries:
            if isinstance(entry, SequenceItem) and entry.uuid == self._current_item_uuid:
                return clone_sequence_entry(entry)
            if isinstance(entry, LoopBlock):
                for child in entry.items:
                    if child.uuid == self._current_item_uuid:
                        cloned = clone_sequence_entry(child)
                        return cloned if isinstance(cloned, SequenceItem) else None
        return None

    def update_current_action(self, definition: ActionDefinition) -> bool:
        if not self._current_item_uuid:
            return False
        updated = list(_clone_entries(self._entries))
        changed = False
        for entry in updated:
            if isinstance(entry, SequenceItem) and entry.uuid == self._current_item_uuid:
                entry.definition = definition
                changed = True
                break
            if isinstance(entry, LoopBlock):
                for child in entry.items:
                    if child.uuid == self._current_item_uuid:
                        child.definition = definition
                        changed = True
                        break
            if changed:
                break
        if changed:
            self._push(updated, "修改动作参数")
        return changed

    def entry_count(self) -> int:
        return len(self._entries)

    def current_entry_row(self) -> int:
        selected_rows = self.selected_entry_rows()
        if selected_rows:
            return selected_rows[0]
        for index, entry in enumerate(self._entries):
            if self._current_item_uuid == entry.uuid:
                return index
            if isinstance(entry, LoopBlock) and any(
                    child.uuid == self._current_item_uuid
                    for child in entry.items
            ):
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
        self._editing_enabled = False
        self._entries = _clone_entries(compiled.entries)
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
            if isinstance(entry, SequenceItem):
                entry.status = item.status
            else:
                for child in entry.items:
                    if child.uuid == item.uuid:
                        child.status = item.status
                        break
            break
        self._entries = tuple(updated)
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
            if isinstance(entry, LoopBlock) and entry.uuid == loop_uuid:
                entry.current_iteration = current_iteration
                break
        self._entries = tuple(updated)
        self._rebuild_scene(selected_node_ids=(node_id,))

    def finish_execution(self) -> None:
        self._editing_enabled = True
        self._compiled = None
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
            _clone_entries(self._entries),
            _clone_entries(entries),
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
        self._entries = _clone_entries(entries)
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
        self.scene.clear()
        self._node_items.clear()
        center_x = NODE_WIDTH / 2.0
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
                insertion.insert_requested.connect(self.insert_action_requested)
                self.scene.addItem(insertion)
            node_y = y + NODE_GAP / 2.0
            node = WorkflowNodeItem(entry.uuid, _clone_canvas_entry(entry))
            node.setFlag(
                node.GraphicsItemFlag.ItemIsMovable,
                self._editing_enabled,
            )
            node.setPos(0.0, node_y)
            node.setSelected(entry.uuid in selected_node_ids)
            node.move_requested.connect(self._on_node_moved)
            node.focused.connect(self._on_node_focused)
            node.edit_requested.connect(self._on_node_edit_requested)
            self.scene.addItem(node)
            self._node_items[entry.uuid] = node
            self._add_edge(previous_bottom, node_y)
            previous_bottom = node_y + node.node_height
            y = previous_bottom + NODE_GAP / 2.0

        if self._editing_enabled:
            insertion = InsertionItem(len(self._entries))
            insertion.setPos(center_x - 22.0, y - 22.0)
            insertion.insert_requested.connect(self.insert_action_requested)
            self.scene.addItem(insertion)
        end_y = y + NODE_GAP / 2.0
        end = StartEndItem("结束", QColor("#64748b"))
        end.setPos(center_x - 60.0, end_y)
        self.scene.addItem(end)
        self._add_edge(previous_bottom, end_y)
        bounds = self.scene.itemsBoundingRect().adjusted(
            -CANVAS_MARGIN,
            -CANVAS_MARGIN,
            CANVAS_MARGIN,
            CANVAS_MARGIN,
        )
        self.scene.setSceneRect(bounds)
        self.scene.blockSignals(False)
        self._on_selection_changed()

    def _add_edge(self, source_y: float, target_y: float) -> None:
        center_x = NODE_WIDTH / 2.0
        path = QPainterPath()
        path.moveTo(center_x, source_y)
        midpoint = (source_y + target_y) / 2.0
        path.cubicTo(center_x, midpoint, center_x, midpoint, center_x, target_y)
        edge = QGraphicsPathItem(path)
        edge.setPen(QPen(QColor("#94a3b8"), 2.0))
        edge.setZValue(-10.0)
        self.scene.addItem(edge)

    def _on_action_dropped(self, action: ActionDefinition, scene_y: float) -> None:
        for index, entry in enumerate(self._entries):
            node = self._node_items[entry.uuid]
            top = node.scenePos().y()
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

    def _insertion_index(self, scene_y: float) -> int:
        for index, entry in enumerate(self._entries):
            node = self._node_items[entry.uuid]
            if scene_y < node.scenePos().y() + node.node_height / 2.0:
                return index
        return self.entry_count()

    def _on_node_moved(self, node_id: str, scene_y: float) -> None:
        if not self._editing_enabled:
            self._rebuild_scene(selected_node_ids=(node_id,))
            return
        source = next(
            index
            for index, entry in enumerate(self._entries)
            if entry.uuid == node_id
        )
        target = self._insertion_index(scene_y)
        if target > source:
            target -= 1
        target = max(0, min(target, self.entry_count() - 1))
        if source == target:
            self._rebuild_scene(selected_node_ids=(node_id,))
            return
        updated = list(_clone_entries(self._entries))
        entry = updated.pop(source)
        updated.insert(target, entry)
        self._push(updated, "拖动排序", (node_id,))

    def _on_node_focused(self, node_id: str, item_uuid: str) -> None:
        self._current_item_uuid = item_uuid
        item = self._node_items.get(node_id)
        if item is not None and not item.isSelected():
            self.scene.clearSelection()
            item.setSelected(True)
        self._emit_selection_summary()

    def _on_node_edit_requested(self, node_id: str, item_uuid: str) -> None:
        self._on_node_focused(node_id, item_uuid)
        if self.current_sequence_item() is not None:
            self.edit_requested.emit()

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
        self._emit_selection_summary()

    def _emit_selection_summary(self) -> None:
        selected = self.current_sequence_item()
        if selected is not None:
            self.selection_summary_changed.emit(selected)
            return
        current_row = self.current_entry_row()
        entry = (
            _clone_canvas_entry(self._entries[current_row])
            if 0 <= current_row < self.entry_count()
            else None
        )
        self.selection_summary_changed.emit(entry)


def _clone_entries(
    entries: Sequence[SequenceEntry],
) -> tuple[SequenceEntry, ...]:
    return tuple(_clone_canvas_entry(entry) for entry in entries)


def _clone_canvas_entry(entry: SequenceEntry) -> SequenceEntry:
    """Clone editor state while preserving transient execution presentation."""
    if isinstance(entry, LoopBlock):
        return LoopBlock(
            uuid=entry.uuid,
            items=[SequenceItem.from_dict(child.to_dict()) for child in entry.items],
            repeat_count=entry.repeat_count,
            current_iteration=entry.current_iteration,
        )
    if isinstance(entry, SequenceItem):
        return SequenceItem.from_dict(entry.to_dict())
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _require_insert_index(index: int, length: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("index must be an integer")
    if not 0 <= index <= length:
        raise IndexError(f"index {index} is outside insertion range 0..{length}")
    return index

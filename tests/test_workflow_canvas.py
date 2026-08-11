from __future__ import annotations

import gc
import unittest

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QHelpEvent,
    QPalette,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
)

from src.application import WorkflowCompiler
from src.domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)
from src.domain.workflow import WorkflowDocument
from src.gui.drag_preview_style import DRAG_CARD_OPACITY, DRAG_CARD_RELATIVE_SCALE
from src.gui.views import WorkflowCanvasWidget
from src.gui.tooltips import install_tooltip_service
from src.gui.views.workflow_canvas.items import (
    InsertionItem,
    NodeDragPreviewItem,
    StartEndItem,
    WorkflowNodeItem,
)
from src.gui.views.workflow_canvas.tokens import (
    ControlFlowKind,
    INSERT_HOVER_TRANSITION_MS,
    LOOP_CHILD_GAP,
    LOOP_CHILD_HEIGHT,
    LOOP_HEADER_HEIGHT,
    LOOP_NODE_WIDTH,
    LOOP_SECTION_GAP,
    NODE_DRAG_THRESHOLD,
    NODE_HEIGHT,
    NODE_WIDTH,
    PARALLEL_BRANCH_HEADER_HEIGHT,
    PARALLEL_BRANCH_PADDING,
    PARALLEL_BRANCH_WIDTH,
    PARALLEL_CHILD_GAP,
    PARALLEL_HEADER_HEIGHT,
    PARALLEL_SECTION_GAP,
    contrasting_text,
    control_flow_colors,
)


class WorkflowCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.canvas = WorkflowCanvasWidget()

    def test_subworkflow_scope_edits_publish_the_root_document(self) -> None:
        subworkflow = SubworkflowBlock(
            uuid="subworkflow",
            name="Reusable task",
            items=[_item("inside")],
            source_workflow_id="source",
            source_revision=2,
        )
        self.canvas.render_entries((subworkflow,))

        self.assertTrue(self.canvas.enter_subworkflow("subworkflow"))
        self.canvas.insert_action(_action("added"))
        self.canvas.leave_scope()

        root = self.canvas.get_entries()
        self.assertEqual(1, len(root))
        restored = root[0]
        self.assertIsInstance(restored, SubworkflowBlock)
        assert isinstance(restored, SubworkflowBlock)
        self.assertEqual(
            ["inside", "added"],
            [item.definition.id for item in restored.items],
        )
        self.canvas.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.canvas.close()
        self.canvas.deleteLater()
        QApplication.processEvents()

    def test_canvas_view_uses_background_separation_without_outer_frame(self) -> None:
        self.assertEqual(QFrame.Shape.NoFrame, self.canvas.view.frameShape())
        self.assertEqual(
            self.canvas.view.ViewportUpdateMode.FullViewportUpdate,
            self.canvas.view.viewportUpdateMode(),
        )
        self.assertFalse(
            self.canvas.view.optimizationFlags()
            & self.canvas.view.OptimizationFlag.DontSavePainterState
        )

    def test_start_and_end_nodes_remain_visible_when_hovered(self) -> None:
        self.canvas.render_entries(())
        self.canvas.view.fit_workflow()
        QApplication.processEvents()
        endpoints = [item for item in self.canvas.scene.items() if isinstance(item, StartEndItem)]

        self.assertEqual(2, len(endpoints))
        endpoint_identities = {id(endpoint) for endpoint in endpoints}
        gc.collect()
        self.assertEqual(
            endpoint_identities,
            {
                id(endpoint)
                for endpoint in self.canvas._endpoint_items  # noqa: SLF001
            },
        )
        background = self.canvas.scene.backgroundBrush().color()
        for endpoint in endpoints:
            position = self.canvas.view.mapFromScene(endpoint.sceneBoundingRect().center())
            QTest.mouseMove(self.canvas.view.viewport(), position)
            QApplication.processEvents()
            tooltip_event = QHelpEvent(
                QEvent.Type.ToolTip,
                position,
                self.canvas.view.viewport().mapToGlobal(position),
            )
            QApplication.sendEvent(self.canvas.view.viewport(), tooltip_event)
            QTest.qWait(50)
            QApplication.processEvents()
            rendered = self.canvas.view.viewport().grab().toImage()
            self.assertTrue(endpoint.isVisible())
            self.assertNotEqual(background, rendered.pixelColor(position))

    def test_insert_move_and_undo_redo_publish_canonical_entries(self) -> None:
        changes = []
        self.canvas.sequence_changed.connect(lambda: changes.append("changed"))
        first = _item("first")
        second = _item("second")
        self.canvas.render_entries((first, second))
        self.canvas.set_current_entry_row(1)

        self.assertTrue(self.canvas.move_selected(-1))
        self.assertEqual(
            ("second", "first"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )

        self.canvas.undo()
        self.assertEqual(
            ("first", "second"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.canvas.redo()
        self.assertEqual(
            ("second", "first"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.assertEqual(3, len(changes))

    def test_visual_insertion_target_forwards_the_requested_index(self) -> None:
        self.canvas.render_entries((_item("first"),))
        requested: list[int] = []
        self.canvas.insert_action_requested.connect(requested.append)
        insertions = sorted(
            (item for item in self.canvas.scene.items() if isinstance(item, InsertionItem)),
            key=lambda item: item.scenePos().y(),
        )

        for insertion in insertions:
            view_position = self.canvas.view.mapFromScene(insertion.sceneBoundingRect().center())
            QTest.mouseClick(
                self.canvas.view.viewport(),
                Qt.MouseButton.LeftButton,
                pos=view_position,
            )

        self.assertEqual([0, 1], requested)

    def test_insertion_target_animates_and_labels_mouse_hover(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"),))
        self.canvas.show()
        QApplication.processEvents()
        insertion = self.canvas._insertion_items[0]  # noqa: SLF001
        target = self.canvas.view.mapFromScene(
            insertion.scenePos() + QPointF(22.0, 22.0)
        )

        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QTest.qWait(INSERT_HOVER_TRANSITION_MS + 30)

        self.assertTrue(insertion.is_hovered)
        self.assertGreater(insertion.hover_phase, 0.9)
        self.assertEqual("在此处插入动作", insertion.toolTip())

        QTest.mouseMove(self.canvas.view.viewport(), QPoint(2, 2), delay=20)
        QTest.qWait(INSERT_HOVER_TRANSITION_MS + 30)
        self.assertFalse(insertion.is_hovered)
        self.assertLess(insertion.hover_phase, 0.1)

    def test_loop_container_supports_count_edit_and_unwrap(self) -> None:
        self.canvas.render_entries((_item("first"), _item("second")))
        self.canvas.set_selected_entry_rows((0, 1))

        total_steps = self.canvas.wrap_selected_in_loop(3)

        self.assertEqual(6, total_steps)
        loop = self.canvas.current_loop_block()
        self.assertIsNotNone(loop)
        assert loop is not None
        self.assertEqual(3, loop.repeat_count)
        self.assertTrue(self.canvas.update_current_loop_count(4))
        self.assertEqual(4, self.canvas.current_loop_block().repeat_count)
        self.assertTrue(self.canvas.unwrap_selected_loop())
        self.assertEqual(
            ("first", "second"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )

    def test_subworkflow_and_parallel_nodes_can_be_wrapped_in_a_loop(self) -> None:
        task = SubworkflowBlock(
            uuid="saved-task",
            name="已保存任务",
            items=[_item("task-action")],
        )
        parallel = ParallelBlock(
            uuid="parallel",
            branches=[ParallelBranch("branch", [_item("parallel-action")])],
        )
        self.canvas.render_entries((task, parallel))
        self.canvas.set_selected_entry_rows((0, 1))

        total_steps = self.canvas.wrap_selected_in_loop(3)

        self.assertEqual(6, total_steps)
        entries = self.canvas.get_entries()
        self.assertEqual(1, len(entries))
        self.assertIsInstance(entries[0], LoopBlock)
        assert isinstance(entries[0], LoopBlock)
        self.assertIsInstance(entries[0].items[0], SubworkflowBlock)
        self.assertIsInstance(entries[0].items[1], ParallelBlock)

    def test_loop_is_expanded_and_supports_precise_child_insertion(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("first"), _item("second")],
            repeat_count=2,
        )
        self.canvas.render_entries((loop,))
        node = next(
            item for item in self.canvas.scene.items() if isinstance(item, WorkflowNodeItem)
        )

        self.assertGreater(node.node_width, 300)
        self.assertGreater(node.node_height, 200)
        self.assertLessEqual(NODE_HEIGHT, 60.0)
        self.assertGreaterEqual((LOOP_NODE_WIDTH - NODE_WIDTH) / 2.0, 80.0)
        self.assertEqual(
            0,
            node._loop_insertion_index_at(  # noqa: SLF001
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP / 2.0,
                ),
            ),
        )
        self.assertEqual(
            2,
            node._loop_insertion_index_at(  # noqa: SLF001
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT
                    + LOOP_SECTION_GAP
                    + 2 * LOOP_CHILD_HEIGHT
                    + LOOP_CHILD_GAP
                    + LOOP_SECTION_GAP / 2.0,
                ),
            ),
        )
        requested: list[tuple[str, int]] = []
        self.canvas.insert_loop_action_requested.connect(
            lambda loop_uuid, index: requested.append((loop_uuid, index))
        )
        first_insert_position = self.canvas.view.mapFromScene(
            node.mapToScene(
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP / 2.0,
                )
            )
        )
        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=first_insert_position,
        )
        self.assertEqual([("loop", 0)], requested)
        self.assertTrue(
            self.canvas.insert_action_into_loop(
                "loop",
                1,
                _action("inserted"),
            )
        )
        rendered = self.canvas.get_entries()[0]
        assert isinstance(rendered, LoopBlock)
        self.assertEqual(
            ["first", "inserted", "second"],
            [item.definition.id for item in rendered.items],
        )
        self.canvas.undo()
        restored = self.canvas.get_entries()[0]
        assert isinstance(restored, LoopBlock)
        self.assertEqual(["first", "second"], [item.uuid for item in restored.items])

    def test_loop_insertion_marker_tooltip_takes_priority_over_loop_details(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("inside")],
            repeat_count=2,
        )
        self.canvas.render_entries((loop,))
        self.canvas.show()
        QApplication.processEvents()
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        marker_position = QPointF(
            LOOP_NODE_WIDTH / 2.0,
            LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP / 2.0,
        )

        self.assertEqual("在此处插入动作", node.tooltip_at(marker_position))
        viewport_position = self.canvas.view.mapFromScene(
            node.mapToScene(marker_position)
        )
        tooltip_service = install_tooltip_service(self.application)
        tooltip_event = QHelpEvent(
            QEvent.Type.ToolTip,
            viewport_position,
            self.canvas.view.viewport().mapToGlobal(viewport_position),
        )
        QApplication.sendEvent(self.canvas.view.viewport(), tooltip_event)
        QApplication.processEvents()
        self.assertEqual("在此处插入动作", tooltip_service.bubble.text)

    def test_loop_child_can_be_selected_and_double_clicked_for_editing(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("child")],
            repeat_count=2,
        )
        self.canvas.resize(620, 700)
        self.canvas.render_entries((loop,))
        self.canvas.show()
        QApplication.processEvents()
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        child_position = self.canvas.view.mapFromScene(
            node.mapToScene(
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
                )
            )
        )
        edit_requests: list[str] = []
        self.canvas.edit_requested.connect(lambda: edit_requests.append("edit"))
        node.setSelected(True)
        self.canvas._current_item_uuid = "loop"  # noqa: SLF001

        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=child_position,
        )
        current = self.canvas.current_sequence_item()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual("child", current.uuid)
        self.assertEqual("child", node._active_child_uuid)  # noqa: SLF001

        QTest.mouseDClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=child_position,
        )
        self.assertEqual(["edit"], edit_requests)

    def test_delete_targets_selected_loop_child_before_loop_container(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("first-child"), _item("second-child")],
            repeat_count=2,
        )
        self.canvas.resize(620, 700)
        self.canvas.render_entries((loop,))
        self.canvas.show()
        QApplication.processEvents()
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        first_child_position = self.canvas.view.mapFromScene(
            node.mapToScene(
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT
                    + LOOP_SECTION_GAP
                    + LOOP_CHILD_HEIGHT / 2.0,
                )
            )
        )

        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=first_child_position,
        )
        self.assertEqual("first-child", self.canvas.current_entry().uuid)

        self.assertTrue(self.canvas.delete_selected())
        entries = self.canvas.get_entries()
        self.assertEqual(1, len(entries))
        self.assertIsInstance(entries[0], LoopBlock)
        assert isinstance(entries[0], LoopBlock)
        self.assertEqual(["second-child"], [item.uuid for item in entries[0].items])

        self.canvas.undo()
        restored = self.canvas.get_entries()
        assert isinstance(restored[0], LoopBlock)
        self.assertEqual(
            ["first-child", "second-child"],
            [item.uuid for item in restored[0].items],
        )
        self.canvas.redo()
        redone = self.canvas.get_entries()
        assert isinstance(redone[0], LoopBlock)
        self.assertEqual(["second-child"], [item.uuid for item in redone[0].items])

        self.canvas.set_current_entry_row(0)
        self.assertTrue(self.canvas.delete_selected())
        self.assertEqual([], self.canvas.get_entries())

    def test_loop_child_uses_its_own_tooltip_and_drag_thumbnail(self) -> None:
        child = _item("child")
        child.definition.parameters["target"] = "sample"
        loop = LoopBlock(uuid="loop", items=[child], repeat_count=2)
        self.canvas.resize(620, 700)
        self.canvas.render_entries((loop,))
        self.canvas.show()
        QApplication.processEvents()
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        child_position = self.canvas.view.mapFromScene(
            node.mapToScene(
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
                )
            )
        )

        child_local_position = QPointF(
            node.node_width / 2.0,
            LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
        )
        self.assertIn("child", node.tooltip_at(child_local_position))
        self.assertNotIn("循环 2 次", node.tooltip_at(child_local_position))
        tooltip_service = install_tooltip_service(self.application)
        tooltip_event = QHelpEvent(
            QEvent.Type.ToolTip,
            child_position,
            self.canvas.view.viewport().mapToGlobal(child_position),
        )
        QApplication.sendEvent(self.canvas.view.viewport(), tooltip_event)
        QApplication.processEvents()
        self.assertIn("child", tooltip_service.bubble.text)

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=child_position,
        )
        QTest.mouseMove(
            self.canvas.view.viewport(),
            child_position + QPoint(0, NODE_DRAG_THRESHOLD + 4),
            delay=20,
        )
        QApplication.processEvents()
        preview = node.drag_preview
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertEqual("child", preview.node_id)
        self.assertEqual(
            (
                round(NODE_WIDTH * DRAG_CARD_RELATIVE_SCALE),
                round(NODE_HEIGHT * DRAG_CARD_RELATIVE_SCALE),
            ),
            (preview.pixmap().width(), preview.pixmap().height()),
        )
        self.assertEqual(DRAG_CARD_OPACITY, preview.opacity())
        self.assertEqual(1.0, node.opacity())
        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=child_position + QPoint(0, NODE_DRAG_THRESHOLD + 4),
        )

    def test_loop_container_drag_keeps_full_thumbnail_and_reorders(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("inside-first"), _item("inside-second")],
            repeat_count=2,
        )
        self.canvas.resize(620, 900)
        self.canvas.render_entries((_item("before"), loop, _item("after")))
        self.canvas.show()
        QApplication.processEvents()
        loop_node = self.canvas._node_items["loop"]  # noqa: SLF001
        header_position = self.canvas.view.mapFromScene(
            loop_node.mapToScene(QPointF(loop_node.node_width / 2.0, 20.0))
        )
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        target = self.canvas.view.mapFromScene(
            insertion.scenePos() + QPointF(22.0, 22.0)
        )

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=header_position,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QApplication.processEvents()
        preview = loop_node.drag_preview
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertEqual("loop", preview.node_id)
        self.assertGreater(preview.pixmap().height(), 100)
        self.assertLess(loop_node.opacity(), 0.5)

        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )
        QApplication.processEvents()
        self.assertEqual(
            ("before", "after", "loop"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )

    def test_loop_container_cannot_be_dropped_into_its_own_body(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("inside-first"), _item("inside-second")],
            repeat_count=2,
        )
        self.canvas.render_entries((_item("before"), loop, _item("after")))
        loop_node = self.canvas._node_items["loop"]  # noqa: SLF001
        target = loop_node.mapToScene(
            QPointF(
                loop_node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
            )
        )

        self.canvas._on_node_drag_position("loop", target.x(), target.y())  # noqa: SLF001
        self.assertIsNone(self.canvas._active_loop_drop)  # noqa: SLF001
        self.canvas._on_node_drag_ended("loop", True)  # noqa: SLF001
        self.canvas._on_node_moved("loop", target.y())  # noqa: SLF001

        entries = self.canvas.get_entries()
        self.assertEqual(("before", "loop", "after"), tuple(item.uuid for item in entries))
        assert isinstance(entries[1], LoopBlock)
        self.assertEqual(
            ("inside-first", "inside-second"),
            tuple(item.uuid for item in entries[1].items),
        )

    def test_container_cannot_be_moved_into_its_descendant_loop(self) -> None:
        nested_loop = LoopBlock(
            uuid="nested-loop",
            items=[_item("inside")],
            repeat_count=2,
        )
        task = SubworkflowBlock(
            uuid="task",
            name="包含循环的任务",
            items=[nested_loop],
        )
        self.canvas.render_entries((task, _item("after")))

        moved = self.canvas._move_root_item_to_loop(  # noqa: SLF001
            "task",
            "nested-loop",
            1,
        )

        self.assertFalse(moved)
        entries = self.canvas.get_entries()
        self.assertEqual(("task", "after"), tuple(item.uuid for item in entries))
        assert isinstance(entries[0], SubworkflowBlock)
        self.assertEqual(("nested-loop",), tuple(item.uuid for item in entries[0].items))

    def test_double_clicking_subworkflow_inside_loop_enters_its_scope(self) -> None:
        subworkflow = SubworkflowBlock(
            uuid="nested-task",
            name="嵌套任务",
            items=[_item("nested-action")],
        )
        loop = LoopBlock(
            uuid="loop",
            items=[subworkflow],
            repeat_count=2,
        )
        self.canvas.resize(620, 700)
        self.canvas.render_entries((loop,))
        self.canvas.show()
        QApplication.processEvents()
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        node.setSelected(True)
        self.canvas._current_item_uuid = "loop"  # noqa: SLF001
        subworkflow_position = self.canvas.view.mapFromScene(
            node.mapToScene(
                QPointF(
                    node.node_width / 2.0,
                    LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
                )
            )
        )

        QTest.mouseDClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=subworkflow_position,
        )

        self.assertEqual(1, self.canvas.entry_count())
        self.assertEqual("nested-action", self.canvas._entries[0].uuid)  # noqa: SLF001
        self.canvas.leave_scope()
        self.assertIsInstance(self.canvas.get_entries()[0], LoopBlock)
        self.assertEqual(1, self.canvas.entry_count())
        self.assertIn("loop", self.canvas._node_items)  # noqa: SLF001
        self.assertFalse(self.canvas.root_scope_button.isVisible())

    def test_dragging_actions_across_loop_boundary_preserves_entries(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("inside")],
            repeat_count=2,
        )
        self.canvas.resize(620, 800)
        self.canvas.render_entries((_item("outside"), loop))
        self.canvas.show()
        QApplication.processEvents()
        loop_node = self.canvas._node_items["loop"]  # noqa: SLF001
        child_center = loop_node.mapToScene(
            QPointF(
                loop_node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
            )
        )
        root_node = self.canvas._node_items["outside"]  # noqa: SLF001

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.canvas.view.mapFromScene(root_node.sceneBoundingRect().center()),
        )
        QTest.mouseMove(self.canvas.view.viewport(), self.canvas.view.mapFromScene(child_center), delay=20)
        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.canvas.view.mapFromScene(child_center),
        )
        QApplication.processEvents()

        moved_into_loop = self.canvas.get_entries()
        self.assertEqual(1, len(moved_into_loop))
        assert isinstance(moved_into_loop[0], LoopBlock)
        self.assertEqual(["inside", "outside"], [item.uuid for item in moved_into_loop[0].items])

        loop_node = self.canvas._node_items["loop"]  # noqa: SLF001
        child_center = loop_node.mapToScene(
            QPointF(
                loop_node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
            )
        )
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        root_target = insertion.scenePos() + QPointF(22.0, 22.0)
        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.canvas.view.mapFromScene(child_center),
        )
        QTest.mouseMove(self.canvas.view.viewport(), self.canvas.view.mapFromScene(root_target), delay=20)
        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.canvas.view.mapFromScene(root_target),
        )
        QApplication.processEvents()

        moved_out_of_loop = self.canvas.get_entries()
        assert isinstance(moved_out_of_loop[0], LoopBlock)
        self.assertEqual(["outside"], [item.uuid for item in moved_out_of_loop[0].items])
        self.assertEqual("inside", moved_out_of_loop[1].uuid)

    def test_dragging_root_subworkflow_into_loop_preserves_the_subworkflow(self) -> None:
        loop = LoopBlock(uuid="loop", items=[_item("inside")], repeat_count=2)
        task = SubworkflowBlock(
            uuid="task",
            name="可复用任务",
            items=[_item("task-action")],
        )
        self.canvas.render_entries((loop, task))
        loop_node = self.canvas._node_items["loop"]  # noqa: SLF001
        target = loop_node.mapToScene(
            QPointF(
                loop_node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + LOOP_CHILD_HEIGHT / 2.0,
            )
        )

        self.canvas._on_node_drag_position("task", target.x(), target.y())  # noqa: SLF001
        self.assertEqual(("loop", 1), self.canvas._active_loop_drop)  # noqa: SLF001
        self.canvas._on_node_drag_ended("task", True)  # noqa: SLF001
        self.canvas._on_node_moved("task", target.y())  # noqa: SLF001

        entries = self.canvas.get_entries()
        self.assertEqual(1, len(entries))
        self.assertIsInstance(entries[0], LoopBlock)
        assert isinstance(entries[0], LoopBlock)
        self.assertEqual(["inside", "task"], [entry.uuid for entry in entries[0].items])

    def test_compiled_event_mapping_updates_action_and_loop_progress(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("child")],
            repeat_count=2,
        )
        plain = _item("plain")
        document = WorkflowDocument.from_entries(
            workflow_id="workflow",
            name="Workflow",
            revision=1,
            entries=(loop, plain),
        )
        compiled = WorkflowCompiler().compile(document)
        self.canvas.begin_execution(compiled)
        running_child = _item("child")
        running_child.status = SequenceItemStatus.RUNNING

        self.canvas.update_execution_step(0, running_child)
        self.canvas.update_loop_progress("loop", 1)

        rendered_loop = self.canvas.get_entries()[0]
        self.assertIsInstance(rendered_loop, LoopBlock)
        assert isinstance(rendered_loop, LoopBlock)
        self.assertIs(SequenceItemStatus.RUNNING, rendered_loop.items[0].status)
        self.assertEqual(1, rendered_loop.current_iteration)
        self.assertTrue(
            self.canvas._node_items["loop"].execution_pulse_active  # noqa: SLF001
        )
        before = tuple(entry.uuid for entry in self.canvas.get_entries())
        self.canvas.insert_action(_action("ignored-during-run"))
        self.assertEqual(before, tuple(entry.uuid for entry in self.canvas.get_entries()))
        self.canvas.finish_execution()

    def test_parallel_wrap_round_trip_and_undo_redo(self) -> None:
        self.canvas.render_entries((_item("first"), _item("second")))
        self.canvas.set_selected_entry_rows((0, 1))

        parallel = self.canvas.wrap_selected_in_parallel()

        self.assertEqual(2, len(parallel.branches))
        self.assertEqual(
            [["first"], ["second"]],
            [[item.uuid for item in branch.items] for branch in parallel.branches],
        )
        document = self.canvas.document(
            workflow_id="parallel-workflow",
            name="Parallel",
            revision=1,
        )
        restored = WorkflowDocument.from_dict(document.to_dict()).to_entries()[0]
        self.assertIsInstance(restored, ParallelBlock)
        self.canvas.undo()
        self.assertEqual(
            ("first", "second"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.canvas.redo()
        self.assertIsInstance(self.canvas.get_entries()[0], ParallelBlock)

    def test_parallel_wrap_enforces_runtime_branch_limit(self) -> None:
        self.canvas.render_entries(tuple(_item(f"branch-{index}") for index in range(9)))
        self.canvas.set_selected_entry_rows(tuple(range(9)))

        with self.assertRaisesRegex(ValueError, "最多支持 8 个分支"):
            self.canvas.wrap_selected_in_parallel()

        self.assertEqual(9, self.canvas.entry_count())

    def test_parallel_branch_insertion_and_library_drop_use_branch_coordinates(self) -> None:
        parallel = _parallel()
        self.canvas.render_entries((parallel,))
        node = self.canvas._node_items[parallel.uuid]  # noqa: SLF001
        requested: list[tuple[str, str, int]] = []
        self.canvas.insert_parallel_action_requested.connect(
            lambda parallel_id, branch_id, index: requested.append((parallel_id, branch_id, index))
        )
        marker = QPointF(
            PARALLEL_BRANCH_PADDING + PARALLEL_BRANCH_WIDTH / 2.0,
            PARALLEL_HEADER_HEIGHT
            + PARALLEL_SECTION_GAP
            + PARALLEL_BRANCH_HEADER_HEIGHT
            - PARALLEL_CHILD_GAP / 2.0,
        )
        position = self.canvas.view.mapFromScene(node.mapToScene(marker))

        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=position,
        )

        self.assertEqual(
            [(parallel.uuid, parallel.branches[0].branch_id, 0)],
            requested,
        )
        second_branch_point = node.mapToScene(
            QPointF(
                node.node_width - PARALLEL_BRANCH_PADDING - PARALLEL_BRANCH_WIDTH / 2.0,
                marker.y() + 70.0,
            )
        )
        self.canvas._on_action_dropped(  # noqa: SLF001
            _action("dropped"),
            second_branch_point.x(),
            second_branch_point.y(),
        )
        rendered = self.canvas.get_entries()[0]
        assert isinstance(rendered, ParallelBlock)
        self.assertEqual(
            ["right", "dropped"],
            [item.definition.id for item in rendered.branches[1].items],
        )

    def test_parallel_branch_commands_preserve_invariants_and_are_undoable(self) -> None:
        parallel = ParallelBlock(
            uuid="parallel",
            branches=[
                ParallelBranch("left", [_item("left-a"), _item("left-b")]),
                ParallelBranch("middle", [_item("middle")]),
                ParallelBranch("right", [_item("right")]),
            ],
        )
        self.canvas.render_entries((parallel,))
        self.canvas.set_current_entry_row(0)
        self.canvas._current_item_uuid = "left-b"  # noqa: SLF001

        self.assertTrue(self.canvas.move_current_parallel_item(1))
        moved = self.canvas.get_entries()[0]
        assert isinstance(moved, ParallelBlock)
        self.assertEqual(["left-a"], [item.uuid for item in moved.branches[0].items])
        self.assertEqual(
            ["middle", "left-b"],
            [item.uuid for item in moved.branches[1].items],
        )
        self.assertTrue(self.canvas.move_current_parallel_branch(1))
        reordered = self.canvas.get_entries()[0]
        assert isinstance(reordered, ParallelBlock)
        self.assertEqual(
            ("left", "right", "middle"),
            tuple(branch.branch_id for branch in reordered.branches),
        )
        self.assertTrue(self.canvas.remove_current_parallel_branch())
        reduced = self.canvas.get_entries()[0]
        assert isinstance(reduced, ParallelBlock)
        self.assertEqual(2, len(reduced.branches))
        self.canvas.undo()
        self.assertEqual(3, len(self.canvas.get_entries()[0].branches))

    def test_parallel_execution_updates_branch_and_nested_action_state(self) -> None:
        parallel = _parallel()
        compiled = WorkflowCompiler().compile(
            WorkflowDocument.from_entries(
                workflow_id="parallel-workflow",
                name="Parallel",
                revision=1,
                entries=(parallel,),
            )
        )
        self.canvas.begin_execution(compiled)
        running = _item("left")
        running.status = SequenceItemStatus.RUNNING

        self.canvas.update_parallel_branch_state("parallel", "left-branch", "started")
        self.canvas.update_execution_step(0, running)

        rendered = self.canvas.get_entries()[0]
        assert isinstance(rendered, ParallelBlock)
        self.assertIs(
            SequenceItemStatus.RUNNING,
            rendered.branches[0].items[0].status,
        )
        self.assertEqual(
            "started",
            self.canvas._parallel_branch_states[("parallel", "left-branch")],  # noqa: SLF001
        )
        self.assertFalse(self.canvas._editing_enabled)  # noqa: SLF001
        self.canvas.finish_execution()
        self.assertFalse(self.canvas._parallel_branch_states)  # noqa: SLF001

    def test_parallel_node_renders_in_light_dark_and_narrow_viewports(self) -> None:
        original = QApplication.palette()
        try:
            for window, base, text in (
                ("#f3f4f6", "#ffffff", "#111827"),
                ("#111827", "#1f2937", "#f9fafb"),
            ):
                palette = QPalette()
                palette.setColor(QPalette.ColorRole.Window, QColor(window))
                palette.setColor(QPalette.ColorRole.Base, QColor(base))
                palette.setColor(QPalette.ColorRole.Text, QColor(text))
                palette.setColor(QPalette.ColorRole.Mid, QColor("#64748b"))
                palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
                QApplication.setPalette(palette)
                self.canvas.resize(360, 640)
                self.canvas.render_entries((_parallel(),))
                QApplication.processEvents()
                node = self.canvas._node_items["parallel"]  # noqa: SLF001
                self.assertGreater(node.node_width, 400.0)
                self.assertGreater(node.node_height, 200.0)
                self.assertTrue(node.isVisible())
                self.assertGreater(self.canvas.scene.itemsBoundingRect().width(), 0)
        finally:
            QApplication.setPalette(original)
            QApplication.processEvents()

    def test_existing_cached_nodes_repaint_immediately_after_palette_change(self) -> None:
        original = QApplication.palette()
        try:
            light = _palette("#f1f5f9", "#ffffff", "#1e293b")
            dark = _palette("#0f172a", "#111827", "#f1f5f9")
            QApplication.setPalette(light)
            self.canvas.resize(900, 700)
            self.canvas.render_entries((_item("theme-refresh"),))
            self.canvas.show()
            QApplication.processEvents()

            node = self.canvas._node_items["theme-refresh"]  # noqa: SLF001
            sample_point = self.canvas.view.mapFromScene(
                node.scenePos() + QPointF(node.node_width - 18.0, 16.0)
            )
            before = self.canvas.view.viewport().grab().toImage().pixelColor(sample_point)

            QApplication.setPalette(dark)
            QApplication.processEvents()
            after = self.canvas.view.viewport().grab().toImage().pixelColor(sample_point)

            self.assertEqual(QColor("#ffffff"), before)
            self.assertEqual(QColor("#111827"), after)
        finally:
            QApplication.setPalette(original)
            self.canvas.close()
            QApplication.processEvents()

    def test_canvas_uses_lightweight_items_and_versioned_document(self) -> None:
        self.canvas.render_entries((_item("first"),))

        document = self.canvas.document(
            workflow_id="workflow",
            name="Workflow",
            revision=2,
        )

        self.assertEqual(
            ("first",),
            tuple(node.node_id for node in document.root.children),
        )
        self.assertFalse(
            any(isinstance(item, QGraphicsProxyWidget) for item in self.canvas.scene.items())
        )

    def test_canvas_follows_light_and_dark_system_palettes(self) -> None:
        original = QApplication.palette()
        try:
            for window, base, text in (
                ("#f3f4f6", "#ffffff", "#111827"),
                ("#111827", "#1f2937", "#f9fafb"),
            ):
                palette = QPalette()
                palette.setColor(QPalette.ColorRole.Window, QColor(window))
                palette.setColor(QPalette.ColorRole.Base, QColor(base))
                palette.setColor(QPalette.ColorRole.Text, QColor(text))
                palette.setColor(
                    QPalette.ColorRole.PlaceholderText,
                    QColor("#6b7280"),
                )
                palette.setColor(QPalette.ColorRole.Mid, QColor("#64748b"))
                palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
                QApplication.setPalette(palette)
                QApplication.processEvents()
                self.canvas.render_entries((_item("palette-node"),))

                self.assertEqual(
                    QColor(window),
                    self.canvas.scene.backgroundBrush().color(),
                )
        finally:
            QApplication.setPalette(original)
            QApplication.processEvents()

    def test_control_flow_nodes_have_distinct_theme_aware_visual_semantics(self) -> None:
        light = _palette("#f3f4f6", "#ffffff", "#111827")
        dark = _palette("#111827", "#1f2937", "#f9fafb")

        light_loop = control_flow_colors(ControlFlowKind.LOOP, light)
        dark_loop = control_flow_colors(ControlFlowKind.LOOP, dark)
        light_parallel = control_flow_colors(ControlFlowKind.PARALLEL, light)

        self.assertNotEqual(light_loop.header, dark_loop.header)
        self.assertNotEqual(light_loop.accent, light_parallel.accent)
        self.assertGreater(light_loop.header.lightnessF(), 0.75)
        self.assertLess(dark_loop.header.lightnessF(), 0.35)

    def test_offscreen_size_matrix_preserves_accessible_canvas_controls(self) -> None:
        self.canvas.render_entries(tuple(_item(f"node-{index}") for index in range(20)))

        for width, height in ((360, 640), (720, 1280), (1280, 720)):
            self.canvas.resize(width, height)
            QApplication.processEvents()
            self.assertEqual("任务工作流画布", self.canvas.view.accessibleName())
            self.assertTrue(self.canvas.view.accessibleDescription())
            self.assertGreater(self.canvas.scene.itemsBoundingRect().height(), 0)

    def test_ctrl_left_drag_pans_while_plain_left_drag_does_not(self) -> None:
        self.canvas.render_entries(tuple(_item(f"node-{index}") for index in range(20)))
        self.canvas.resize(420, 360)
        QApplication.processEvents()
        scrollbar = self.canvas.view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 2)
        initial_value = scrollbar.value()
        viewport = self.canvas.view.viewport()
        start = next(
            QPoint(x, y)
            for x in range(8, max(9, viewport.width()), 12)
            for y in range(8, max(9, viewport.height()), 12)
            if self.canvas.view.itemAt(QPoint(x, y)) is None
        )

        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, start + QPoint(0, 48), delay=20)
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.LeftButton,
            pos=start + QPoint(0, 48),
        )

        self.assertEqual(initial_value, scrollbar.value())

        QTest.mousePress(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start,
        )
        QTest.mouseMove(viewport, start + QPoint(0, 48), delay=20)
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            start + QPoint(0, 48),
        )

        self.assertLess(scrollbar.value(), initial_value)

    def test_left_click_selects_and_shift_left_click_adds_selection(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second"), _item("third")))
        QApplication.processEvents()
        nodes = [
            self.canvas._node_items[node_id]  # noqa: SLF001
            for node_id in ("first", "second", "third")
        ]
        positions = [
            self.canvas.view.mapFromScene(node.sceneBoundingRect().center()) for node in nodes
        ]
        initial_item_ids = [id(node) for node in nodes]
        initial_positions = [node.scenePos() for node in nodes]
        initial_scene_rect = self.canvas.scene.sceneRect()
        initial_scroll = self.canvas.view.verticalScrollBar().value()

        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=positions[0],
        )
        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            positions[1],
        )

        self.assertEqual([0, 1], self.canvas.selected_entry_rows())
        self.assertEqual(
            initial_item_ids,
            [
                id(self.canvas._node_items[node_id])
                for node_id in (  # noqa: SLF001
                    "first",
                    "second",
                    "third",
                )
            ],
        )
        self.assertEqual(
            initial_positions,
            [
                self.canvas._node_items[node_id].scenePos()  # noqa: SLF001
                for node_id in ("first", "second", "third")
            ],
        )
        self.assertEqual(initial_scene_rect, self.canvas.scene.sceneRect())
        self.assertEqual(initial_scroll, self.canvas.view.verticalScrollBar().value())

        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=positions[1],
        )
        self.assertEqual([1], self.canvas.selected_entry_rows())
        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            positions[0],
        )
        self.assertEqual([0, 1], self.canvas.selected_entry_rows())
        QTest.mouseClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=positions[0],
        )
        self.assertEqual([0], self.canvas.selected_entry_rows())
        self.assertEqual(initial_scene_rect, self.canvas.scene.sceneRect())
        self.assertEqual(initial_scroll, self.canvas.view.verticalScrollBar().value())

    def test_double_left_click_requests_node_editing(self) -> None:
        self.canvas.resize(620, 500)
        self.canvas.render_entries((_item("first"),))
        QApplication.processEvents()
        edit_requests: list[str] = []
        self.canvas.edit_requested.connect(lambda: edit_requests.append("edit"))
        node = self.canvas._node_items["first"]  # noqa: SLF001
        position = self.canvas.view.mapFromScene(node.sceneBoundingRect().center())

        QTest.mouseDClick(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=position,
        )

        self.assertEqual(["edit"], edit_requests)

    def test_left_drag_reorders_node_and_undo_restores_order(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second"), _item("third")))
        QApplication.processEvents()
        first = self.canvas._node_items["first"]  # noqa: SLF001
        third = self.canvas._node_items["third"]  # noqa: SLF001
        start = self.canvas.view.mapFromScene(first.sceneBoundingRect().center())
        target = self.canvas.view.mapFromScene(
            third.sceneBoundingRect().center() + QPointF(0.0, 60.0)
        )

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )
        QApplication.processEvents()

        self.assertEqual(
            ("second", "third", "first"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.assertEqual([2], self.canvas.selected_entry_rows())
        self.canvas.undo()
        self.assertEqual(
            ("first", "second", "third"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )

    def test_node_drag_uses_preview_and_pulsing_insertion_target(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second"), _item("third")))
        QApplication.processEvents()
        first = self.canvas._node_items["first"]  # noqa: SLF001
        original_position = QPointF(first.scenePos())
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        insertion_center = insertion.scenePos() + QPointF(22.0, 22.0)
        start = self.canvas.view.mapFromScene(first.sceneBoundingRect().center())
        target = self.canvas.view.mapFromScene(insertion_center)

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QApplication.processEvents()

        preview = first.drag_preview
        self.assertIsInstance(preview, NodeDragPreviewItem)
        assert preview is not None
        self.assertEqual(
            (
                round(NODE_WIDTH * DRAG_CARD_RELATIVE_SCALE),
                round(NODE_HEIGHT * DRAG_CARD_RELATIVE_SCALE),
            ),
            (preview.pixmap().width(), preview.pixmap().height()),
        )
        self.assertEqual(DRAG_CARD_OPACITY, preview.opacity())
        self.assertIs(self.canvas.scene, preview.scene())
        self.assertEqual(original_position, first.scenePos())
        self.assertLess(first.opacity(), 0.5)
        self.assertAlmostEqual(
            insertion_center.y(),
            preview.sceneBoundingRect().center().y(),
            delta=2.0,
        )
        self.assertTrue(insertion.is_drop_target_active)
        self.assertTrue(insertion.is_pulsing)
        self.assertEqual("移动到第 3 位", insertion.target_label)
        self.assertEqual(
            1,
            sum(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            ),
        )

        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )
        QApplication.processEvents()

        self.assertEqual(
            ("second", "third", "first"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )

    def test_node_drag_cancel_restores_stationary_node_and_clears_target(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second")))
        QApplication.processEvents()
        first = self.canvas._node_items["first"]  # noqa: SLF001
        original_position = QPointF(first.scenePos())
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        start = self.canvas.view.mapFromScene(first.sceneBoundingRect().center())
        target = self.canvas.view.mapFromScene(insertion.scenePos() + QPointF(22.0, 22.0))

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QApplication.processEvents()
        self.assertTrue(first.is_dragging)
        self.assertIsNotNone(first.drag_preview)

        QApplication.sendEvent(
            self.canvas.view,
            QEvent(QEvent.Type.WindowDeactivate),
        )
        QApplication.processEvents()

        self.assertFalse(first.is_dragging)
        self.assertIsNone(first.drag_preview)
        self.assertEqual(original_position, first.scenePos())
        self.assertEqual(1.0, first.opacity())
        self.assertEqual(
            ("first", "second"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )
        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )
        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )

    def test_node_drag_only_activates_an_insertion_target_when_pointer_is_nearby(
        self,
    ) -> None:
        self.canvas.render_entries((_item("first"), _item("second")))
        first = self.canvas._node_items["first"]  # noqa: SLF001
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        center = insertion.scenePos() + QPointF(22.0, 22.0)

        first.drag_position_changed.emit(
            first.node_id,
            center.x() + 10_000.0,
            center.y(),
        )

        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )
        first.drag_ended.emit(first.node_id, False)

    def test_node_drag_release_away_from_insertions_keeps_original_order(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second"), _item("third")))
        QApplication.processEvents()
        first = self.canvas._node_items["first"]  # noqa: SLF001
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        start = self.canvas.view.mapFromScene(first.sceneBoundingRect().center())
        insertion_center = insertion.scenePos() + QPointF(22.0, 22.0)
        target = self.canvas.view.mapFromScene(
            QPointF(insertion_center.x() - 200.0, insertion_center.y())
        )

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QApplication.processEvents()
        self.assertTrue(first.is_dragging)
        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )

        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )
        QApplication.processEvents()

        self.assertEqual(
            ("first", "second", "third"),
            tuple(entry.uuid for entry in self.canvas.get_entries()),
        )

    def test_node_drag_lost_mouse_grab_clears_all_transient_feedback(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"), _item("second")))
        QApplication.processEvents()
        first = self.canvas._node_items["first"]  # noqa: SLF001
        insertion = self.canvas._insertion_items[-1]  # noqa: SLF001
        start = self.canvas.view.mapFromScene(first.sceneBoundingRect().center())
        target = self.canvas.view.mapFromScene(insertion.scenePos() + QPointF(22.0, 22.0))

        QTest.mousePress(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(self.canvas.view.viewport(), target, delay=20)
        QApplication.processEvents()
        self.assertTrue(first.is_dragging)

        first.ungrabMouse()
        QApplication.processEvents()

        self.assertFalse(first.is_dragging)
        self.assertIsNone(first.drag_preview)
        self.assertEqual(1.0, first.opacity())
        self.assertFalse(insertion.is_drop_target_active)
        self.assertFalse(insertion.is_pulsing)

        QTest.mouseRelease(
            self.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target,
        )

    def test_external_drag_highlights_nearest_target_and_clears_on_leave(self) -> None:
        self.canvas.render_entries((_item("first"), _item("second")))
        insertion = self.canvas._insertion_items[1]  # noqa: SLF001
        scene_position = insertion.scenePos() + QPointF(22.0, 22.0)

        self.canvas.view.external_action_drag_moved.emit(
            scene_position.x(),
            scene_position.y(),
        )

        self.assertTrue(insertion.is_drop_target_active)
        self.assertTrue(insertion.is_pulsing)
        self.assertEqual("插入到第 2 位", insertion.target_label)

        self.canvas.view.external_drag_finished.emit()

        self.assertFalse(insertion.is_drop_target_active)
        self.assertFalse(insertion.is_pulsing)
        self.assertEqual("", insertion.target_label)

        self.canvas.view.external_action_drag_moved.emit(
            scene_position.x(),
            scene_position.y() + 10_000.0,
        )

        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )

        self.canvas.view.external_action_drag_moved.emit(
            scene_position.x() + 10_000.0,
            scene_position.y(),
        )

        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )

    def test_native_action_drag_events_activate_the_nearest_insertion_pulse(self) -> None:
        self.canvas.resize(620, 700)
        self.canvas.render_entries((_item("first"),))
        self.canvas.show()
        QApplication.processEvents()
        insertion = self.canvas._insertion_items[0]  # noqa: SLF001
        target = self.canvas.view.mapFromScene(
            insertion.scenePos() + QPointF(22.0, 22.0)
        )
        mime = QMimeData()
        mime.setData("application/x-action", b"{}")
        enter_event = QDragEnterEvent(
            target,
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move_event = QDragMoveEvent(
            target,
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        self.canvas.view.dragEnterEvent(enter_event)
        self.canvas.view.dragMoveEvent(move_event)

        self.assertTrue(enter_event.isAccepted())
        self.assertTrue(move_event.isAccepted())
        self.assertTrue(insertion.is_drop_target_active)
        self.assertTrue(insertion.is_pulsing)
        self.assertEqual("插入到第 1 位", insertion.target_label)

    def test_external_action_drop_commits_to_the_highlighted_top_level_target(
        self,
    ) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("child")],
            repeat_count=2,
        )
        self.canvas.render_entries((loop,))
        insertion = self.canvas._insertion_items[0]  # noqa: SLF001
        marker = insertion.scenePos() + QPointF(22.0, 22.0)

        self.canvas.view.external_action_drag_moved.emit(marker.x(), marker.y())
        self.assertTrue(insertion.is_drop_target_active)
        self.canvas.view.action_dropped.emit(
            _action("top-level"),
            marker.x(),
            marker.y(),
        )

        entries = self.canvas.get_entries()
        self.assertEqual(2, len(entries))
        self.assertIsInstance(entries[0], SequenceItem)
        assert isinstance(entries[0], SequenceItem)
        self.assertEqual("top-level", entries[0].definition.id)
        self.assertIsInstance(entries[1], LoopBlock)
        assert isinstance(entries[1], LoopBlock)
        self.assertEqual(["child"], [item.uuid for item in entries[1].items])

        self.canvas.render_entries((loop,))
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        deep_inside_loop = node.mapToScene(
            QPointF(
                node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + 10.0,
            )
        )
        self.canvas.view.external_action_drag_moved.emit(
            deep_inside_loop.x(),
            deep_inside_loop.y(),
        )
        self.assertFalse(
            any(
                item.is_drop_target_active
                for item in self.canvas._insertion_items  # noqa: SLF001
            )
        )
        self.assertEqual(("loop", 0), self.canvas._active_loop_drop)  # noqa: SLF001
        self.assertTrue(node.is_loop_drop_pulsing)
        self.canvas.view.action_dropped.emit(
            _action("loop-child"),
            deep_inside_loop.x(),
            deep_inside_loop.y(),
        )
        entries = self.canvas.get_entries()
        self.assertEqual(1, len(entries))
        self.assertIsInstance(entries[0], LoopBlock)
        assert isinstance(entries[0], LoopBlock)
        self.assertEqual(
            ["loop-child", "child"],
            [item.definition.id for item in entries[0].items],
        )

    def test_external_saved_task_pulses_and_inserts_at_loop_target(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("existing-child")],
            repeat_count=2,
        )
        self.canvas.render_entries((loop,))
        node = self.canvas._node_items["loop"]  # noqa: SLF001
        target = node.mapToScene(
            QPointF(
                node.node_width / 2.0,
                LOOP_HEADER_HEIGHT + LOOP_SECTION_GAP + 10.0,
            )
        )
        requests: list[tuple[str, str, int]] = []
        self.canvas.insert_subworkflow_in_loop_requested.connect(
            lambda task_name, loop_uuid, child_index: requests.append(
                (task_name, loop_uuid, child_index)
            )
        )

        self.canvas.view.external_task_drag_moved.emit(target.x(), target.y())

        self.assertEqual(("loop", 0), self.canvas._active_loop_drop)  # noqa: SLF001
        self.assertTrue(node.is_loop_drop_pulsing)

        self.canvas.view.task_dropped.emit("saved-task", target.x(), target.y())

        self.assertEqual([("saved-task", "loop", 0)], requests)
        inserted = SubworkflowBlock(
            uuid="saved-task-instance",
            name="saved-task",
            items=[_item("nested-action")],
        )
        self.assertTrue(self.canvas.insert_subworkflow_into_loop("loop", 0, inserted))
        entries = self.canvas.get_entries()
        self.assertEqual(1, len(entries))
        assert isinstance(entries[0], LoopBlock)
        self.assertEqual(
            ("saved-task-instance", "existing-child"),
            tuple(item.uuid for item in entries[0].items),
        )
        self.assertIsInstance(entries[0].items[0], SubworkflowBlock)

    def test_insertion_target_release_outside_circle_does_not_insert(self) -> None:
        scene = QGraphicsScene()
        scene.setSceneRect(-250.0, -80.0, 500.0, 160.0)
        insertion = InsertionItem(3)
        insertion.setPos(-22.0, -22.0)
        scene.addItem(insertion)
        view = QGraphicsView(scene)
        view.resize(600, 220)
        view.show()
        QApplication.processEvents()
        insertions: list[int] = []
        insertion.insert_requested.connect(insertions.append)
        press = view.mapFromScene(QPointF(0.0, 0.0))
        release = view.mapFromScene(QPointF(120.0, 0.0))

        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
        QTest.mouseMove(view.viewport(), release, delay=20)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=release)
        QApplication.processEvents()

        self.assertEqual([], insertions)
        view.close()
        view.deleteLater()

    def test_plain_wheel_scrolls_and_ctrl_wheel_zooms(self) -> None:
        self.canvas.resize(420, 360)
        self.canvas.view.setFixedHeight(220)
        self.canvas.render_entries(tuple(_item(f"node-{index}") for index in range(20)))
        QApplication.processEvents()
        scrollbar = self.canvas.view.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum() // 2)
        initial_scroll = scrollbar.value()
        initial_scale = self.canvas.view.transform().m11()

        self.canvas.view.wheelEvent(_wheel_event(self.canvas, -120, Qt.KeyboardModifier.NoModifier))
        self.assertGreater(scrollbar.value(), initial_scroll)
        self.assertEqual(initial_scale, self.canvas.view.transform().m11())

        self.canvas.view.wheelEvent(
            _wheel_event(
                self.canvas,
                120,
                Qt.KeyboardModifier.ControlModifier,
            )
        )
        self.assertGreater(self.canvas.view.transform().m11(), initial_scale)

    def test_context_menu_adapts_to_single_and_multiple_selection(self) -> None:
        self.canvas.render_entries((_item("first"), _item("second")))
        self.canvas.set_selected_entry_rows((0,))
        single_menu = self.canvas._create_context_menu()  # noqa: SLF001
        assert single_menu is not None
        single_labels = [action.text() for action in single_menu.actions()]
        self.assertIn("编辑参数", single_labels)
        self.assertIn("上移", single_labels)
        self.assertIn("删除所选", single_labels)

        self.canvas.set_selected_entry_rows((0, 1))
        multiple_menu = self.canvas._create_context_menu()  # noqa: SLF001
        assert multiple_menu is not None
        multiple_labels = [action.text() for action in multiple_menu.actions()]
        self.assertNotIn("编辑参数", multiple_labels)
        self.assertIn("创建循环", multiple_labels)
        self.assertIn("删除所选", multiple_labels)
        next(action for action in multiple_menu.actions() if action.text() == "删除所选").trigger()
        self.assertEqual(0, self.canvas.entry_count())

    def test_semantic_status_text_uses_contrasting_foreground(self) -> None:
        self.assertEqual(QColor("#111827"), contrasting_text(QColor("#f59e0b")))
        self.assertEqual(QColor("#ffffff"), contrasting_text(QColor("#dc2626")))


def _palette(window: str, base: str, text: str) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    palette.setColor(QPalette.ColorRole.Base, QColor(base))
    palette.setColor(QPalette.ColorRole.Text, QColor(text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#94a3b8"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#64748b"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
    return palette


def _action(action_id: str) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        name=f"Action {action_id}",
        type=ActionType.WAIT,
        parameters={"wait_seconds": 1.0},
    )


def _item(item_uuid: str) -> SequenceItem:
    return SequenceItem(
        uuid=item_uuid,
        definition=_action(item_uuid),
    )


def _parallel() -> ParallelBlock:
    return ParallelBlock(
        uuid="parallel",
        branches=[
            ParallelBranch("left-branch", [_item("left")]),
            ParallelBranch("right-branch", [_item("right")]),
        ],
    )


def _wheel_event(
    canvas: WorkflowCanvasWidget,
    angle_delta_y: int,
    modifiers: Qt.KeyboardModifier,
) -> QWheelEvent:
    center = QPointF(canvas.view.viewport().rect().center())
    return QWheelEvent(
        center,
        center,
        QPoint(),
        QPoint(0, angle_delta_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


if __name__ == "__main__":
    unittest.main()

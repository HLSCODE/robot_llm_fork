from __future__ import annotations

import gc
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QHelpEvent, QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsProxyWidget

from src.application import WorkflowCompiler
from src.domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    SequenceItem,
    SequenceItemStatus,
)
from src.domain.workflow import WorkflowDocument
from src.gui.views import WorkflowCanvasWidget
from src.gui.views.workflow_canvas.items import (
    InsertionItem,
    StartEndItem,
    WorkflowNodeItem,
)
from src.gui.views.workflow_canvas.tokens import contrasting_text


class WorkflowCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.canvas = WorkflowCanvasWidget()
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
        endpoints = [
            item
            for item in self.canvas.scene.items()
            if isinstance(item, StartEndItem)
        ]

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
            position = self.canvas.view.mapFromScene(
                endpoint.sceneBoundingRect().center()
            )
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
            (
                item
                for item in self.canvas.scene.items()
                if isinstance(item, InsertionItem)
            ),
            key=lambda item: item.scenePos().y(),
        )

        for insertion in insertions:
            view_position = self.canvas.view.mapFromScene(
                insertion.sceneBoundingRect().center()
            )
            QTest.mouseClick(
                self.canvas.view.viewport(),
                Qt.MouseButton.LeftButton,
                pos=view_position,
            )

        self.assertEqual([0, 1], requested)

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

    def test_loop_is_expanded_and_supports_precise_child_insertion(self) -> None:
        loop = LoopBlock(
            uuid="loop",
            items=[_item("first"), _item("second")],
            repeat_count=2,
        )
        self.canvas.render_entries((loop,))
        node = next(
            item
            for item in self.canvas.scene.items()
            if isinstance(item, WorkflowNodeItem)
        )

        self.assertGreater(node.node_width, 300)
        self.assertGreater(node.node_height, 200)
        self.assertEqual(
            0,
            node._loop_insertion_index_at(  # noqa: SLF001
                QPointF(node.node_width / 2.0, 102.0),
            ),
        )
        self.assertEqual(
            2,
            node._loop_insertion_index_at(  # noqa: SLF001
                QPointF(node.node_width / 2.0, 358.0),
            ),
        )
        requested: list[tuple[str, int]] = []
        self.canvas.insert_loop_action_requested.connect(
            lambda loop_uuid, index: requested.append((loop_uuid, index))
        )
        first_insert_position = self.canvas.view.mapFromScene(
            node.mapToScene(QPointF(node.node_width / 2.0, 102.0))
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
        before = tuple(entry.uuid for entry in self.canvas.get_entries())
        self.canvas.insert_action(_action("ignored-during-run"))
        self.assertEqual(before, tuple(entry.uuid for entry in self.canvas.get_entries()))
        self.canvas.finish_execution()

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
            any(
                isinstance(item, QGraphicsProxyWidget)
                for item in self.canvas.scene.items()
            )
        )
        self.assertGreaterEqual(self.canvas.fit_button.minimumHeight(), 44)

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

    def test_offscreen_size_matrix_preserves_accessible_touch_controls(self) -> None:
        self.canvas.render_entries(tuple(_item(f"node-{index}") for index in range(20)))

        for width, height in ((360, 640), (720, 1280), (1280, 720)):
            self.canvas.resize(width, height)
            QApplication.processEvents()
            self.assertEqual("任务工作流画布", self.canvas.view.accessibleName())
            self.assertTrue(self.canvas.view.accessibleDescription())
            self.assertGreaterEqual(self.canvas.fit_button.height(), 44)
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
            self.canvas.view.mapFromScene(node.sceneBoundingRect().center())
            for node in nodes
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
            [id(self.canvas._node_items[node_id]) for node_id in (  # noqa: SLF001
                "first",
                "second",
                "third",
            )],
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

        self.canvas.view.wheelEvent(
            _wheel_event(self.canvas, -120, Qt.KeyboardModifier.NoModifier)
        )
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
        next(
            action
            for action in multiple_menu.actions()
            if action.text() == "删除所选"
        ).trigger()
        self.assertEqual(0, self.canvas.entry_count())

    def test_semantic_status_text_uses_contrasting_foreground(self) -> None:
        self.assertEqual(QColor("#111827"), contrasting_text(QColor("#f59e0b")))
        self.assertEqual(QColor("#ffffff"), contrasting_text(QColor("#dc2626")))


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

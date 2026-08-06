from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QGraphicsProxyWidget

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

        self.assertEqual(("first",), document.order)
        self.assertFalse(
            any(
                isinstance(item, QGraphicsProxyWidget)
                for item in self.canvas.scene.items()
            )
        )
        self.assertGreaterEqual(self.canvas.fit_button.minimumHeight(), 36)


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


if __name__ == "__main__":
    unittest.main()

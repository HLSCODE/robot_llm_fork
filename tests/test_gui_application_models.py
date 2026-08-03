from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PyQt6.QtWidgets import QApplication

from src.application import (
    ComposedAction,
    ComposedTask,
    CompositionService,
    TaskComposerService,
)
from src.core.action_schema import get_action_schema
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.storage import JsonCompositionRepository
from src.device_runtime.ids import BODY_AXIS, PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui.dialogs import SchemaActionForm
from src.gui.view_models import DeviceViewModel, ExecutionViewModel


def _action(action_id: str) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        name=f"Action {action_id}",
        type=ActionType.WAIT,
        parameters={"wait_seconds": 1.0},
    )


class TaskComposerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        root = Path(self._temporary_directory.name)
        composition = CompositionService(
            JsonCompositionRepository(
                actions_file=root / "actions.json",
                tasks_directory=root / "tasks",
            )
        )
        composition.save_task(
            "task-a",
            (SequenceItem.from_definition(_action("task-step")),),
            origin="test",
        )
        self.composer = TaskComposerService(composition)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_draft_operations_and_expansion_are_service_owned(self) -> None:
        action = _action("direct")
        self.composer.add_task("task-a")
        self.composer.add_action(action)
        action.name = "mutated outside"

        self.composer.move(1, 0)
        self.composer.repeat(0, 1, 2)

        entries = self.composer.entries()
        self.assertEqual(4, len(entries))
        self.assertIsInstance(entries[0], ComposedAction)
        self.assertEqual("Action direct", entries[0].action.name)
        self.assertIsInstance(entries[1], ComposedTask)
        sequence = self.composer.build_sequence()
        self.assertEqual(
            ["direct", "task-step", "direct", "task-step"],
            [item.definition.id for item in sequence],
        )
        self.assertEqual(4, len({item.uuid for item in sequence}))

    def test_invalid_ranges_and_missing_tasks_are_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.composer.add_task("missing")
        self.composer.add_action(_action("one"))
        with self.assertRaises(IndexError):
            self.composer.move(0, 1)
        with self.assertRaises(ValueError):
            self.composer.repeat(0, 0, 1)


class DeviceAndExecutionViewModelTests(unittest.TestCase):
    def test_device_state_is_derived_from_service_snapshot(self) -> None:
        statuses = {
            ROBOT_SYSTEM: {"ready": True},
            BODY_AXIS: {"ready": False},
            PIPETTE: {"ready": True},
            RELAY_BANK: {"ready": True},
        }
        devices = SimpleNamespace(status=lambda: statuses)

        state = DeviceViewModel(devices).snapshot()

        self.assertTrue(state.robot_ready)
        self.assertFalse(state.body_ready)
        self.assertTrue(state.pipette_ready)
        self.assertTrue(state.relay_ready)

    def test_execution_controls_follow_runtime_state_machine(self) -> None:
        execution = _FakeExecutionService()
        view_model = ExecutionViewModel(execution)

        self.assertFalse(view_model.snapshot().active)
        execution.state = ExecutionState.RUNNING
        paused = view_model.toggle_pause()
        self.assertEqual(ExecutionState.PAUSED, paused.state)
        self.assertEqual("▶ 继续", paused.pause_button_text)
        resumed = view_model.toggle_pause()
        self.assertEqual(ExecutionState.RUNNING, resumed.state)
        cancelled = view_model.cancel()
        self.assertEqual(ExecutionState.CANCELLING, cancelled.state)


class SchemaActionFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_every_action_and_variant_uses_canonical_field_names(self) -> None:
        schema = get_action_schema()
        for action_type in ActionType:
            type_schema = schema[action_type.value]
            variants = type_schema.get("variants")
            if variants is None:
                form = SchemaActionForm(action_type)
                self.assertEqual(
                    set(type_schema.get("fields", {})),
                    set(form.field_names),
                )
                form.deleteLater()
                continue
            variant_key = type_schema["variant_key"]
            for variant_name, variant_schema in variants.items():
                form = SchemaActionForm(
                    action_type,
                    {variant_key: variant_name},
                )
                self.assertEqual(set(variants), set(form.variant_names))
                self.assertEqual(
                    set(variant_schema["fields"]) - {variant_key},
                    set(form.field_names),
                )
                self.assertEqual(
                    variant_name,
                    form.parameters()[variant_key],
                )
                form.deleteLater()


class _FakeExecutionService:
    def __init__(self) -> None:
        self.state = ExecutionState.IDLE

    def snapshot(self):
        return SimpleNamespace(
            state=self.state,
            active=self.state in {
                ExecutionState.STARTING,
                ExecutionState.RUNNING,
                ExecutionState.PAUSED,
                ExecutionState.CANCELLING,
            },
        )

    def pause(self) -> None:
        self.state = ExecutionState.PAUSED

    def resume(self) -> None:
        self.state = ExecutionState.RUNNING

    def cancel(self) -> None:
        self.state = ExecutionState.CANCELLING


if __name__ == "__main__":
    unittest.main()

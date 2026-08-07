from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication

from src.application import (
    ComposedAction,
    ComposedTask,
    CompositionService,
    TaskComposerService,
)
from src.domain.action_schema import get_action_schema
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.persistence.storage import JsonCompositionRepository
from src.devices.runtime.ids import BODY_AXIS, PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui.views.dialogs import SchemaActionForm
from src.gui.bridges.notifications import (
    GuiNotificationCenter,
    GuiNotificationLevel,
)
from src.gui.view_models import DeviceViewModel, ExecutionViewModel
from src.gui.controllers.startup import (
    GuiAuxiliaryServiceStartupWorker,
    GuiAuxiliaryStartupResultReceiver,
)


def _process_events_until(predicate, timeout_seconds: float = 2.0) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        sleep(0.005)
    QApplication.processEvents()
    return bool(predicate())


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
                actions_directory=root / "actions",
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
        self.assertEqual("继续", paused.pause_button_text)
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


class GuiNotificationCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_notifications_have_one_history_log_status_and_modal_path(self) -> None:
        parent = SchemaActionForm(ActionType.WAIT)
        logs: list[str] = []
        statuses: list[str] = []
        presenter = _FakeNotificationPresenter()
        notifications = GuiNotificationCenter(
            parent,
            log_sink=logs.append,
            status_sink=statuses.append,
            presenter=presenter,
            history_limit=2,
        )

        notifications.info("ready")
        warning = notifications.warning("device unavailable")
        notifications.error("execution failed", modal=False)

        state = notifications.snapshot()
        self.assertEqual(
            ["device unavailable", "execution failed"],
            [item.message for item in state.history],
        )
        self.assertEqual(GuiNotificationLevel.ERROR, state.latest.level)
        self.assertEqual(
            ["ready", "device unavailable", "execution failed"],
            logs,
        )
        self.assertEqual(logs, statuses)
        self.assertEqual([warning], presenter.shown)
        presenter.confirmed = True
        self.assertTrue(notifications.confirm("continue?"))
        parent.deleteLater()

    def test_worker_notification_is_presented_on_gui_thread(self) -> None:
        parent = SchemaActionForm(ActionType.WAIT)
        sink_threads: list[QThread] = []
        notifications = GuiNotificationCenter(
            parent,
            log_sink=lambda _message: sink_threads.append(QThread.currentThread()),
            status_sink=lambda _message: None,
            presenter=_FakeNotificationPresenter(),
        )
        gui_thread = QThread.currentThread()
        worker = Thread(target=lambda: notifications.info("background event"))

        worker.start()
        worker.join()
        QApplication.processEvents()

        self.assertEqual([gui_thread], sink_threads)
        parent.deleteLater()


class GuiAuxiliaryStartupResultReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_worker_result_schedules_transition_on_gui_thread(self) -> None:
        transition_completed = Event()
        thread_finished = Event()
        callback_threads: list[QThread] = []
        worker_thread = QThread()
        worker = GuiAuxiliaryServiceStartupWorker(lambda: ("ready",))

        def handle_completed(_snapshots: object) -> None:
            callback_threads.append(QThread.currentThread())
            QTimer.singleShot(0, transition_completed.set)

        receiver = GuiAuxiliaryStartupResultReceiver(
            handle_completed,
            self.fail,
            thread_finished.set,
        )
        worker.moveToThread(worker_thread)
        worker_thread.started.connect(worker.run)
        worker.completed.connect(
            receiver.handle_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.completed.connect(
            worker_thread.quit,
            Qt.ConnectionType.DirectConnection,
        )
        worker_thread.finished.connect(
            receiver.handle_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        worker_thread.start()

        self.assertTrue(_process_events_until(transition_completed.is_set))
        self.assertTrue(_process_events_until(thread_finished.is_set))
        self.assertEqual([QThread.currentThread()], callback_threads)
        self.assertTrue(worker_thread.wait(1000))
        worker.deleteLater()
        receiver.deleteLater()
        worker_thread.deleteLater()


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


class _FakeNotificationPresenter:
    def __init__(self) -> None:
        self.shown = []
        self.confirmed = False

    def show(self, _parent, notification) -> None:
        self.shown.append(notification)

    def confirm(self, _parent, _title: str, _message: str) -> bool:
        return self.confirmed


if __name__ == "__main__":
    unittest.main()

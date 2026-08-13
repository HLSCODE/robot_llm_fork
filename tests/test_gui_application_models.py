from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QSizePolicy

from src.application import (
    CompositionService,
    WorkflowEditingSession,
)
from src.domain.action_schema import get_action_schema
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.persistence.storage import JsonCompositionRepository
from src.devices.runtime.ids import BODY_AXIS, PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from src.execution import ExecutionEvent, ExecutionEventType, ExecutionState
from src.gui.bridges.execution import ExecutionBridge
from src.gui.views.dialogs import (
    ActionConfigDialog,
    CompensationEditor,
    ContentSizedStackedWidget,
    PoseEditor,
    SchemaActionForm,
)
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


class WorkflowEditingSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        root = Path(self._temporary_directory.name)
        self.composition = CompositionService(
            JsonCompositionRepository(
                actions_directory=root / "actions",
                workflows_directory=root / "workflows",
                workflow_drafts_directory=root / "drafts",
            )
        )
        self.composition.save_task(
            "task-a",
            (SequenceItem.from_definition(_action("task-step")),),
            origin="test",
        )
        self.session = WorkflowEditingSession(self.composition)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_saved_workflow_is_instantiated_with_fresh_identity(self) -> None:
        first = self.session.instantiate("task-a")
        second = self.session.instantiate("task-a")

        self.assertNotEqual(first.uuid, second.uuid)
        self.assertNotEqual(first.items[0].uuid, second.items[0].uuid)
        self.assertEqual("task-step", first.items[0].definition.id)
        self.assertTrue(first.source_workflow_id)

    def test_missing_workflow_is_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.session.instantiate("missing")


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

    def test_move_compensation_uses_guided_editor_and_reads_udp_snapshot(self) -> None:
        calls: list[dict[str, float]] = []

        def read_localization(**options: float) -> dict[str, float]:
            calls.append(options)
            return {
                "id": 7,
                "x": 1.25,
                "y": -2.5,
                "angle": 30.0,
                "timestamp": 10.0,
            }

        form = SchemaActionForm(
            ActionType.MOVE,
            {
                "目标": "机械臂",
                "臂": "左",
                "模式": "move_j",
                "点位": [0, 0, 0, 0, 0, 0],
            },
            localization_reader=read_localization,
        )
        editor = form.findChild(CompensationEditor, "compensationEditor")
        assert editor is not None
        editor.mode_combo.setCurrentIndex(editor.mode_combo.findData("udp"))
        button = editor.findChild(QPushButton, "captureLocalizationButton")
        assert button is not None
        button.click()

        compensation = form.parameters()["补偿"]

        self.assertEqual([{"max_age": 2.0, "wait_timeout": 0.0}], calls)
        self.assertEqual("udp", compensation["mode"])
        self.assertEqual(1.25, compensation["udp"]["teach_offset"]["x"])
        form.deleteLater()

    def test_visual_compensation_loads_stations_for_selected_arm(self) -> None:
        requested_arms: list[str | None] = []

        def station_choices(arm: str | None) -> list[tuple[str, str]]:
            requested_arms.append(arm)
            return [("station-a", "装粉工位（左臂）")]

        form = SchemaActionForm(
            ActionType.MOVE,
            {
                "目标": "机械臂",
                "臂": "左",
                "模式": "move_j",
                "点位": [0, 0, 0, 0, 0, 0],
                "补偿": {
                    "mode": "vision",
                    "vision": {"station_id": "station-a", "arm": "left"},
                },
            },
            station_choices_reader=station_choices,
        )
        editor = form.findChild(CompensationEditor, "compensationEditor")
        assert editor is not None

        compensation = form.parameters()["补偿"]

        self.assertIn("left", requested_arms)
        self.assertEqual(
            {
                "mode": "vision",
                "vision": {"station_id": "station-a", "arm": "left"},
            },
            compensation,
        )
        form.deleteLater()

    def test_compensation_stack_sizes_itself_from_the_visible_page(self) -> None:
        form = SchemaActionForm(ActionType.MOVE, {"目标": "机械臂"})
        editor = form.findChild(CompensationEditor, "compensationEditor")
        assert editor is not None
        stack = editor.findChild(ContentSizedStackedWidget, "compensationPages")
        assert stack is not None

        for mode in ("none", "udp", "vision"):
            editor.mode_combo.setCurrentIndex(editor.mode_combo.findData(mode))
            QApplication.processEvents()
            current = stack.currentWidget()
            assert current is not None
            self.assertEqual(current.sizeHint(), stack.sizeHint())
            self.assertEqual(
                QSizePolicy.Policy.Fixed,
                stack.sizePolicy().verticalPolicy(),
            )
        form.deleteLater()

    def test_pose_editor_reads_the_selected_arm_without_blocking_the_form(self) -> None:
        requested_arms: list[str] = []

        def read_pose(arm: str) -> list[float]:
            requested_arms.append(arm)
            return [0.1, -0.2, 0.3, 1.0, -1.1, 1.2]

        form = SchemaActionForm(
            ActionType.MOVE,
            {"目标": "机械臂", "臂": "右"},
            pose_reader=read_pose,
        )
        editor = form.findChild(PoseEditor, "poseEditor")
        assert editor is not None

        editor.read_button.click()
        self.assertTrue(
            _process_events_until(lambda: editor.read_button.isEnabled())
        )

        self.assertEqual(["右"], requested_arms)
        self.assertEqual(
            [0.1, -0.2, 0.3, 1.0, -1.1, 1.2],
            form.parameters()["点位"],
        )
        form.deleteLater()

    def test_required_fields_use_red_indicators_and_error_borders(self) -> None:
        dialog = ActionConfigDialog(ActionType.MOVE)
        indicators = dialog.findChildren(QLabel, "requiredFieldIndicator")

        with patch.object(QMessageBox, "warning") as warning:
            dialog._validate_and_accept()

        pose_editor = dialog.findChild(PoseEditor, "poseEditor")
        assert pose_editor is not None
        self.assertEqual(2, len(indicators))
        self.assertTrue(all(label.property("themeRole") == "danger" for label in indicators))
        self.assertEqual("error", dialog.name_input.property("validationState"))
        self.assertEqual("error", pose_editor.input.property("validationState"))
        warning.assert_called_once()

        dialog.name_input.setText("move-current-pose")
        pose_editor.input.setText("[0, 0, 0, 0, 0, 0]")
        self.assertEqual("", dialog.name_input.property("validationState"))
        self.assertEqual("", pose_editor.input.property("validationState"))
        dialog.deleteLater()

    def test_invalid_required_pose_keeps_its_error_border(self) -> None:
        form = SchemaActionForm(
            ActionType.MOVE,
            {"目标": "机械臂", "点位": [0, 0, 0, 0, 0, 0]},
        )
        editor = form.findChild(PoseEditor, "poseEditor")
        assert editor is not None
        editor.input.setText("not-json")

        with self.assertRaisesRegex(ValueError, "JSON 数组"):
            form.parameters()

        self.assertEqual("error", editor.input.property("validationState"))
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


class ExecutionBridgeTests(unittest.TestCase):
    def test_parallel_branch_event_preserves_runtime_identity_and_state(self) -> None:
        bridge = ExecutionBridge(SimpleNamespace(
            execution=object(),
            safety=object(),
        ))
        emitted: list[tuple[str, str, str, str]] = []
        bridge.parallel_branch_state.connect(
            lambda parallel_id, branch_id, state, message: emitted.append(
                (parallel_id, branch_id, state, message)
            )
        )

        bridge._on_event(ExecutionEvent(  # noqa: SLF001
            run_id="run-1",
            event_type=ExecutionEventType.PARALLEL_BRANCH_FAILED,
            origin="test",
            message="branch failed",
            data={
                "parallel_id": "parallel-1",
                "branch_id": "branch-2",
                "branch_state": "failed",
            },
        ))

        self.assertEqual(
            [("parallel-1", "branch-2", "failed", "branch failed")],
            emitted,
        )
        bridge.deleteLater()


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

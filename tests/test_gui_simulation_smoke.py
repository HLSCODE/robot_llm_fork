from __future__ import annotations

from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.settings import ApplicationSettings
from src.device_runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui import GuiStartupState, MainWindow
from src.gui.startup import GuiStartupLifecycle
from src.widgets.ai_assistant import AIAssistantWidget


def _wait_until(predicate, *, timeout_seconds: float = 2.0) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        sleep(0.005)
    QApplication.processEvents()
    return bool(predicate())


class GuiStartupLifecycleTests(unittest.TestCase):
    def test_lifecycle_rejects_duplicate_and_out_of_order_transitions(self) -> None:
        lifecycle = GuiStartupLifecycle()

        self.assertTrue(lifecycle.begin())
        self.assertFalse(lifecycle.begin())
        self.assertTrue(lifecycle.begin_hardware_initialization())
        self.assertFalse(lifecycle.begin_hardware_initialization())
        lifecycle.mark_ready()
        self.assertEqual(GuiStartupState.READY, lifecycle.state)
        with self.assertRaisesRegex(RuntimeError, "requires initializing_hardware"):
            lifecycle.mark_ready()
        lifecycle.close()
        self.assertEqual(GuiStartupState.CLOSED, lifecycle.state)
        self.assertFalse(lifecycle.begin())

    def test_waiting_startup_can_fail_explicitly(self) -> None:
        lifecycle = GuiStartupLifecycle()

        lifecycle.begin()
        lifecycle.mark_failed()

        self.assertEqual(GuiStartupState.FAILED, lifecycle.state)
        self.assertFalse(lifecycle.begin_hardware_initialization())


class GuiSimulationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        self.window = MainWindow(self.services)
        self.window.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        if self.services.execution.snapshot().active:
            self.services.execution.cancel()
            self.services.execution.wait(timeout=1)
        self.window.close()
        QApplication.processEvents()
        self.services.localization.close()
        self.assertEqual({}, self.services.devices.shutdown_all())

    def test_window_starts_with_shared_simulation_services(self) -> None:
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.services.simulation)
        self.assertEqual(GuiStartupState.READY, self.window.startup_state)
        device_state = self.window._device_view_model.snapshot()
        self.assertTrue(device_state.robot_ready)
        self.assertTrue(device_state.body_ready)
        self.assertTrue(device_state.pipette_ready)
        self.assertIsNotNone(
            self.services.device_runtime.get_if_ready(ROBOT_SYSTEM)
        )
        self.assertIsNotNone(
            self.services.device_runtime.get_if_ready(BODY_AXIS)
        )
        self.assertTrue(
            self.window.ai_assistant_widget.simulation_checkbox.isChecked()
        )
        self.assertFalse(
            self.window.ai_assistant_widget.simulation_checkbox.isEnabled()
        )

        self.window.close()
        QApplication.processEvents()

        self.assertEqual(GuiStartupState.CLOSED, self.window.startup_state)

    def test_buttons_drive_pause_resume_and_cancel_through_shared_runtime(self) -> None:
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="gui-simulation-wait",
                name="GUI simulation wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 5.0},
            )
        )
        self.services.composition.replace_sequence(
            [item],
            origin="gui-smoke-test",
        )
        self.assertTrue(
            _wait_until(
                lambda: self.window.sequence_list.topLevelItemCount() == 1
            )
        )

        self.window.control_panel.start_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: (
                    self.services.execution.snapshot().state
                    is ExecutionState.RUNNING
                    and self.window.control_panel.pause_btn.isEnabled()
                )
            )
        )

        self.window.control_panel.pause_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.PAUSED
            )
        )
        self.assertIn("继续", self.window.control_panel.pause_btn.text())

        self.window.control_panel.pause_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.RUNNING
            )
        )

        self.window.control_panel.stop_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.CANCELLED
            )
        )
        self.assertTrue(
            _wait_until(
                lambda: "序列执行失败或已停止"
                in self.window.log_widget.toPlainText()
            )
        )
        self.assertEqual("⏸ 暂停", self.window.control_panel.pause_btn.text())
        self.assertEqual({}, self.services.resources.snapshot())

    def test_task_composer_widget_renders_service_owned_draft(self) -> None:
        first = ActionDefinition(
            id="composer-first",
            name="Composer first",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 1.0},
        )
        second = ActionDefinition(
            id="composer-second",
            name="Composer second",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 1.0},
        )
        self.window._add_action_to_composer(first, 0)
        self.window._add_action_to_composer(second, 1)

        self.window.task_composer_list.order_changed.emit(0, 1)

        entries = self.services.task_composer.entries()
        self.assertEqual(
            ["composer-second", "composer-first"],
            [entry.action.id for entry in entries],
        )
        self.assertTrue(
            self.window.task_composer_list.item(0).data(
                Qt.ItemDataRole.UserRole
            )
        )


class GuiSpeechStartupSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_speech_startup_wait_is_asynchronous_and_signal_driven(self) -> None:
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        window = None
        try:
            with patch.object(
                AIAssistantWidget,
                "start_voice_speech_runtime_if_configured",
                return_value=True,
            ):
                window = MainWindow(services)

            self.assertEqual(
                GuiStartupState.WAITING_FOR_SPEECH,
                window.startup_state,
            )

            window.ai_assistant_widget.speech_runtime_startup_finished.emit(True)
            self.assertTrue(
                _wait_until(
                    lambda: window.startup_state is GuiStartupState.READY
                )
            )
        finally:
            if window is not None:
                window.close()
                QApplication.processEvents()
            services.localization.close()
            self.assertEqual({}, services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()

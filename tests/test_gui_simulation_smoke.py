from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

from src.application import create_application_services
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import ApplicationSettings
from src.devices.runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui import GuiStartupState, MainWindow
from src.gui.controllers.startup import GuiStartupLifecycle
from src.gui.views import StartupProgressCard
from src.gui.views.ai_assistant import AIAssistantWidget


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
        self.assertTrue(
            _wait_until(
                lambda: self.window.startup_state is GuiStartupState.READY
            )
        )

    def tearDown(self) -> None:
        if self.services.execution.snapshot().active:
            self.services.execution.cancel()
            self.services.execution.wait(timeout=1)
        self.window.close()
        QApplication.processEvents()
        self.window.shutdown_after_event_loop()
        asyncio.run(self.services.llm.close())
        self.services.external_localization.close()
        self.assertEqual({}, self.services.devices.shutdown_all())

    def test_window_starts_with_shared_simulation_services(self) -> None:
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.services.simulation)
        self.assertEqual(GuiStartupState.READY, self.window.startup_state)
        device_state = self.window._device_view_model.snapshot()
        self.assertTrue(device_state.robot_ready)
        self.assertTrue(device_state.body_ready)
        self.assertTrue(device_state.pipette_ready)
        self.assertTrue(self.services.devices.is_ready(ROBOT_SYSTEM))
        self.assertTrue(self.services.devices.is_ready(BODY_AXIS))
        self.assertTrue(
            self.window.action_library_view.ai_assistant.simulation_checkbox.isChecked()
        )
        self.assertFalse(
            self.window.action_library_view.ai_assistant.simulation_checkbox.isEnabled()
        )
        assistant = self.window.action_library_view.ai_assistant
        self.assertIs(assistant._ai_controller._llm_registry, self.services.llm)
        self.assertIs(assistant._voice_controller.llm_registry, self.services.llm)
        self.assertIsNotNone(self.window.workflow_view.sequence_list)
        self.assertIsNotNone(self.window.device_status_view)
        self.assertIsNotNone(self.window.device_control_view)
        for removed_alias in (
            "sequence_list",
            "control_panel",
            "task_composer_list",
            "ai_assistant_widget",
            "robot1_status_indicator",
            "gripper_open_btn",
        ):
            self.assertFalse(hasattr(self.window, removed_alias))

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
                lambda: self.window.workflow_view.sequence_list.topLevelItemCount() == 1
            )
        )

        self.window.workflow_view.control_panel.start_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: (
                    self.services.execution.snapshot().state
                    is ExecutionState.RUNNING
                    and self.window.workflow_view.control_panel.pause_btn.isEnabled()
                )
            )
        )

        self.window.workflow_view.control_panel.pause_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.PAUSED
            )
        )
        self.assertIn("继续", self.window.workflow_view.control_panel.pause_btn.text())

        self.window.workflow_view.control_panel.pause_btn.click()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.RUNNING
            )
        )

        self.window.workflow_view.control_panel.stop_btn.click()
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
        self.assertEqual("⏸ 暂停", self.window.workflow_view.control_panel.pause_btn.text())
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

        self.window.workflow_view.task_composer_list.order_changed.emit(0, 1)

        entries = self.services.task_composer.entries()
        self.assertEqual(
            ["composer-second", "composer-first"],
            [entry.action.id for entry in entries],
        )
        self.assertTrue(
            self.window.workflow_view.task_composer_list.item(0).data(
                Qt.ItemDataRole.UserRole
            )
        )

    def test_ai_widget_uses_narrow_signals_without_main_window_reference(self) -> None:
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="ai-signal-item",
                name="AI signal item",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 1.0},
            )
        )

        self.assertFalse(hasattr(self.window.action_library_view.ai_assistant, "_main_window"))
        self.window.action_library_view.ai_assistant.sequence_visualization_requested.emit(
            [item],
            True,
            0,
        )

        entries = self.services.composition.sequence_entries()
        self.assertEqual("ai-signal-item", entries[0].definition.id)

    def test_close_requests_execution_cancel_without_blocking_visible_gui(self) -> None:
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="close-cancel-wait",
                name="Close cancel wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 5.0},
            )
        )
        self.services.composition.replace_sequence([item], origin="close-test")
        self.window.start_execution()
        self.assertTrue(
            _wait_until(
                lambda: self.services.execution.snapshot().state
                is ExecutionState.RUNNING
            )
        )

        started_at = monotonic()
        self.window.close()
        QApplication.processEvents()

        self.assertLess(monotonic() - started_at, 0.5)
        self.assertIn(
            self.services.execution.snapshot().state,
            {ExecutionState.CANCELLING, ExecutionState.CANCELLED},
        )
        self.assertIn(
            "应用正在关闭，后台资源将按顺序释放...",
            [
                notification.message
                for notification in self.window._notifications.snapshot().history
            ],
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
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state
                        is GuiStartupState.WAITING_FOR_SPEECH
                    )
                )
                window.action_library_view.ai_assistant.speech_runtime_startup_finished.emit(
                    True
                )
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state is GuiStartupState.READY
                    )
                )
        finally:
            if window is not None:
                window.close()
                QApplication.processEvents()
                window.shutdown_after_event_loop()
            services.external_localization.close()
            self.assertEqual({}, services.devices.shutdown_all())

    def test_speech_wait_timeout_starts_hardware_and_reports_progress(self) -> None:
        settings = ApplicationSettings.defaults()
        settings = replace(
            settings,
            voice=replace(
                settings.voice,
                voice_input_enabled=True,
                voice_speech_startup_wait_timeout_s=0.02,
            ),
        )
        services = create_application_services(settings, simulation=True)
        window = None
        progress_messages: list[str] = []
        try:
            with patch.object(
                AIAssistantWidget,
                "start_voice_speech_runtime_if_configured",
                return_value=True,
            ):
                window = MainWindow(services)
                window.startup_progress_changed.connect(
                    lambda _percent, message, _detail: progress_messages.append(message)
                )

                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state is GuiStartupState.READY
                    )
                )
            self.assertTrue(
                any("后台加载" in message for message in progress_messages)
            )
        finally:
            if window is not None:
                window.close()
                QApplication.processEvents()
                window.shutdown_after_event_loop()
            services.external_localization.close()
            self.assertEqual({}, services.devices.shutdown_all())

    def test_hardware_initialization_after_speech_does_not_block_gui(self) -> None:
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        window = None
        initialization_started = Event()
        release_initialization = Event()
        gui_heartbeat = Event()
        original_initialize = services.devices.initialize

        def blocking_initialize(device_id: str):
            if device_id == ROBOT_SYSTEM:
                initialization_started.set()
                release_initialization.wait(timeout=1.0)
            return original_initialize(device_id)

        try:
            with (
                patch.object(
                    AIAssistantWidget,
                    "start_voice_speech_runtime_if_configured",
                    return_value=True,
                ),
                patch.object(
                    services.devices,
                    "initialize",
                    side_effect=blocking_initialize,
                ),
            ):
                window = MainWindow(services)
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state
                        is GuiStartupState.WAITING_FOR_SPEECH
                    )
                )
                window.action_library_view.ai_assistant.speech_runtime_startup_finished.emit(
                    True
                )
                self.assertTrue(_wait_until(initialization_started.is_set))
                QTimer.singleShot(10, gui_heartbeat.set)
                self.assertTrue(
                    _wait_until(gui_heartbeat.is_set, timeout_seconds=0.2)
                )
                release_initialization.set()
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state is GuiStartupState.READY
                    )
                )
        finally:
            release_initialization.set()
            if window is not None:
                window.close()
                QApplication.processEvents()
                window.shutdown_after_event_loop()
            services.external_localization.close()
            self.assertEqual({}, services.devices.shutdown_all())


class StartupProgressCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_progress_is_monotonic_and_failure_exposes_exit_action(self) -> None:
        card = StartupProgressCard()
        card.set_progress(40, "正在初始化机械臂...", "robot-system")
        card.set_progress(20, "迟到的旧进度", "ignored")

        self.assertEqual(40, card.progress_bar.value())
        self.assertEqual("40%", card.percent_label.text())
        card.mark_failed("测试失败")
        self.assertFalse(card.exit_button.isHidden())
        card.close()


if __name__ == "__main__":
    unittest.main()

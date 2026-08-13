from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.application import create_application_services
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import ApplicationSettings
from src.devices.runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui import GuiStartupState, MainWindow
from src.gui.controllers.startup import GuiStartupLifecycle
from src.gui.theme import ThemeController, ThemeMode
from src.gui.shortcuts import DEFAULT_SHORTCUTS
from src.gui.views import StartupProgressCard
from src.gui.views.ai_assistant import AIAssistantWidget
from src.gui.views.workflow_canvas.items import InsertionItem


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
        startup_action = ActionDefinition(
            id="startup-task-action",
            name="启动任务动作",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 1.0},
        )
        self.services.composition.save_task(
            "startup-visible",
            (SequenceItem.from_definition(startup_action),),
            origin="test",
        )
        self.window = MainWindow(
            self.services,
            ThemeController(self.application, ThemeMode.SYSTEM),
        )
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
        self.window._theme_controller.set_mode(ThemeMode.SYSTEM)
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
            self.window.ai_assistant_view.simulation_checkbox.isChecked()
        )
        self.assertFalse(
            self.window.ai_assistant_view.simulation_checkbox.isEnabled()
        )
        assistant = self.window.ai_assistant_view
        self.assertIs(assistant._ai_controller._llm_registry, self.services.llm)
        self.assertIs(assistant._voice_controller.llm_registry, self.services.llm)
        self.assertIsNotNone(self.window.workflow_view.sequence_list)
        self.assertIsNotNone(self.window.device_status_view)
        self.assertIsNotNone(self.window.device_control_view)
        self.assertEqual(
            1,
            self.window.task_library_view.task_library_list.count(),
        )
        self.assertEqual(
            "startup-visible.workflow.json",
            self.window.task_library_view.task_library_list.item(0).data(
                Qt.ItemDataRole.UserRole
            ),
        )
        self.assertEqual(
            "startup-visible (1 步)",
            self.window.task_library_view.task_library_list.item(0).text(),
        )
        for removed_alias in (
            "sequence_list",
            "control_panel",
            "task_composer_list",
            "ai_assistant_widget",
            "robot1_status_indicator",
            "gripper_open_btn",
        ):
            self.assertFalse(hasattr(self.window, removed_alias))

    def test_canvas_plus_click_opens_picker_and_inserts_selected_action(self) -> None:
        action = ActionDefinition(
            id="plus-click-action",
            name="加号插入测试",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 1.0},
        )
        self.window.actions[ActionType.WAIT] = [action]
        canvas = self.window.workflow_view.sequence_list
        canvas.render_entries(())
        QApplication.processEvents()
        insertion = next(
            item
            for item in canvas.scene.items()
            if isinstance(item, InsertionItem)
        )
        position = canvas.view.mapFromScene(
            insertion.sceneBoundingRect().center()
        )

        with patch.object(
            self.window,
            "_choose_action",
            return_value=action,
        ):
            QTest.mouseClick(
                canvas.view.viewport(),
                Qt.MouseButton.LeftButton,
                pos=position,
            )
            QApplication.processEvents()

        self.assertEqual(1, canvas.entry_count())
        inserted = canvas.get_entries()[0]
        self.assertIsInstance(inserted, SequenceItem)
        assert isinstance(inserted, SequenceItem)
        self.assertEqual("plus-click-action", inserted.definition.id)

    def test_workbench_keeps_canvas_and_safety_commands_available(self) -> None:
        workbench = self.window.workbench_view
        self.assertEqual(
            {"tasks", "actions", "assistant"},
            set(workbench.activity_bar.buttons),
        )
        resource_button = workbench.activity_bar.buttons["tasks"]

        if workbench.active_side_page != "tasks":
            resource_button.click()
            QApplication.processEvents()

        self.assertEqual("tasks", workbench.active_side_page)
        self.assertTrue(self.window.task_library_view.isVisible())
        resource_button.click()
        QApplication.processEvents()
        self.assertFalse(workbench.side_stack.isVisible())
        self.assertTrue(self.window.workflow_view.sequence_list.isVisible())
        controls = self.window.workflow_view.control_panel
        for button in (
            controls.stop_btn,
            controls.quick_stop_btn,
            controls.emergency_stop_btn,
        ):
            self.assertTrue(button.isVisible())
            self.assertGreaterEqual(button.height(), 44)

        workbench.status_bar.buttons["poses"].click()
        QApplication.processEvents()
        self.assertEqual("poses", workbench.active_bottom_page)
        self.assertTrue(self.window.device_pose_view.isVisible())

    def test_theme_menu_switches_the_single_application_theme(self) -> None:
        self.window._theme_actions[ThemeMode.DARK].trigger()
        QApplication.processEvents()

        self.assertIs(ThemeMode.DARK, self.window._theme_controller.mode)
        self.assertTrue(self.window._theme_actions[ThemeMode.DARK].isChecked())
        self.assertEqual(
            QColor("#0f172a"),
            QApplication.palette().color(QPalette.ColorRole.Window),
        )

        self.window.close()
        QApplication.processEvents()

        self.assertEqual(GuiStartupState.CLOSED, self.window.startup_state)

    def test_top_menu_commands_share_one_nonempty_shortcut_registry(self) -> None:
        expected_command_ids = {
            definition.command_id for definition in DEFAULT_SHORTCUTS
        }

        self.assertEqual(expected_command_ids, set(self.window._menu_actions))
        self.assertIn("view.shortcuts", self.window._menu_actions)
        for command_id, action in self.window._menu_actions.items():
            with self.subTest(command=command_id):
                self.assertFalse(action.shortcut().isEmpty())
                self.assertIn(self.window, action.associatedObjects())

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
                lambda: self.window.workflow_view.sequence_list.entry_count() == 1
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
        self.assertIn(
            "继续",
            self.window.workflow_view.control_panel.pause_btn.toolTip(),
        )

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
        self.assertEqual(
            "暂停",
            self.window.workflow_view.control_panel.pause_btn.toolTip(),
        )
        self.assertEqual({}, self.services.resources.snapshot())

    def test_saved_task_can_be_inserted_as_editable_subworkflow(self) -> None:
        library = self.window.task_library_view.task_library_list
        library.setCurrentRow(0)

        self.window.insert_selected_task()

        entries = self.window.workflow_view.sequence_list.get_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("startup-visible", entries[0].name)
        self.assertTrue(entries[0].source_workflow_id)

    def test_ai_widget_uses_narrow_signals_without_main_window_reference(self) -> None:
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="ai-signal-item",
                name="AI signal item",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 1.0},
            )
        )

        self.assertFalse(hasattr(self.window.ai_assistant_view, "_main_window"))
        self.window.ai_assistant_view.sequence_visualization_requested.emit(
            [item],
            True,
            0,
        )

        entries = self.services.workflow_editing.snapshot().document.to_entries()
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
                window = MainWindow(
                    services,
                    ThemeController(self.application, ThemeMode.SYSTEM),
                )
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state
                        is GuiStartupState.WAITING_FOR_SPEECH
                    )
                )
                window.ai_assistant_view.speech_runtime_startup_finished.emit(
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
                window = MainWindow(
                    services,
                    ThemeController(self.application, ThemeMode.SYSTEM),
                )
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
                window = MainWindow(
                    services,
                    ThemeController(self.application, ThemeMode.SYSTEM),
                )
                self.assertTrue(
                    _wait_until(
                        lambda: window.startup_state
                        is GuiStartupState.WAITING_FOR_SPEECH
                    )
                )
                window.ai_assistant_view.speech_runtime_startup_finished.emit(
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

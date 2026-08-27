from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSizePolicy

from src.application import create_application_services
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import ApplicationSettings
from src.devices.runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.gui import GuiStartupState, MainWindow
from src.gui.branding import APPLICATION_NAME
from src.gui.controllers.startup import GuiHardwareStartupWorker, GuiStartupLifecycle
from src.gui.theme import ThemeController, ThemeMode
from src.gui.shortcuts import DEFAULT_SHORTCUTS
from src.gui.views import StartupProgressCard
from src.gui.views.ai_assistant import AIAssistantWidget
from src.gui.views.log_widget import LogFilter
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

    def test_hardware_startup_failure_records_device_and_traceback(self) -> None:
        def fail_initialization() -> None:
            raise RuntimeError("native initialization failed")

        with self.assertLogs(
            "src.gui.controllers.startup",
            level="ERROR",
        ) as captured:
            result = GuiHardwareStartupWorker._run_step(
                ROBOT_SYSTEM,
                fail_initialization,
            )

        self.assertFalse(result.succeeded)
        self.assertEqual("native initialization failed", result.error)
        message = "\n".join(captured.output)
        self.assertIn("device_id=robot-system", message)
        self.assertIn("RuntimeError: native initialization failed", message)


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
        self.assertEqual(
            QSizePolicy.Policy.Expanding,
            assistant.chat_history.sizePolicy().verticalPolicy(),
        )
        self.assertGreater(assistant.chat_history.maximumHeight(), 1_000_000)
        self.assertTrue(assistant.simulation_checkbox.isHidden())
        self.assertEqual("模拟", assistant.mode_badge.text())
        self.assertTrue(assistant.skill_list.isHidden())
        self.assertIn(
            str(assistant.skill_list.count()),
            assistant.skill_toggle_button.text(),
        )
        assistant.skill_toggle_button.click()
        QApplication.processEvents()
        self.assertFalse(assistant.skill_list.isHidden())
        self.assertTrue(assistant.plan_card.isHidden())

        assistant._handle_interaction_event(
            {
                "type": "command_preview",
                "text": "已生成测试方案",
                "data": {
                    "preview_id": "preview-layout-test",
                    "version": 1,
                    "sequence": [{"type": "WAIT"}],
                },
            },
            source="dialog",
        )
        self.assertFalse(assistant.plan_card.isHidden())
        self.assertEqual("待执行方案 · 1 个步骤", assistant.plan_summary_label.text())
        assistant._reset_ui()
        self.assertTrue(assistant.plan_card.isHidden())
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

    def test_window_uses_custom_title_bar_with_embedded_menu(self) -> None:
        title_bar = self.window.application_title_bar

        self.assertTrue(
            self.window.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        self.assertIs(title_bar.menu_bar, self.window.application_menu_bar)
        self.assertEqual(APPLICATION_NAME, self.window.windowTitle())
        self.assertFalse(hasattr(title_bar, "title_label"))
        self.assertFalse(title_bar.icon_label.pixmap().isNull())
        self.assertEqual("最小化", title_bar.minimize_button.accessibleName())
        self.assertEqual("最大化", title_bar.maximize_button.accessibleName())
        self.assertEqual("关闭", title_bar.close_button.accessibleName())
        self.assertTrue(self.window.mask().isEmpty())
        self.assertEqual("rounded", title_bar.property("windowCorners"))
        self.assertEqual(
            "rounded",
            self.window.workbench_view.status_bar.property("windowCorners"),
        )
        restored_image = self.window.grab().toImage()
        self.assertEqual(0, restored_image.pixelColor(0, 0).alpha())
        self.assertEqual(
            0,
            restored_image.pixelColor(
                restored_image.width() - 1,
                restored_image.height() - 1,
            ).alpha(),
        )
        self.assertGreater(
            restored_image.pixelColor(restored_image.width() // 2, 0).alpha(),
            0,
        )
        self.assertEqual(
            255,
            restored_image.pixelColor(
                restored_image.width() // 2,
                title_bar.height() + 8,
            ).alpha(),
        )

        title_bar.maximize_button.click()
        QApplication.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertEqual("还原", title_bar.maximize_button.accessibleName())
        self.assertTrue(self.window.mask().isEmpty())
        self.assertEqual("square", title_bar.property("windowCorners"))
        maximized_image = self.window.grab().toImage()
        self.assertGreater(maximized_image.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(
            maximized_image.pixelColor(
                maximized_image.width() - 1,
                maximized_image.height() - 1,
            ).alpha(),
            0,
        )

        title_bar.maximize_button.click()
        QApplication.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual("最大化", title_bar.maximize_button.accessibleName())
        self.assertTrue(self.window.mask().isEmpty())
        self.assertEqual("rounded", title_bar.property("windowCorners"))

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

        self.assertTrue(
            _wait_until(
                lambda: self.window._theme_controller.mode is ThemeMode.DARK
            )
        )
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
        self.assertIn("help.about", self.window._menu_actions)
        menus = {
            menu.title(): menu
            for menu in self.window.application_menu_bar._buttons
        }
        self.assertIn("帮助", menus)
        self.assertNotIn(
            "快捷键设置…",
            [action.text() for action in menus["视图"].actions()],
        )
        self.assertEqual(
            ["快捷键设置…", "关于 机器人工作流控制台"],
            [
                action.text()
                for action in menus["帮助"].actions()
                if not action.isSeparator()
            ],
        )
        for command_id, action in self.window._menu_actions.items():
            with self.subTest(command=command_id):
                self.assertFalse(action.shortcut().isEmpty())
                self.assertIn(self.window, action.associatedObjects())

    def test_window_shortcut_remains_active_with_custom_title_bar(self) -> None:
        workbench = self.window.workbench_view
        self.assertIsNotNone(workbench.active_side_page)

        QTest.keyClick(
            self.window,
            Qt.Key.Key_B,
            Qt.KeyboardModifier.ControlModifier,
        )
        QApplication.processEvents()

        self.assertIsNone(workbench.active_side_page)

    def test_non_modal_problem_uses_one_corner_toast_instead_of_status_text(self) -> None:
        toast = self.window.workbench_view.notification_toast
        details = "第一行错误\n必要设备未就绪: robot-system " + "诊断详情 " * 100

        self.window._notifications.error(details, modal=False)
        QApplication.processEvents()

        self.assertTrue(toast.isVisible())
        self.assertNotIn("\n", toast.message_label.text())
        self.assertTrue(toast.message_label.text().endswith("…"))
        self.assertEqual(details.strip(), toast.message_label.toolTip())
        self.assertFalse(hasattr(self.window.workbench_view.status_bar, "message_label"))

    def test_typed_notifications_update_problem_counts_and_shared_log_filter(self) -> None:
        status_bar = self.window.workbench_view.status_bar
        initial_errors = self.window.log_widget.error_count
        initial_warnings = self.window.log_widget.warning_count

        self.window._notifications.warning("smoke warning", modal=False)
        self.window._notifications.error("smoke error", modal=False)
        QApplication.processEvents()

        self.assertEqual(initial_errors + 1, self.window.log_widget.error_count)
        self.assertEqual(initial_warnings + 1, self.window.log_widget.warning_count)
        self.assertEqual(
            str(initial_errors + 1),
            status_bar.log_problem_buttons[LogFilter.ERRORS].text(),
        )
        self.assertEqual(
            str(initial_warnings + 1),
            status_bar.log_problem_buttons[LogFilter.WARNINGS].text(),
        )

        status_bar.log_problem_buttons[LogFilter.ERRORS].click()
        QApplication.processEvents()
        self.assertEqual(LogFilter.ERRORS, self.window.log_widget.active_filter)
        self.assertIn("smoke error", self.window.log_widget.toPlainText())
        self.assertNotIn("smoke warning", self.window.log_widget.toPlainText())

        status_bar.log_problem_buttons[LogFilter.WARNINGS].click()
        QApplication.processEvents()
        self.assertEqual(LogFilter.WARNINGS, self.window.log_widget.active_filter)
        self.assertIn("smoke warning", self.window.log_widget.toPlainText())
        self.assertNotIn("smoke error", self.window.log_widget.toPlainText())

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

    def test_trajectory_recording_uses_configured_storage_without_save_dialog(
        self,
    ) -> None:
        with (
            patch.object(self.window._notifications, "info"),
            patch(
                "src.gui.controllers.main_window.QFileDialog.getSaveFileName"
            ) as save_dialog,
        ):
            result = self.window.record_trajectory("robot1")

        self.assertIsNotNone(result)
        assert result is not None
        saved_path = Path(result)
        self.assertEqual(
            self.services.trajectory_teaching.trajectory_directory("robot1"),
            saved_path.parent,
        )
        self.assertTrue(saved_path.is_file())
        save_dialog.assert_not_called()


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

    def test_startup_card_displays_the_application_logo(self) -> None:
        card = StartupProgressCard()

        logo = card.logo_label.pixmap()

        self.assertIsNotNone(logo)
        assert logo is not None
        self.assertFalse(logo.isNull())
        self.assertEqual(f"{APPLICATION_NAME} Logo", card.logo_label.accessibleName())
        self.assertEqual(APPLICATION_NAME, card.title_label.text())
        card.close()

    def test_progress_updates_keep_normal_card_height_stable(self) -> None:
        card = StartupProgressCard()
        card.show()
        QApplication.processEvents()
        normal_height = card.height()

        card.set_progress(40, "正在初始化机械臂...", "robot-system")
        QApplication.processEvents()
        self.assertEqual(normal_height, card.height())

        card.set_progress(54, "正在连接机械臂...", "")
        QApplication.processEvents()

        self.assertEqual("", card.detail_label.text())
        self.assertFalse(card.detail_label.isHidden())
        self.assertEqual(normal_height, card.height())

        card.set_progress(
            66,
            "正在连接移动底盘...",
            "设备初始化切换时复用稳定的详情区域",
        )
        QApplication.processEvents()
        self.assertEqual(normal_height, card.height())

        card.mark_failed("设备连接失败")
        QApplication.processEvents()
        self.assertFalse(card.detail_label.isHidden())
        self.assertGreater(card.height(), normal_height)
        card.close()


if __name__ == "__main__":
    unittest.main()

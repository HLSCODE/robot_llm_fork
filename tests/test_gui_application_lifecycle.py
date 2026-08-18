from __future__ import annotations

import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from src.bootstrap.launcher import run_gui
from src.gui.app_dialogs import AppDialog
from src.gui.application_lifecycle import (
    GUI_STARTUP_FAILURE_EXIT_CODE,
    GuiDisplayUnavailableError,
    GuiPresentationStatus,
    install_gui_application_lifecycle,
    require_available_display,
)
from src.gui.bridges.notifications import (
    GuiNotification,
    GuiNotificationCenter,
)
from src.gui.controllers.main_window import MainWindow
from src.gui.views.startup import StartupProgressCard


class GuiStartupEnvironmentTests(unittest.TestCase):
    def test_no_screen_is_a_diagnostic_startup_failure(self) -> None:
        inventory = SimpleNamespace(
            screens=lambda: [],
            primaryScreen=lambda: None,
        )

        with self.assertRaisesRegex(
            GuiDisplayUnavailableError,
            "未检测到可用图形屏幕",
        ):
            require_available_display(inventory)

    def test_launcher_returns_nonzero_before_constructing_a_window(self) -> None:
        with (
            patch(
                "src.gui.application_lifecycle.create_gui_application",
                side_effect=GuiDisplayUnavailableError("no screens"),
            ),
            self.assertLogs("src.bootstrap.launcher", level="ERROR") as captured,
        ):
            result = run_gui(SimpleNamespace(), SimpleNamespace())

        self.assertEqual(GUI_STARTUP_FAILURE_EXIT_CODE, result)
        self.assertIn("GUI 启动环境不可用", captured.output[0])


class GuiWindowShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_shared_dialog_refuses_late_window_creation(self) -> None:
        dialog = AppDialog()
        dialog.setWindowTitle("迟到的对话框")
        unavailable = GuiPresentationStatus(False, "QApplication 已进入退出流程")

        with (
            patch(
                "src.gui.app_dialogs.gui_presentation_status",
                return_value=unavailable,
            ),
            self.assertLogs("src.gui.app_dialogs", level="WARNING") as captured,
        ):
            result = dialog.exec()

        self.assertEqual(int(QDialog.DialogCode.Rejected), result)
        self.assertFalse(dialog.isVisible())
        self.assertIn("迟到的对话框", captured.output[0])
        dialog.deleteLater()

    def test_startup_card_refuses_presentation_after_screen_loss(self) -> None:
        card = StartupProgressCard()
        unavailable = GuiPresentationStatus(False, "最后一个屏幕已断开")

        with (
            patch(
                "src.gui.views.startup.gui_presentation_status",
                return_value=unavailable,
            ),
            self.assertLogs("src.gui.views.startup", level="ERROR"),
        ):
            presentation = card.show_if_available()

        self.assertFalse(presentation.allowed)
        self.assertFalse(card.isVisible())
        card.deleteLater()

    def test_notification_center_stops_touching_gui_sinks_during_shutdown(self) -> None:
        parent = QWidget()
        logs: list[GuiNotification] = []
        toasts: list[GuiNotification] = []
        presenter = _RecordingPresenter()
        notifications = GuiNotificationCenter(
            parent,
            log_sink=logs.append,
            toast_sink=toasts.append,
            presenter=presenter,
        )
        notifications.begin_shutdown()

        with self.assertLogs(
            "src.gui.bridges.notifications",
            level="WARNING",
        ):
            notification = notifications.warning("late hardware result")

        self.assertEqual((notification,), notifications.snapshot().history)
        self.assertEqual([], logs)
        self.assertEqual([], toasts)
        self.assertEqual([], presenter.shown)
        parent.deleteLater()

    def test_post_event_loop_cleanup_uses_logging_not_gui_notifications(self) -> None:
        hardware_thread = _TimeoutThread()
        worker = _StopRecorder()
        assistant = _ShutdownRecorder()
        window = SimpleNamespace(
            _hardware_startup_thread=hardware_thread,
            _hardware_startup_worker=worker,
            _camera_test_thread=None,
            ai_assistant_view=assistant,
        )

        with self.assertLogs(
            "src.gui.controllers.main_window",
            level="WARNING",
        ) as captured:
            MainWindow.shutdown_after_event_loop(window)

        self.assertTrue(worker.stop_requested)
        self.assertTrue(hardware_thread.quit_requested)
        self.assertEqual([10_000, None], hardware_thread.wait_calls)
        self.assertTrue(assistant.shutdown_requested)
        self.assertIn("设备初始化线程未在 10 秒内退出", captured.output[0])

    def test_registered_worker_is_joined_before_qapplication_teardown(self) -> None:
        lifecycle = install_gui_application_lifecycle(self.application)
        thread = _SlowThread()
        self.assertTrue(
            lifecycle.register_background_thread(thread, "测试位姿读取线程")
        )
        thread.start()

        with self.assertLogs(
            "src.gui.application_lifecycle",
            level="WARNING",
        ):
            lifecycle.join_background_threads(grace_period_ms=1)

        self.assertFalse(thread.isRunning())
        thread.deleteLater()


class _RecordingPresenter:
    def __init__(self) -> None:
        self.shown: list[GuiNotification] = []

    def show(self, _parent: QWidget, notification: GuiNotification) -> None:
        self.shown.append(notification)

    def confirm(self, _parent: QWidget, _title: str, _message: str) -> bool:
        return True


class _SlowThread(QThread):
    def run(self) -> None:
        time.sleep(0.03)


class _TimeoutThread:
    def __init__(self) -> None:
        self.quit_requested = False
        self.wait_calls: list[int | None] = []

    def isRunning(self) -> bool:  # noqa: N802
        return True

    def quit(self) -> None:
        self.quit_requested = True

    def wait(self, milliseconds: int | None = None) -> bool:
        self.wait_calls.append(milliseconds)
        return milliseconds is None


class _StopRecorder:
    def __init__(self) -> None:
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True


class _ShutdownRecorder:
    def __init__(self) -> None:
        self.shutdown_requested = False

    def shutdown(self) -> None:
        self.shutdown_requested = True


if __name__ == "__main__":
    unittest.main()

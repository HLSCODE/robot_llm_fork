"""Single-owner Qt application and top-level window lifecycle guards."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from threading import current_thread, main_thread
from typing import Protocol

from PySide6.QtCore import QCoreApplication, QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


logger = logging.getLogger(__name__)

GUI_STARTUP_FAILURE_EXIT_CODE = 3
GUI_LIFECYCLE_OBJECT_NAME = "guiApplicationLifecycle"


class GuiStartupError(RuntimeError):
    """Describe a GUI startup failure that can be reported without a window."""


class GuiApplicationOwnershipError(GuiStartupError):
    """Raised when the process cannot establish one GUI application owner."""


class GuiDisplayUnavailableError(GuiStartupError):
    """Raised when Qt has no screen on which a window can be presented."""


class GuiLifecycleState(str, Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    DISPLAY_UNAVAILABLE = "display_unavailable"


class _ScreenInventory(Protocol):
    def screens(self) -> list[QScreen]: ...

    def primaryScreen(self) -> QScreen | None: ...  # noqa: N802


@dataclass(frozen=True, slots=True)
class GuiPresentationStatus:
    allowed: bool
    reason: str = ""


def create_gui_application(arguments: list[str]) -> QApplication:
    """Create the process-wide QApplication on the Python main thread."""
    if current_thread() is not main_thread():
        raise GuiApplicationOwnershipError(
            "GUI 必须在进程主线程启动，当前调用来自后台线程"
        )
    if QCoreApplication.instance() is not None:
        raise GuiApplicationOwnershipError(
            "进程中已经存在 Qt Application；GUI 入口只能创建一个 QApplication"
        )

    application = QApplication(arguments)
    if application.thread() is not QThread.currentThread():
        raise GuiApplicationOwnershipError("QApplication 未归属于当前 GUI 主线程")
    require_available_display(application)
    return application


def require_available_display(application: _ScreenInventory) -> None:
    """Reject headless startup before constructing the first top-level widget."""
    screens = tuple(application.screens())
    primary_screen = application.primaryScreen()
    if screens and primary_screen is not None:
        return
    platform_name = QApplication.platformName() or "unknown"
    raise GuiDisplayUnavailableError(
        "未检测到可用图形屏幕，GUI 未启动"
        f"（Qt platform={platform_name}）。请确认桌面会话仍有效，"
        "并检查 Windows/RDP 或 Linux DISPLAY/WAYLAND_DISPLAY 配置"
    )


class GuiApplicationLifecycle(QObject):
    """Own whether the process may still present a top-level GUI window."""

    display_unavailable = Signal(str)

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self.setObjectName(GUI_LIFECYCLE_OBJECT_NAME)
        self._application = application
        self._state = GuiLifecycleState.ACTIVE
        self._reason = ""
        self._background_threads: dict[QThread, str] = {}
        application.aboutToQuit.connect(
            self._on_about_to_quit,
            Qt.ConnectionType.DirectConnection,
        )
        application.screenRemoved.connect(
            self._on_screen_removed,
            Qt.ConnectionType.DirectConnection,
        )

    @property
    def state(self) -> GuiLifecycleState:
        return self._state

    @property
    def reason(self) -> str:
        return self._reason

    def presentation_status(
        self,
        parent: QWidget | None = None,
    ) -> GuiPresentationStatus:
        if self._state is not GuiLifecycleState.ACTIVE:
            return GuiPresentationStatus(False, self._reason or "GUI 正在关闭")
        if QCoreApplication.closingDown():
            return GuiPresentationStatus(False, "QApplication 正在销毁")
        if QThread.currentThread() is not self._application.thread():
            return GuiPresentationStatus(False, "窗口只能在 GUI 主线程展示")
        if (
            not self._application.screens()
            or self._application.primaryScreen() is None
        ):
            return GuiPresentationStatus(False, "当前没有可用图形屏幕")
        if parent is not None and (
            not isValid(parent) or parent.thread() is not self._application.thread()
        ):
            return GuiPresentationStatus(False, "父窗口已销毁或不属于 GUI 主线程")
        return GuiPresentationStatus(True)

    def begin_shutdown(self, reason: str = "GUI 正在关闭") -> None:
        if self._state is not GuiLifecycleState.ACTIVE:
            return
        self._state = GuiLifecycleState.CLOSING
        self._reason = reason

    def begin_window_session(self) -> None:
        """Activate a newly constructed main-window session in this QApplication."""
        require_available_display(self._application)
        if QCoreApplication.closingDown():
            raise GuiDisplayUnavailableError("QApplication 正在销毁，无法创建主窗口")
        self._state = GuiLifecycleState.ACTIVE
        self._reason = ""

    def register_background_thread(self, thread: QThread, label: str) -> bool:
        """Keep a worker thread alive until it finishes or shutdown joins it."""
        if self._state is not GuiLifecycleState.ACTIVE or QCoreApplication.closingDown():
            return False
        self._background_threads[thread] = label.strip() or thread.objectName()
        thread.finished.connect(
            lambda thread=thread: self._background_threads.pop(thread, None)
        )
        return True

    def join_background_threads(self, grace_period_ms: int = 2000) -> None:
        """Stop and join registered workers before QApplication is destroyed."""
        grace_period_ms = max(0, int(grace_period_ms))
        for thread, label in tuple(self._background_threads.items()):
            if not isValid(thread):
                self._background_threads.pop(thread, None)
                continue
            try:
                running = thread.isRunning()
            except RuntimeError:
                self._background_threads.pop(thread, None)
                continue
            if running:
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(grace_period_ms):
                    logger.warning(
                        "%s 未在 %.1f 秒内退出；继续等待，避免销毁运行中的 Qt 线程",
                        label or "GUI 后台线程",
                        grace_period_ms / 1000.0,
                    )
                    thread.wait()
            self._background_threads.pop(thread, None)

    @Slot()
    def _on_about_to_quit(self) -> None:
        self.begin_shutdown("QApplication 已进入退出流程")

    @Slot(QScreen)
    def _on_screen_removed(self, removed_screen: QScreen) -> None:
        if self._has_remaining_screen(removed_screen):
            return
        message = (
            "图形会话中的最后一个屏幕已断开；应用将停止创建窗口并安全退出"
        )
        self._state = GuiLifecycleState.DISPLAY_UNAVAILABLE
        self._reason = message
        logger.error(message)
        self.display_unavailable.emit(message)
        self._application.quit()

    def _has_remaining_screen(self, removed_screen: QScreen) -> bool:
        screens = tuple(self._application.screens())
        if any(screen != removed_screen for screen in screens):
            return True
        primary_screen = self._application.primaryScreen()
        return primary_screen is not None and primary_screen != removed_screen


def install_gui_application_lifecycle(
    application: QApplication,
) -> GuiApplicationLifecycle:
    """Install or return the one lifecycle owner attached to QApplication."""
    existing = application.findChild(
        GuiApplicationLifecycle,
        GUI_LIFECYCLE_OBJECT_NAME,
    )
    if existing is not None:
        existing.begin_window_session()
        return existing
    return GuiApplicationLifecycle(application)


def gui_application_lifecycle() -> GuiApplicationLifecycle | None:
    application = QApplication.instance()
    if application is None:
        return None
    return application.findChild(
        GuiApplicationLifecycle,
        GUI_LIFECYCLE_OBJECT_NAME,
    )


def gui_presentation_status(
    parent: QWidget | None = None,
) -> GuiPresentationStatus:
    """Return a diagnostic presentation decision for shared GUI entry points."""
    application = QApplication.instance()
    if application is None:
        return GuiPresentationStatus(False, "QApplication 尚未创建")
    lifecycle = gui_application_lifecycle()
    if lifecycle is not None:
        return lifecycle.presentation_status(parent)
    if QCoreApplication.closingDown():
        return GuiPresentationStatus(False, "QApplication 正在销毁")
    if QThread.currentThread() is not application.thread():
        return GuiPresentationStatus(False, "窗口只能在 GUI 主线程展示")
    if not application.screens() or application.primaryScreen() is None:
        return GuiPresentationStatus(False, "当前没有可用图形屏幕")
    if parent is not None and (
        not isValid(parent) or parent.thread() is not application.thread()
    ):
        return GuiPresentationStatus(False, "父窗口已销毁或不属于 GUI 主线程")
    return GuiPresentationStatus(True)


def begin_gui_shutdown(reason: str = "GUI 正在关闭") -> None:
    lifecycle = gui_application_lifecycle()
    if lifecycle is not None:
        lifecycle.begin_shutdown(reason)


def register_gui_background_thread(thread: QThread, label: str) -> bool:
    """Register a short-lived GUI worker with the process lifecycle owner."""
    application = QApplication.instance()
    if application is None:
        return False
    lifecycle = gui_application_lifecycle()
    if lifecycle is None:
        lifecycle = install_gui_application_lifecycle(application)
    return lifecycle.register_background_thread(thread, label)


def join_gui_background_threads(grace_period_ms: int = 2000) -> None:
    """Join every registered GUI worker before service and Qt teardown."""
    lifecycle = gui_application_lifecycle()
    if lifecycle is not None:
        lifecycle.join_background_threads(grace_period_ms)

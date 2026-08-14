"""Thread-safe GUI notification bridge and dialog presenter."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QWidget

from ..app_dialogs import (
    MessageDialogKind,
    ask_confirmation,
    show_message,
)


class GuiNotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class GuiNotification:
    level: GuiNotificationLevel
    title: str
    message: str
    modal: bool = False


@dataclass(frozen=True, slots=True)
class GuiNotificationState:
    latest: GuiNotification | None
    history: tuple[GuiNotification, ...]


class NotificationDialogPresenter(Protocol):
    def show(self, parent: QWidget, notification: GuiNotification) -> None: ...

    def confirm(self, parent: QWidget, title: str, message: str) -> bool: ...


class AppDialogPresenter:
    """Map application notifications to the shared cross-platform dialog."""

    def show(self, parent: QWidget, notification: GuiNotification) -> None:
        kinds = {
            GuiNotificationLevel.INFO: MessageDialogKind.INFO,
            GuiNotificationLevel.WARNING: MessageDialogKind.WARNING,
            GuiNotificationLevel.ERROR: MessageDialogKind.ERROR,
            GuiNotificationLevel.CRITICAL: MessageDialogKind.CRITICAL,
        }
        show_message(
            parent,
            kinds[notification.level],
            notification.title,
            notification.message,
        )

    def confirm(self, parent: QWidget, title: str, message: str) -> bool:
        return ask_confirmation(parent, title, message)


class GuiNotificationCenter(QObject):
    """Own operational GUI notifications and their observable state."""

    notification_requested = Signal(object)

    def __init__(
        self,
        parent: QWidget,
        *,
        log_sink: Callable[[GuiNotification], None],
        toast_sink: Callable[[GuiNotification], None],
        presenter: NotificationDialogPresenter | None = None,
        history_limit: int = 200,
    ) -> None:
        super().__init__(parent)
        if history_limit <= 0:
            raise ValueError("notification history limit must be positive")
        self._parent = parent
        self._log_sink = log_sink
        self._toast_sink = toast_sink
        self._presenter = presenter or AppDialogPresenter()
        self._history: deque[GuiNotification] = deque(maxlen=history_limit)
        self.notification_requested.connect(self._record_and_present)

    def snapshot(self) -> GuiNotificationState:
        history = tuple(self._history)
        return GuiNotificationState(
            latest=history[-1] if history else None,
            history=history,
        )

    def info(
        self,
        message: str,
        *,
        title: str = "提示",
        modal: bool = False,
    ) -> GuiNotification:
        return self.publish(GuiNotificationLevel.INFO, title, message, modal=modal)

    def warning(
        self,
        message: str,
        *,
        title: str = "警告",
        modal: bool = True,
    ) -> GuiNotification:
        return self.publish(GuiNotificationLevel.WARNING, title, message, modal=modal)

    def error(
        self,
        message: str,
        *,
        title: str = "错误",
        modal: bool = True,
    ) -> GuiNotification:
        return self.publish(GuiNotificationLevel.ERROR, title, message, modal=modal)

    def publish(
        self,
        level: GuiNotificationLevel,
        title: str,
        message: str,
        *,
        modal: bool,
    ) -> GuiNotification:
        normalized = message.strip()
        if not normalized:
            raise ValueError("notification message must not be empty")
        notification = GuiNotification(
            level=level,
            title=title.strip() or "提示",
            message=normalized,
            modal=modal,
        )
        self.notification_requested.emit(notification)
        return notification

    @Slot(object)
    def _record_and_present(self, notification: GuiNotification) -> None:
        self._history.append(notification)
        self._log_sink(notification)
        if notification.modal:
            self._presenter.show(self._parent, notification)
            return
        if notification.level is not GuiNotificationLevel.INFO:
            self._toast_sink(notification)

    def confirm(self, message: str, *, title: str = "确认") -> bool:
        normalized = message.strip()
        if not normalized:
            raise ValueError("confirmation message must not be empty")
        return self._presenter.confirm(self._parent, title, normalized)

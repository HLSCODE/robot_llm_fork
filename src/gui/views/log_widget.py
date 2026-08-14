"""Compact read-only GUI event log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from ..bridges.notifications import GuiNotification, GuiNotificationLevel


class LogFilter(str, Enum):
    ALL = "all"
    ERRORS = "errors"
    WARNINGS = "warnings"


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: str
    level: GuiNotificationLevel
    message: str

    def rendered_text(self) -> str:
        return f"[{self.timestamp}] {self.message}"


class LogWidget(QTextEdit):
    counts_changed = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("运行日志")
        self.setReadOnly(True)
        self.setMinimumHeight(100)
        self._entries: list[LogEntry] = []
        self._filter = LogFilter.ALL
        self._error_count = 0
        self._warning_count = 0

    @property
    def active_filter(self) -> LogFilter:
        return self._filter

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def warning_count(self) -> int:
        return self._warning_count

    def append_notification(self, notification: GuiNotification) -> None:
        self._append_entry(notification.message, notification.level)

    def append_log(self, message: str) -> None:
        """Append a plain informational message from non-notification callers."""
        self._append_entry(message, GuiNotificationLevel.INFO)

    def set_filter(self, log_filter: LogFilter) -> None:
        if self._filter is log_filter:
            return
        self._filter = log_filter
        self._render_entries()

    def clear(self) -> None:
        self._entries.clear()
        self._error_count = 0
        self._warning_count = 0
        super().clear()
        self.counts_changed.emit(0, 0)

    def _append_entry(self, message: str, level: GuiNotificationLevel) -> None:
        normalized = message.strip()
        if not normalized:
            return
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            message=normalized,
        )
        self._entries.append(entry)
        if level in {GuiNotificationLevel.ERROR, GuiNotificationLevel.CRITICAL}:
            self._error_count += 1
        elif level is GuiNotificationLevel.WARNING:
            self._warning_count += 1
        if self._matches_filter(entry):
            self._append_rendered_entry(entry)
        self.counts_changed.emit(self._error_count, self._warning_count)

    def _matches_filter(self, entry: LogEntry) -> bool:
        if self._filter is LogFilter.ALL:
            return True
        if self._filter is LogFilter.ERRORS:
            return entry.level in {
                GuiNotificationLevel.ERROR,
                GuiNotificationLevel.CRITICAL,
            }
        return entry.level is GuiNotificationLevel.WARNING

    def _append_rendered_entry(self, entry: LogEntry) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(entry.rendered_text())
        self.setTextCursor(cursor)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _render_entries(self) -> None:
        visible_entries = (
            entry for entry in self._entries if self._matches_filter(entry)
        )
        self.setPlainText("\n".join(entry.rendered_text() for entry in visible_entries))
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

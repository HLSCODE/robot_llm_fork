"""Compact read-only GUI event log."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QTextEdit, QWidget


class LogWidget(QTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("运行日志")
        self.setReadOnly(True)
        self.setMaximumHeight(120)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append(f"[{timestamp}] {message}")
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

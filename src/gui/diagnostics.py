"""Capture Qt messages without touching widgets or forwarding to stderr."""

from contextlib import contextmanager
from collections.abc import Iterator
import logging
from threading import local

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

from ..observability.crash_diagnostics import CrashDiagnostics


@contextmanager
def capture_qt_messages(diagnostics: CrashDiagnostics) -> Iterator[None]:
    guard = local()

    def handle(kind: QtMsgType, context: QMessageLogContext, message: str) -> None:
        if getattr(guard, "active", False):
            return
        guard.active = True
        try:
            level = {
                QtMsgType.QtDebugMsg: logging.DEBUG,
                QtMsgType.QtInfoMsg: logging.INFO,
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }.get(kind, logging.WARNING)
            diagnostics.logger.log(
                level, "qt.message category=%s file=%s line=%s function=%s message=%s",
                context.category, context.file, context.line, context.function, message,
            )
            if kind == QtMsgType.QtFatalMsg:
                diagnostics.dump_threads()
        finally:
            guard.active = False

    previous = qInstallMessageHandler(handle)
    try:
        yield
    finally:
        qInstallMessageHandler(previous)

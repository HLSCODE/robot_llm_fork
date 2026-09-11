"""Read display geometry without attaching a QScreen wrapper to a widget."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QPoint, QRect, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


def available_screen_geometry(anchor: QPoint | None = None) -> QRect | None:
    """Copy the available rectangle at a global logical point, or on the primary screen.

    Only callable on the GUI thread while the application has a usable display.
    Do not replace this with QWidget.screen() or QWindow.screen(): PySide 6.11.1
    can associate the shared screen wrapper with the temporary caller and delete
    the native screen during cyclic GC. Application-level queries avoid that
    ownership heuristic; callers receive a value, never a borrowed QObject.
    """
    application = QApplication.instance()
    if application is None or QCoreApplication.closingDown():
        return None
    if QThread.currentThread() is not application.thread():
        return None
    screens = [screen for screen in QApplication.screens() if isValid(screen)]
    if not screens:
        return None

    screen = QApplication.screenAt(anchor) if anchor is not None else None
    if screen is None or not isValid(screen) or screen not in screens:
        screen = QApplication.primaryScreen()
    if screen is None or not isValid(screen) or screen not in screens:
        return None
    return QRect(screen.availableGeometry())

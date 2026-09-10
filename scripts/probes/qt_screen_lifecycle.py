"""Exercise native Qt screen creation/shutdown without application services or hardware."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from shiboken6 import delete


def main() -> int:
    application = QGuiApplication([])
    QTimer.singleShot(500, application.quit)
    result = application.exec()
    # This probe owns this application. Explicit cleanup makes the native screen
    # destruction observable before interpreter shutdown, with no screen deletion
    # or ownership changes injected into the production application.
    delete(application)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

"""Keep native recording calls off the GUI thread while awaiting their result."""

from collections.abc import Callable
from typing import Generic, TypeVar

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QLabel, QWidget

from ..app_dialogs import AppDialog
from ..application_lifecycle import register_gui_background_thread

_Result = TypeVar("_Result")


class _RecordingWorker(QThread, Generic[_Result]):
    def __init__(self, callback: Callable[[], _Result]) -> None:
        super().__init__()
        self.callback = callback
        self.result: _Result | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.result = self.callback()
        except Exception as error:
            self.error = error


class _RecordingProgress(AppDialog):
    def reject(self) -> None:
        # Closing a progress window must not abandon a running native SDK call.
        return None


def run_recording_operation(
    parent: QWidget, title: str, callback: Callable[[], _Result],
) -> _Result | None:
    dialog = _RecordingProgress(parent)
    dialog.setWindowTitle(title)
    dialog.content_layout.addWidget(QLabel(title + "，请稍候…", dialog))
    worker = _RecordingWorker(callback)
    if not register_gui_background_thread(worker, title):
        worker.deleteLater()
        dialog.deleteLater()
        raise RuntimeError("界面正在关闭，无法开始录制操作")
    worker.finished.connect(dialog.accept)
    worker.start()
    dialog.exec()
    worker.wait()
    error, result = worker.error, worker.result
    worker.deleteLater()
    dialog.deleteLater()
    if error is not None:
        raise error
    return result

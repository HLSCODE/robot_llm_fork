"""One asynchronously opened window owns a complete recording session."""

from collections.abc import Callable
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QLineEdit, QWidget

from ...application.services import TrajectoryTeachingService
from ...devices.runtime.arm_models import TrajectoryRecordingResult
from ...observability.crash_diagnostics import record_failure, record_stage
from ..app_dialogs import AppDialog, create_dialog_button_box
from ..application_lifecycle import gui_presentation_status, register_gui_background_thread
from .recording_operation import _RecordingWorker


class RecordingPhase(str, Enum):
    STARTING = "starting"
    RECORDING = "recording"
    SAVING = "saving"
    NAMING = "naming"
    CANCELLING = "cancelling"
    ERROR = "error"


class TrajectoryRecordingDialog(AppDialog):
    saved = Signal(str)

    def __init__(
        self, parent: QWidget, service: TrajectoryTeachingService, arm: str,
        create_action: Callable[[str, str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.arm = arm
        self.create_action = create_action
        self.phase = RecordingPhase.STARTING
        self.saved_path: str | None = None
        self.worker: _RecordingWorker[object] | None = None
        self._cancel_requested = False
        self.setWindowTitle("轨迹录制")
        self.setMinimumWidth(430)
        self.message = QLabel(self.content)
        self.message.setWordWrap(True)
        self.editor = QLineEdit(self.content)
        self.editor.setPlaceholderText("轨迹动作名称")
        self.editor.hide()
        self.buttons = create_dialog_button_box(self.content)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.content_layout.addWidget(self.message)
        self.content_layout.addWidget(self.editor)
        self.content_layout.addWidget(self.buttons)

    def start(self) -> None:
        if not gui_presentation_status(self.parentWidget()).allowed:
            self.done(0)
            return
        self.open()
        self._run(RecordingPhase.STARTING, lambda: self.service.start(self.arm))

    def _run(self, phase: RecordingPhase, callback: Callable[[], object]) -> None:
        self.phase = phase
        self.message.setText({
            RecordingPhase.STARTING: "正在开始录制，请稍候…",
            RecordingPhase.SAVING: "正在停止并保存轨迹，请稍候…",
            RecordingPhase.CANCELLING: "正在结束录制，请稍候…",
        }[phase])
        self.buttons.setEnabled(False)
        worker = _RecordingWorker(callback)
        if not register_gui_background_thread(worker, "轨迹录制"):
            worker.deleteLater()
            self._show_error("界面正在关闭，无法开始录制操作")
            return
        self.worker = worker
        worker.finished.connect(self._worker_finished, Qt.ConnectionType.QueuedConnection)
        record_stage("recording.phase", phase=phase.value, dialog_id=self._diagnostic_id)
        worker.start()

    @Slot()
    def _worker_finished(self) -> None:
        worker = self.worker
        if worker is None:
            return
        result, error = worker.result, worker.error
        worker.finished.disconnect(self._worker_finished)
        self.worker = None
        worker.deleteLater()
        if not gui_presentation_status(self.parentWidget()).allowed:
            return
        if error is not None:
            self._show_error(str(error))
            return
        if self.phase is RecordingPhase.CANCELLING:
            super().reject()
        elif self._cancel_requested and self.phase is RecordingPhase.STARTING:
            self._run(RecordingPhase.CANCELLING, self.service.cancel)
        elif self.phase is RecordingPhase.STARTING:
            self.phase = RecordingPhase.RECORDING
            self.message.setText(
                f"{self.arm.upper()} 正在录制。\n{self.service.scope_description}\n"
                "完成后点击确定停止并保存；取消将结束录制。"
            )
            self.buttons.setEnabled(True)
        elif self.phase is RecordingPhase.SAVING:
            if not isinstance(result, TrajectoryRecordingResult):
                self._show_error("保存未返回有效的轨迹结果")
                return
            self.saved_path = str(result.path)
            self.saved.emit(self.saved_path)
            if self._cancel_requested:
                super().reject()
                return
            if self.create_action is None or not self.service.supports_playback:
                super().accept()
                return
            self.phase = RecordingPhase.NAMING
            self.message.setText(f"轨迹已保存（{result.point_count} 点）。\n请输入动作名称：")
            self.editor.setText(f"{self.arm.upper()} {result.path.stem}")
            self.editor.show()
            self.editor.selectAll()
            self.editor.setFocus()
            self.buttons.setEnabled(True)

    def _show_error(self, message: str) -> None:
        self.phase = RecordingPhase.ERROR
        self.message.setText(f"轨迹操作失败：{message}\n点击取消关闭；若录制仍活动，会先尝试停止。")
        self.buttons.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def accept(self) -> None:
        if self.worker is not None:
            return
        if self.phase is RecordingPhase.RECORDING:
            self._run(RecordingPhase.SAVING, self.service.stop_and_save)
        elif self.phase is RecordingPhase.NAMING and self.saved_path and self.create_action:
            name = self.editor.text().strip() or f"{self.arm.upper()} {Path(self.saved_path).stem}"
            try:
                self.create_action(self.arm, self.saved_path, name)
            except Exception as error:
                record_failure("trajectory.create.failed")
                self.message.setText(f"动作创建失败：{error}\n轨迹文件已保留，可以重试。")
                return
            super().accept()

    def reject(self) -> None:
        if self.worker is not None:
            # Never discard a running SDK call. Cancel only after it returns.
            self._cancel_requested = True
            return
        if self.service.active:
            self._run(RecordingPhase.CANCELLING, self.service.cancel)
            return
        super().reject()

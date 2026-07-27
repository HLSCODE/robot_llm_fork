"""Qt adapter for the process-level execution application service."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from ..application import ApplicationServices
from ..core.models import SequenceItem
from ..execution import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionState,
    ExecutionStateError,
)


logger = logging.getLogger(__name__)


class ExecutionBridge(QObject):
    """Translate execution-domain events into Qt signals.

    This object does not own a worker thread or any hardware. The application
    service is the only execution entry and ``ExecutionManager`` owns the only
    sequence worker.
    """

    execution_status_changed = pyqtSignal(str)
    step_started = pyqtSignal(int, object)
    step_completed = pyqtSignal(int, object)
    step_failed = pyqtSignal(int, object, str)
    loop_progress = pyqtSignal(str, int, int)
    execution_completed = pyqtSignal(bool)
    log_message = pyqtSignal(str)

    def __init__(self, services: ApplicationServices) -> None:
        super().__init__()
        self._execution = services.execution

    def execute_sequence_items(
        self,
        items: list[SequenceItem] | list[Any],
        *,
        origin: str,
    ) -> bool:
        """Submit a sequence; completion is reported asynchronously by signals."""
        if not items:
            self.log_message.emit("动作序列为空")
            return False
        try:
            self._execution.start(
                items,
                origin=origin,
                listener=self._on_event,
            )
        except Exception as exc:
            logger.exception("提交动作序列失败")
            self.execution_status_changed.emit("执行失败")
            self.log_message.emit(f"提交执行失败: {exc}")
            return False

        return True

    def pause_execution(self) -> bool:
        try:
            self._execution.pause()
            return True
        except ExecutionStateError as exc:
            self.log_message.emit(f"暂停失败: {exc}")
            return False

    def resume_execution(self) -> bool:
        try:
            self._execution.resume()
            return True
        except ExecutionStateError as exc:
            self.log_message.emit(f"恢复失败: {exc}")
            return False

    def stop_execution(self) -> None:
        try:
            self._execution.cancel()
        except ExecutionStateError:
            return
        self.execution_status_changed.emit("停止中...")
        self.log_message.emit(
            "已发送任务停止请求（非硬件急停，将在当前动作可中断点停止）"
        )

    def get_execution_status(self) -> str:
        state = self._execution.snapshot().state
        return {
            ExecutionState.IDLE: "空闲",
            ExecutionState.STARTING: "启动中",
            ExecutionState.RUNNING: "执行中",
            ExecutionState.PAUSED: "已暂停",
            ExecutionState.CANCELLING: "停止中",
            ExecutionState.SUCCEEDED: "执行完成",
            ExecutionState.FAILED: "执行失败",
            ExecutionState.CANCELLED: "已停止",
        }[state]

    def is_executing(self) -> bool:
        return self._execution.snapshot().active

    def _on_event(self, event: ExecutionEvent) -> None:
        event_type = event.event_type
        if event_type is ExecutionEventType.STARTED:
            self.execution_status_changed.emit("执行中...")
            return
        if event_type is ExecutionEventType.STEP_STARTED:
            self.step_started.emit(event.index or 0, event.item)
            return
        if event_type is ExecutionEventType.STEP_COMPLETED:
            self.step_completed.emit(event.index or 0, event.item)
            return
        if event_type is ExecutionEventType.STEP_FAILED:
            self.step_failed.emit(event.index or 0, event.item, event.message)
            return
        if event_type is ExecutionEventType.LOOP_PROGRESS:
            self.loop_progress.emit(
                str(event.data["loop_uuid"]),
                int(event.data["current_iteration"]),
                int(event.data["total_iterations"]),
            )
            return
        if event_type is ExecutionEventType.LOG:
            self.log_message.emit(event.message)
            return
        if event_type is ExecutionEventType.PAUSED:
            self.execution_status_changed.emit("已暂停")
            return
        if event_type is ExecutionEventType.RESUMED:
            self.execution_status_changed.emit("执行中...")
            return
        if event_type is ExecutionEventType.CANCELLING:
            self.execution_status_changed.emit("停止中...")
            return
        if event_type is ExecutionEventType.SUCCEEDED:
            self._emit_terminal(True, "执行完成", "")
            return
        if event_type is ExecutionEventType.CANCELLED:
            self._emit_terminal(False, "已停止", "任务已停止")
            return
        if event_type is ExecutionEventType.FAILED:
            self._emit_terminal(False, "执行失败", event.message)

    def _emit_terminal(
        self,
        success: bool,
        status: str,
        message: str,
    ) -> None:
        self.execution_status_changed.emit(status)
        if message:
            self.log_message.emit(message)
        self.execution_completed.emit(success)

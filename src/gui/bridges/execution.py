"""Qt adapter for the process-level execution application service."""

from __future__ import annotations

import logging
from threading import Lock, Thread
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from ...application import ApplicationServices
from ...domain.models import SequenceItem
from ...devices import StopMode
from ...execution import (
    ExecutionEvent,
    ExecutionEventType,
)


logger = logging.getLogger(__name__)


class ExecutionBridge(QObject):
    """Translate execution-domain events into Qt signals.

    ``ExecutionManager`` owns the only sequence worker. Safety requests use a
    short-lived dispatch thread so hardware I/O never blocks the Qt UI thread.
    """

    execution_status_changed = pyqtSignal(str)
    step_started = pyqtSignal(int, object)
    step_completed = pyqtSignal(int, object)
    step_failed = pyqtSignal(int, object, str)
    loop_progress = pyqtSignal(str, int, int)
    execution_completed = pyqtSignal(bool)
    log_message = pyqtSignal(str)
    safety_stop_completed = pyqtSignal(object)
    safety_stop_failed = pyqtSignal(str)

    def __init__(self, services: ApplicationServices) -> None:
        super().__init__()
        self._execution = services.execution
        self._safety = services.safety
        self._safety_request_lock = Lock()
        self._safety_request_active = False

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

    def request_safety_stop(self, mode: StopMode) -> bool:
        if mode not in {StopMode.QUICK, StopMode.EMERGENCY}:
            raise ValueError("Qt safety request must be quick or emergency")
        with self._safety_request_lock:
            if self._safety_request_active:
                return False
            self._safety_request_active = True
        thread = Thread(
            target=self._run_safety_stop,
            args=(mode,),
            daemon=True,
            name=f"SafetyStop-{mode.value}",
        )
        try:
            thread.start()
        except Exception:
            with self._safety_request_lock:
                self._safety_request_active = False
            raise
        return True

    def _run_safety_stop(self, mode: StopMode) -> None:
        try:
            report = self._safety.stop(mode)
        except Exception as exc:
            logger.exception("Safety stop dispatch failed: %s", mode.value)
            self.safety_stop_failed.emit(str(exc))
            self.log_message.emit(f"设备停止请求失败: {exc}")
        else:
            self.safety_stop_completed.emit(report)
            outcome = "完成" if report.complete else "未完全停止"
            self.log_message.emit(
                f"{mode.value} 软件停止编排{outcome}；"
                "物理急停回路仍须独立保障"
            )
        finally:
            with self._safety_request_lock:
                self._safety_request_active = False

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

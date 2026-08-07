"""Execution WebSocket controller."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from ...domain.models import (
    ActionDefinition,
    LoopBlock,
    SequenceItem,
    SequenceItemStatus,
)
from ...devices import StopMode
from ...execution import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionState,
)
from ..protocol import CURRENT_WEBSOCKET_REQUEST, WebSocketRequest
from .base import WebSocketHandlerHost

logger = logging.getLogger(__name__)


class ExecutionWebSocketHandler:
    def __init__(self, server: WebSocketHandlerHost) -> None:
        self._server = server

    async def _handle_execute(self, websocket, data: WebSocketRequest) -> None:
        """
        执行动作序列
        请求: {"action": "execute", "sequence": [...]}
        如果 sequence 省略，则执行当前编排的序列
        """
        if self._server._services.execution.snapshot().active:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "已有序列正在执行，请先停止"}
                )
            )
            return

        raw_sequence = data.get("sequence")
        if raw_sequence:
            # 前端传入了序列数据
            try:
                sequence = self._parse_sequence(raw_sequence)
            except Exception as e:
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"序列解析失败: {str(e)}"}
                    )
                )
                return
        else:
            # 执行当前编排的序列
            sequence = list(self._server._services.composition.sequence_entries())

        if not sequence:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "序列为空，请先添加动作"}
                )
            )
            return

        # 重置状态
        total_steps = 0
        for entry in sequence:
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for child in entry.items:
                    child.status = SequenceItemStatus.PENDING
                total_steps += len(entry.items) * entry.repeat_count
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING
                total_steps += 1

        await self._submit_execution(
            websocket,
            sequence,
            origin="websocket",
            message="开始执行",
            steps=total_steps,
        )

    async def _handle_execute_task(self, websocket, data: WebSocketRequest) -> None:
        """
        加载并执行已保存的任务
        请求: {"action": "execute_task", "name": "xxx.workflow.json"}
        """
        if self._server._services.execution.snapshot().active:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "已有序列正在执行，请先停止"}
                )
            )
            return

        task_name = data.get("name", "")
        if not task_name:
            await websocket.send(
                self._server._json_msg({"event": "error", "message": "name 不能为空"})
            )
            return

        try:
            entries = await asyncio.to_thread(
                self._server._services.composition.load_task,
                task_name,
            )
        except (FileNotFoundError, ValueError):
            entries = ()
        if not entries:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
                )
            )
            return

        for entry in entries:
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for child in entry.items:
                    child.status = SequenceItemStatus.PENDING
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING

        total_steps = sum(
            len(e.items) * e.repeat_count if isinstance(e, LoopBlock) else 1
            for e in entries
        )

        await self._submit_execution(
            websocket,
            entries,
            origin="websocket-task",
            message=f"加载任务 '{task_name}'，开始执行",
            steps=total_steps,
        )

    async def _handle_stop(self, websocket, data: WebSocketRequest) -> None:
        """请求协作式停止任务；该接口不会触发设备硬件急停。"""
        if self._server._services.execution.snapshot().active:
            if self._server._ai_execution_pending:
                self._server._execution_had_failure = True  # 人工停止视为未成功完成
            self._server._services.execution.cancel()
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "stopped",
                        "message": "已发送任务停止请求（非硬件急停）",
                    }
                )
            )
        else:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "当前没有正在执行的序列"}
                )
            )

    async def _handle_quick_stop(self, websocket, data: WebSocketRequest) -> None:
        del data
        await self._handle_safety_stop(websocket, StopMode.QUICK)

    async def _handle_emergency_stop(self, websocket, data: WebSocketRequest) -> None:
        del data
        await self._handle_safety_stop(websocket, StopMode.EMERGENCY)

    async def _handle_safety_stop(
        self,
        websocket,
        mode: StopMode,
    ) -> None:
        report = await asyncio.to_thread(self._server._services.safety.stop, mode)
        if report.execution_before.active and self._server._ai_execution_pending:
            self._server._execution_had_failure = True
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "safety_stop_completed",
                    "report": report.to_dict(),
                }
            )
        )

    async def _handle_pause(self, websocket, data: WebSocketRequest) -> None:
        """暂停执行"""
        snapshot = self._server._services.execution.snapshot()
        if snapshot.state is ExecutionState.RUNNING:
            self._server._services.execution.pause()
            await websocket.send(
                self._server._json_msg({"event": "paused", "message": "执行已暂停"})
            )
        else:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "无法暂停：未在执行或已暂停"}
                )
            )

    async def _handle_resume(self, websocket, data: WebSocketRequest) -> None:
        """恢复执行"""
        snapshot = self._server._services.execution.snapshot()
        if snapshot.state is ExecutionState.PAUSED:
            self._server._services.execution.resume()
            await websocket.send(
                self._server._json_msg({"event": "resumed", "message": "执行已恢复"})
            )
        else:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "无法恢复：未处于暂停状态"}
                )
            )

    async def _submit_execution(
        self,
        websocket,
        sequence: list,
        *,
        origin: str,
        message: str,
        steps: int,
    ) -> bool:
        gate_lock = threading.Lock()
        pending_events: list[ExecutionEvent] = []
        events_released = False

        def listener(event: ExecutionEvent) -> None:
            nonlocal events_released
            with gate_lock:
                if not events_released:
                    pending_events.append(event)
                    return
            self._on_execution_event(event)

        try:
            handle = self._server._services.execution.start(
                sequence,
                origin=origin,
                listener=listener,
            )
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": f"提交执行失败: {exc}",
                    }
                )
            )
            return False
        request_context = CURRENT_WEBSOCKET_REQUEST.get()
        if request_context is not None:
            request_context.run_id = handle.run_id
            with self._server._execution_requests_lock:
                self._server._execution_requests[handle.run_id] = (
                    request_context.execution_correlation()
                )
            correlation = request_context.correlation
            self._server._audit(
                client_id=correlation.client_id,
                principal=correlation.principal,
                action=correlation.action,
                request_id=correlation.request_id,
                outcome="accepted",
                run_id=handle.run_id,
            )
            request_context.initial_audit_recorded = True
        await self._server._broadcast(
            {
                "event": "accepted",
                "run_id": handle.run_id,
                "message": message,
                "steps": steps,
            }
        )
        with gate_lock:
            for event in pending_events:
                self._on_execution_event(event)
            pending_events.clear()
            events_released = True
        return True

    def _parse_sequence(self, raw: list) -> list:
        """
        将前端传来的 JSON 数组转换为 SequenceEntry 列表（含 LoopBlock）

        支持三种格式:
        1. 循环块:  {"kind": "loop", "uuid": "...", "items": [...], "repeat_count": N}
        2. 完整格式: {"uuid": "...", "definition": {...}, "status": "PENDING"}
        3. 简化格式: {"name": "...", "type": "MOVE_TO_POINT", "parameters": {...}}
        """
        sequence = []
        for item_data in raw:
            if item_data.get("kind") == "loop":
                loop = LoopBlock.from_dict(item_data)
                for child in loop.items:
                    child.status = SequenceItemStatus.PENDING
                sequence.append(loop)
            elif "definition" in item_data:
                seq_item = SequenceItem.from_dict(item_data)
                seq_item.status = SequenceItemStatus.PENDING
                sequence.append(seq_item)
            else:
                action_def = ActionDefinition.from_dict(
                    {
                        "id": item_data.get("id", ""),
                        "name": item_data.get("name", "未命名动作"),
                        "type": item_data.get("type", ""),
                        "parameters": item_data.get("parameters", {}),
                    }
                )
                seq_item = SequenceItem.from_definition(action_def)
                seq_item.status = SequenceItemStatus.PENDING
                sequence.append(seq_item)

        return sequence

    def _on_execution_event(self, event: ExecutionEvent) -> None:
        """Translate execution-domain events to the WebSocket protocol."""
        event_type = event.event_type
        metadata = self._execution_metadata(event)
        if event_type is ExecutionEventType.STEP_STARTED:
            self._on_step_started(
                event.index or 0,
                event.item,
                event.data,
                metadata,
            )
        elif event_type is ExecutionEventType.STEP_COMPLETED:
            self._on_step_completed(
                event.index or 0,
                event.item,
                metadata,
            )
        elif event_type is ExecutionEventType.STEP_FAILED:
            self._on_step_failed(
                event.index or 0,
                event.item,
                event.message,
                event.data,
                metadata,
            )
        elif event_type is ExecutionEventType.LOOP_PROGRESS:
            self._on_loop_progress(
                str(event.data["loop_uuid"]),
                int(event.data["current_iteration"]),
                int(event.data["total_iterations"]),
                metadata,
            )
        elif event_type is ExecutionEventType.LOG:
            self._on_log(event.message, event.level, metadata)
        elif event_type is ExecutionEventType.FAILED:
            self._server._execution_had_failure = True
            if event.message:
                self._on_log(event.message, "error", metadata)
            self._on_finished(event, metadata)
        elif event_type is ExecutionEventType.CANCELLED:
            self._server._execution_had_failure = True
            self._on_finished(event, metadata)
        elif event_type is ExecutionEventType.SUCCEEDED:
            self._on_finished(event, metadata)

    def _execution_metadata(
        self,
        event: ExecutionEvent,
    ) -> dict[str, Any]:
        with self._server._execution_requests_lock:
            correlation = self._server._execution_requests.get(event.run_id)
        metadata: dict[str, Any] = {
            "run_id": event.run_id,
            "origin": event.origin,
        }
        if correlation is not None:
            metadata.update(
                {
                    "request_id": correlation.request_id,
                    "action": correlation.action,
                }
            )
        return metadata

    def _on_step_started(
        self,
        index: int,
        item: SequenceItem,
        control_policy: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self._server._broadcast_threadsafe(
            {
                "event": "step_started",
                "index": index,
                "name": item.definition.name,
                "status": item.status.value,
                "control_policy": control_policy,
                **metadata,
            }
        )

    def _on_step_completed(
        self,
        index: int,
        item: SequenceItem,
        metadata: dict[str, Any],
    ) -> None:
        self._server._broadcast_threadsafe(
            {
                "event": "step_completed",
                "index": index,
                "name": item.definition.name,
                **metadata,
            }
        )

    def _on_step_failed(
        self,
        index: int,
        item: SequenceItem,
        error: str,
        failure: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self._server._execution_had_failure = True
        self._server._broadcast_threadsafe(
            {
                "event": "step_failed",
                "index": index,
                "name": item.definition.name,
                "error": error,
                "failure": failure,
                **metadata,
            }
        )

    def _on_loop_progress(
        self,
        loop_uuid: str,
        current_iteration: int,
        total_iterations: int,
        metadata: dict[str, Any],
    ) -> None:
        self._server._broadcast_threadsafe(
            {
                "event": "loop_progress",
                "loop_uuid": loop_uuid,
                "current_iteration": current_iteration,
                "total_iterations": total_iterations,
                **metadata,
            }
        )

    def _on_log(
        self,
        message: str,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        log_fn = {"warn": logger.warning, "error": logger.error}.get(level, logger.info)
        log_fn(message)
        self._server._broadcast_threadsafe(
            {
                "event": "log",
                "level": level,
                "message": message,
                **(metadata or {}),
            }
        )

    def _on_finished(
        self,
        event: ExecutionEvent,
        metadata: dict[str, Any],
    ) -> None:
        succeeded = event.event_type is ExecutionEventType.SUCCEEDED
        if self._server._ai_execution_pending:
            self._server._ai_execution_pending = False
            success = not self._server._execution_had_failure
            self._server._broadcast_threadsafe(
                {
                    "event": "ai_execution_finished",
                    "success": success,
                    "message": "AI 序列执行完成" if success else "AI 序列执行失败",
                    **metadata,
                }
            )
        self._server._broadcast_threadsafe(
            {
                "event": "execution_finished",
                "state": event.event_type.value,
                "success": succeeded,
                "error": event.message or None,
                "failure": {
                    "code": event.data.get("code") or None,
                    "operation": event.data.get("operation") or None,
                    "device_id": event.data.get("device_id") or None,
                },
                **metadata,
            }
        )
        with self._server._execution_requests_lock:
            correlation = self._server._execution_requests.pop(
                event.run_id,
                None,
            )
        if correlation is not None:
            self._server._audit(
                client_id=correlation.client_id,
                principal=correlation.principal,
                action=correlation.action,
                request_id=correlation.request_id,
                run_id=event.run_id,
                outcome=event.event_type.value,
                code=event.data.get("code") or None,
            )

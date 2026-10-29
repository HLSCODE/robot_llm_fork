"""Transport-independent saved-workflow execution and durable request receipts."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from ..execution import ExecutionEvent, ExecutionEventType
from ..execution.models import ExecutionAlreadyRunningError, ExecutionStateError
from ..devices import ResourceBusyError

if TYPE_CHECKING:
    from .composition import CompositionService
    from .services import ExecutionService
    from .workflow_compiler import WorkflowCompiler
    from .workflow_preflight import WorkflowPreflightService
    from ..execution.manager import ExecutionHandle, ExecutionListener

logger = logging.getLogger(__name__)
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ACTIVE = {"accepted", "running", "paused", "cancelling"}


class TaskRunJournalPort(Protocol):
    def find_request(self, principal: str, request_id: str) -> dict[str, Any] | None: ...
    def find_run(self, principal: str, run_id: str) -> dict[str, Any] | None: ...
    def reserve(self, principal: str, request_id: str, task: str, run_id: str) -> None: ...
    def update(
        self,
        principal: str,
        request_id: str,
        status: str,
        result: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None: ...


class TaskServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskRequestContext:
    principal: str
    request_id: str
    origin: str


class SavedTaskService:
    """One shared submission gate. Never connects devices or changes the editor.

    Receipts survive restart; nonterminal receipts become unknown, never replayed.
    SQLite connections are short-lived, so application shutdown owns no DB handle.
    """

    def __init__(
        self,
        composition: CompositionService,
        compiler: WorkflowCompiler,
        preflight: WorkflowPreflightService,
        execution: ExecutionService,
        journal: TaskRunJournalPort,
        *,
        allowlist: tuple[str, ...] = (),
    ) -> None:
        self._composition = composition
        self._compiler = compiler
        self._preflight = preflight
        self._execution = execution
        self._journal = journal
        self._lock = RLock()
        self._handles: dict[str, ExecutionHandle] = {}
        self._allowlist = {name.removesuffix(".workflow.json") for name in allowlist}

    def list_tasks(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._composition.list_workflows()
            if not self._allowlist or name.removesuffix(".workflow.json") in self._allowlist
        )

    def run_task(
        self, task: str, context: TaskRequestContext, *, listener: ExecutionListener | None = None
    ) -> dict[str, Any]:
        if not context.principal or not _ID.fullmatch(context.request_id):
            raise TaskServiceError("invalid_request", "需要身份和有效 request_id")
        # Names only: no caller-controlled paths, even within the data directory.
        if (
            not isinstance(task, str)
            or not task.strip()
            or any(c in task for c in ("/", "\\", "\x00"))
        ):
            raise TaskServiceError("invalid_task", "task 必须是已保存任务的名称，不能是路径")
        if self._allowlist and task.removesuffix(".workflow.json") not in self._allowlist:
            raise TaskServiceError("task_forbidden", "该任务不在远程执行白名单中")
        with self._lock:
            previous = self._journal.find_request(context.principal, context.request_id)
            if previous is not None:
                if previous["task"] != task:
                    raise TaskServiceError("request_conflict", "同一 request_id 不能执行不同任务")
                return {**previous, "duplicate": True}
            try:
                document = self._composition.load_workflow(task)
            except FileNotFoundError as error:
                raise TaskServiceError("task_not_found", "已保存任务不存在") from error
            except (ValueError, TypeError) as error:
                raise TaskServiceError("invalid_workflow", str(error)) from error
            try:
                workflow = self._compiler.compile(document)
            except (ValueError, TypeError) as error:
                raise TaskServiceError("invalid_workflow", str(error)) from error
            readiness = self._preflight.check(workflow)
            if not readiness.ready:
                code = (
                    "busy"
                    if any(i.code.value == "execution_active" for i in readiness.issues)
                    else "not_ready"
                )
                raise TaskServiceError(code, "; ".join(i.message for i in readiness.issues))
            provisional = uuid4().hex
            try:
                self._journal.reserve(context.principal, context.request_id, task, provisional)
            except OverflowError as error:
                raise TaskServiceError("history_full", str(error)) from error

            def on_event(event: ExecutionEvent) -> None:
                with self._lock:
                    self._record_event(context, event)
                if listener is not None:
                    listener(event)

            try:
                handle = self._execution.start(
                    workflow.plan, origin=context.origin, listener=on_event
                )
            except Exception as error:
                # Keep the receipt: an ambiguous worker-start failure must not replay.
                self._journal.update(
                    context.principal,
                    context.request_id,
                    "failed",
                    {"code": "submission_failed", "message": str(error)},
                )
                if isinstance(error, (ExecutionAlreadyRunningError, ResourceBusyError)):
                    raise TaskServiceError("busy", str(error)) from error
                raise
            self._handles[handle.run_id] = handle
            logger.info(
                "Saved task accepted: principal=%s request_id=%s run_id=%s task=%s origin=%s",
                context.principal,
                context.request_id,
                handle.run_id,
                task,
                context.origin,
            )
            return {
                "run_id": handle.run_id,
                "status": "accepted",
                "duplicate": False,
                "steps": len(workflow.steps),
            }

    def _record_event(self, context: TaskRequestContext, event: ExecutionEvent) -> None:
        states = {
            ExecutionEventType.ACCEPTED: "accepted",
            ExecutionEventType.STARTED: "running",
            ExecutionEventType.PAUSED: "paused",
            ExecutionEventType.RESUMED: "running",
            ExecutionEventType.CANCELLING: "cancelling",
            ExecutionEventType.SUCCEEDED: "succeeded",
            ExecutionEventType.FAILED: "failed",
            ExecutionEventType.CANCELLED: "cancelled",
        }
        status = states.get(event.event_type)
        if status is None:
            if event.event_type in {
                ExecutionEventType.STEP_STARTED,
                ExecutionEventType.STEP_COMPLETED,
            }:
                definition = getattr(event.item, "definition", None)
                self._journal.update(
                    context.principal,
                    context.request_id,
                    "running",
                    {
                        "progress": {
                            "step_index": event.index,
                            "step_name": getattr(definition, "name", ""),
                            "event": event.event_type.value,
                        },
                    },
                    run_id=event.run_id,
                )
            return
        result = {**event.data, "message": event.message}
        self._journal.update(
            context.principal, context.request_id, status, result, run_id=event.run_id
        )
        if status not in _ACTIVE:
            self._handles.pop(event.run_id, None)

    def get_run(self, run_id: str, principal: str) -> dict[str, Any]:
        with self._lock:
            row = self._journal.find_run(principal, run_id)
            if row is None:
                raise TaskServiceError("run_not_found", "运行不存在或不属于当前身份")
            return row

    def cancel_run(self, run_id: str, principal: str) -> dict[str, Any]:
        with self._lock:
            result = self.get_run(run_id, principal)
            if result["status"] not in _ACTIVE:
                return result
            handle = self._handles.get(run_id)
            if handle is None:
                raise TaskServiceError("run_unknown", "运行状态未知，不能向其他任务发送停止指令")
            try:
                handle.cancel()
            except ExecutionStateError as error:
                raise TaskServiceError("run_not_active", "该任务已结束或不再是当前运行") from error
            return {"run_id": run_id, "status": "cancel_requested"}

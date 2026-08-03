from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from threading import RLock, Thread
import time
from typing import Any, Protocol
from uuid import uuid4

from ..device_runtime import ResourceArbiter, ResourceLease
from ..core.logging_config import log_context
from .action_control import ActionControlPolicy
from .action_handlers import (
    ActionHandlerResult,
    ActionResultCode,
)
from .control import ExecutionControl
from .models import (
    EngineResult,
    ExecutionAlreadyRunningError,
    ExecutionEvent,
    ExecutionEventType,
    ExecutionSnapshot,
    ExecutionState,
    ExecutionStateError,
)


logger = logging.getLogger(__name__)
ExecutionListener = Callable[[ExecutionEvent], None]


@dataclass(frozen=True, slots=True)
class EngineCallbacks:
    on_step_started: Callable[[int, Any, ActionControlPolicy], None]
    on_step_completed: Callable[[int, Any], None]
    on_step_failed: Callable[[int, Any, ActionHandlerResult], None]
    on_loop_progress: Callable[[str, int, int], None]
    on_log: Callable[[str, str], None]


class ExecutionEngine(Protocol):
    def run(
        self,
        sequence: Sequence[Any],
        control: ExecutionControl,
        callbacks: EngineCallbacks,
    ) -> EngineResult: ...


class ExecutionHandle:
    def __init__(self, run_id: str, manager: "ExecutionManager") -> None:
        self.run_id = run_id
        self._manager = manager

    def pause(self) -> None:
        self._manager.pause(self.run_id)

    def resume(self) -> None:
        self._manager.resume(self.run_id)

    def cancel(self) -> None:
        self._manager.cancel(self.run_id)

    def snapshot(self) -> ExecutionSnapshot:
        return self._manager.snapshot(self.run_id)

    def wait(self, timeout: float | None = None) -> ExecutionSnapshot:
        return self._manager.wait(self.run_id, timeout)


class ExecutionManager:
    """Own the single process-level sequence execution."""

    def __init__(
        self,
        engine: ExecutionEngine,
        resource_arbiter: ResourceArbiter,
        execution_resources: Callable[
            [Sequence[Any]],
            tuple[str, ...],
        ],
    ) -> None:
        self._engine = engine
        self._resource_arbiter = resource_arbiter
        self._execution_resources = execution_resources
        self._lock = RLock()
        self._run_id: str | None = None
        self._origin = ""
        self._state = ExecutionState.IDLE
        self._error = ""
        self._error_code = ""
        self._error_operation = ""
        self._error_device_id = ""
        self._error_category = ""
        self._raw_error_code = ""
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._control: ExecutionControl | None = None
        self._thread: Thread | None = None
        self._listener: ExecutionListener | None = None

    def submit(
        self,
        sequence: Sequence[Any],
        *,
        origin: str,
        listener: ExecutionListener | None = None,
    ) -> ExecutionHandle:
        if not sequence:
            raise ValueError("execution sequence must not be empty")

        with self._lock:
            worker_alive = (
                self._thread is not None and self._thread.is_alive()
            )
            if self._snapshot_unlocked().active or worker_alive:
                raise ExecutionAlreadyRunningError(
                    f"run '{self._run_id}' is already {self._state.value}"
                )

            run_id = uuid4().hex
            resources = self._execution_resources(sequence)
            lease = (
                self._resource_arbiter.acquire(
                    owner_id=f"execution:{run_id}",
                    resources=resources,
                )
                if resources
                else None
            )
            control = ExecutionControl()
            self._run_id = run_id
            self._origin = origin
            self._state = ExecutionState.STARTING
            self._error = ""
            self._error_code = ""
            self._error_operation = ""
            self._error_device_id = ""
            self._error_category = ""
            self._raw_error_code = ""
            self._started_at = None
            self._finished_at = None
            self._control = control
            self._listener = listener
            self._thread = Thread(
                target=self._run,
                args=(run_id, tuple(sequence), control, lease),
                daemon=True,
                name=f"ExecutionManager-{run_id[:8]}",
            )
            thread = self._thread

        self._emit(run_id, ExecutionEventType.ACCEPTED)
        try:
            thread.start()
        except Exception:
            if lease is not None:
                lease.release()
            with self._lock:
                self._state = ExecutionState.FAILED
                self._error = "failed to start execution worker"
                self._error_code = ActionResultCode.INTERNAL_ERROR.value
                self._error_operation = "execution.worker.start"
                self._finished_at = time.time()
            raise
        return ExecutionHandle(run_id, self)

    def pause(self, run_id: str | None = None) -> None:
        with self._lock:
            self._assert_current_run(run_id)
            if self._state is not ExecutionState.RUNNING:
                raise ExecutionStateError(
                    f"cannot pause execution in state {self._state.value}"
                )
            control = self._required_control()
            control.pause()
            self._state = ExecutionState.PAUSED
            current_run_id = self._required_run_id()
        self._emit(current_run_id, ExecutionEventType.PAUSED)

    def resume(self, run_id: str | None = None) -> None:
        with self._lock:
            self._assert_current_run(run_id)
            if self._state is not ExecutionState.PAUSED:
                raise ExecutionStateError(
                    f"cannot resume execution in state {self._state.value}"
                )
            control = self._required_control()
            control.resume()
            self._state = ExecutionState.RUNNING
            current_run_id = self._required_run_id()
        self._emit(current_run_id, ExecutionEventType.RESUMED)

    def cancel(self, run_id: str | None = None) -> None:
        with self._lock:
            self._assert_current_run(run_id)
            if not self._snapshot_unlocked().active:
                raise ExecutionStateError("there is no active execution")
            control = self._required_control()
            control.cancel()
            self._state = ExecutionState.CANCELLING
            current_run_id = self._required_run_id()
        self._emit(current_run_id, ExecutionEventType.CANCELLING)

    def snapshot(self, run_id: str | None = None) -> ExecutionSnapshot:
        with self._lock:
            self._assert_current_run(run_id, allow_empty=True)
            return self._snapshot_unlocked()

    def wait(
        self,
        run_id: str | None = None,
        timeout: float | None = None,
    ) -> ExecutionSnapshot:
        with self._lock:
            self._assert_current_run(run_id)
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        return self.snapshot(run_id)

    def _run(
        self,
        run_id: str,
        sequence: Sequence[Any],
        control: ExecutionControl,
        lease: ResourceLease | None,
    ) -> None:
        with log_context(run_id=run_id, operation="execution.run"):
            self._run_with_context(run_id, sequence, control, lease)

    def _run_with_context(
        self,
        run_id: str,
        sequence: Sequence[Any],
        control: ExecutionControl,
        lease: ResourceLease | None,
    ) -> None:
        logger.info("Execution started: origin=%s", self._origin)
        with self._lock:
            if self._run_id != run_id:
                if lease is not None:
                    lease.release()
                return
            self._state = ExecutionState.RUNNING
            self._started_at = time.time()
        self._emit(run_id, ExecutionEventType.STARTED)

        callbacks = EngineCallbacks(
            on_step_started=lambda index, item, policy: self._emit(
                run_id,
                ExecutionEventType.STEP_STARTED,
                index=index,
                item=item,
                data=policy.to_event_data(),
            ),
            on_step_completed=lambda index, item: self._emit(
                run_id,
                ExecutionEventType.STEP_COMPLETED,
                index=index,
                item=item,
            ),
            on_step_failed=lambda index, item, failure: self._emit(
                run_id,
                ExecutionEventType.STEP_FAILED,
                index=index,
                item=item,
                message=failure.message,
                level="error",
                data=failure.to_event_data(),
            ),
            on_loop_progress=lambda loop_uuid, current, total: self._emit(
                run_id,
                ExecutionEventType.LOOP_PROGRESS,
                data={
                    "loop_uuid": loop_uuid,
                    "current_iteration": current,
                    "total_iterations": total,
                },
            ),
            on_log=lambda message, level="info": self._emit(
                run_id,
                ExecutionEventType.LOG,
                message=message,
                level=level,
            ),
        )

        try:
            result = self._engine.run(sequence, control, callbacks)
        except Exception as exc:
            logger.exception("Unhandled execution engine error: run_id=%s", run_id)
            result = EngineResult(
                success=False,
                error=str(exc),
                error_code=ActionResultCode.INTERNAL_ERROR.value,
                error_operation="execution.engine.run",
            )
        finally:
            if lease is not None:
                lease.release()

        if result.cancelled or control.cancel_requested:
            final_state = ExecutionState.CANCELLED
            final_event = ExecutionEventType.CANCELLED
        elif result.success:
            final_state = ExecutionState.SUCCEEDED
            final_event = ExecutionEventType.SUCCEEDED
        else:
            final_state = ExecutionState.FAILED
            final_event = ExecutionEventType.FAILED

        with self._lock:
            if self._run_id == run_id:
                self._state = final_state
                self._error = result.error
                self._error_code = result.error_code
                self._error_operation = result.error_operation
                self._error_device_id = result.error_device_id
                self._error_category = result.error_category
                self._raw_error_code = result.raw_error_code
                self._finished_at = time.time()
        self._emit(
            run_id,
            final_event,
            message=result.error,
            level="error" if final_state is ExecutionState.FAILED else "info",
            data={
                "code": result.error_code,
                "operation": result.error_operation,
                "device_id": result.error_device_id,
                "error_category": result.error_category,
                "raw_error_code": result.raw_error_code,
            },
        )
        logger.info("Execution finished: state=%s", final_state.value)

    def _emit(
        self,
        run_id: str,
        event_type: ExecutionEventType,
        *,
        index: int | None = None,
        item: Any = None,
        message: str = "",
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if self._run_id != run_id:
                return
            listener = self._listener
            origin = self._origin
        if listener is None:
            return

        event = ExecutionEvent(
            run_id=run_id,
            event_type=event_type,
            origin=origin,
            index=index,
            item=item,
            message=message,
            level=level,
            data=data or {},
        )
        try:
            listener(event)
        except Exception:
            logger.exception(
                "Execution event listener failed: run_id=%s event=%s",
                run_id,
                event_type.value,
            )

    def _assert_current_run(
        self,
        run_id: str | None,
        *,
        allow_empty: bool = False,
    ) -> None:
        if self._run_id is None:
            if allow_empty and run_id is None:
                return
            raise ExecutionStateError("there is no execution")
        if run_id is not None and run_id != self._run_id:
            raise ExecutionStateError(
                f"run '{run_id}' is not current run '{self._run_id}'"
            )

    def _snapshot_unlocked(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            run_id=self._run_id,
            state=self._state,
            origin=self._origin,
            error=self._error,
            error_code=self._error_code,
            error_operation=self._error_operation,
            error_device_id=self._error_device_id,
            error_category=self._error_category,
            raw_error_code=self._raw_error_code,
            started_at=self._started_at,
            finished_at=self._finished_at,
        )

    def _required_control(self) -> ExecutionControl:
        if self._control is None:
            raise ExecutionStateError("execution control is unavailable")
        return self._control

    def _required_run_id(self) -> str:
        if self._run_id is None:
            raise ExecutionStateError("there is no execution")
        return self._run_id

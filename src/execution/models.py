from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any


class ExecutionState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutionState.SUCCEEDED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }


class ExecutionEventType(str, Enum):
    ACCEPTED = "accepted"
    STARTED = "started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    LOOP_PROGRESS = "loop_progress"
    PARALLEL_BRANCH_STARTED = "parallel_branch_started"
    PARALLEL_BRANCH_COMPLETED = "parallel_branch_completed"
    PARALLEL_BRANCH_FAILED = "parallel_branch_failed"
    PARALLEL_BRANCH_CANCELLED = "parallel_branch_cancelled"
    LOG = "log"
    PAUSED = "paused"
    RESUMED = "resumed"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    run_id: str | None
    state: ExecutionState
    origin: str = ""
    error: str = ""
    error_code: str = ""
    error_operation: str = ""
    error_device_id: str = ""
    error_category: str = ""
    raw_error_code: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.state in {
            ExecutionState.STARTING,
            ExecutionState.RUNNING,
            ExecutionState.PAUSED,
            ExecutionState.CANCELLING,
        }


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    run_id: str
    event_type: ExecutionEventType
    origin: str
    timestamp: float = field(default_factory=time.time)
    index: int | None = None
    item: Any = None
    message: str = ""
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineResult:
    success: bool
    cancelled: bool = False
    error: str = ""
    error_code: str = ""
    error_operation: str = ""
    error_device_id: str = ""
    error_category: str = ""
    raw_error_code: str = ""


class ExecutionRuntimeError(RuntimeError):
    pass


class ExecutionAlreadyRunningError(ExecutionRuntimeError):
    pass


class ExecutionStateError(ExecutionRuntimeError):
    pass


class ParallelResourceConflictError(ExecutionRuntimeError):
    def __init__(
        self,
        parallel_id: str,
        resource_id: str,
        branch_ids: tuple[str, str],
    ) -> None:
        self.parallel_id = parallel_id
        self.resource_id = resource_id
        self.branch_ids = branch_ids
        super().__init__(
            f"parallel '{parallel_id}' branches {branch_ids[0]!r} and "
            f"{branch_ids[1]!r} both require resource {resource_id!r}"
        )

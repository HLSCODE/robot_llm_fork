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


class ExecutionRuntimeError(RuntimeError):
    pass


class ExecutionAlreadyRunningError(ExecutionRuntimeError):
    pass


class ExecutionStateError(ExecutionRuntimeError):
    pass

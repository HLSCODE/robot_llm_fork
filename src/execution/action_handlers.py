from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import time
from typing import Any, Protocol, TypeVar

from ..core.models import ActionType
from .control import ExecutionControl


ActionLog = Callable[[str, str], None]
ActionParameters = Mapping[str, Any]
_ResultT = TypeVar("_ResultT")


class ActionCancelledError(RuntimeError):
    """Raised at a cooperative cancellation point."""


class ActionTimeoutError(RuntimeError):
    """Raised when an action exceeds its configured deadline."""


class ActionHandlerNotFoundError(LookupError):
    """Raised when no handler is registered for an action type."""


@dataclass(slots=True)
class ActionExecutionContext:
    """Cancellation, pause, and deadline contract shared by every handler.

    A deadline does not terminate an in-flight SDK call. The call stays in the
    execution worker so its resource lease is retained, and the timeout is
    reported at the first checkpoint after the call returns.
    """

    action_name: str
    control: ExecutionControl
    timeout_seconds: float
    log: ActionLog
    _deadline: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("action timeout must be positive")
        self._deadline = time.monotonic() + self.timeout_seconds

    @property
    def cancel_requested(self) -> bool:
        return self.control.cancel_requested

    @property
    def timed_out(self) -> bool:
        return time.monotonic() >= self._deadline

    @property
    def stop_requested(self) -> bool:
        """Return whether cooperative work should stop at its next checkpoint."""
        return self.cancel_requested or self.timed_out

    @property
    def paused(self) -> bool:
        return self.control.paused

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def checkpoint(self) -> None:
        """Apply pause, cancellation, and timeout semantics in one place."""
        if self.cancel_requested:
            raise ActionCancelledError(
                f"action cancelled: {self.action_name}"
            )
        if self.timed_out:
            raise ActionTimeoutError(self._timeout_message())
        if not self.control.wait_if_paused(deadline=self._deadline):
            raise ActionCancelledError(
                f"action cancelled: {self.action_name}"
            )
        if self.timed_out:
            raise ActionTimeoutError(self._timeout_message())

    def sleep(self, seconds: float) -> None:
        """Sleep cooperatively without exceeding the action deadline."""
        if seconds <= 0:
            self.checkpoint()
            return

        self.checkpoint()
        if not self.control.sleep(
            min(seconds, self.remaining_seconds),
            deadline=self._deadline,
        ):
            raise ActionCancelledError(
                f"action cancelled: {self.action_name}"
            )
        self.checkpoint()

    def invoke(
        self,
        operation: str,
        callback: Callable[[], _ResultT],
    ) -> _ResultT:
        """Run one synchronous operation with checkpoints around it.

        This deliberately does not move blocking device I/O to a detached
        thread. A timeout therefore cannot release a resource while the device
        call is still active.
        """
        self.checkpoint()
        result = callback()
        if self.timed_out:
            raise ActionTimeoutError(
                f"{self._timeout_message()}; device operation "
                f"'{operation}' returned after the deadline"
            )
        self.checkpoint()
        return result

    def _timeout_message(self) -> str:
        return (
            f"action timed out after {self.timeout_seconds:g}s: "
            f"{self.action_name}"
        )


class ActionHandler(Protocol):
    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool: ...


class ActionHandlerRegistry:
    """The only ActionType-to-handler dispatch table."""

    def __init__(self) -> None:
        self._handlers: dict[ActionType, ActionHandler] = {}
        self._frozen = False

    def register(
        self,
        action_type: ActionType,
        handler: ActionHandler,
    ) -> None:
        if self._frozen:
            raise RuntimeError("action handler registry is frozen")
        if action_type in self._handlers:
            raise ValueError(
                f"handler already registered for {action_type.value}"
            )
        self._handlers[action_type] = handler

    def validate_complete(self) -> None:
        missing = [
            action_type.value
            for action_type in ActionType
            if action_type not in self._handlers
        ]
        if missing:
            raise ActionHandlerNotFoundError(
                "missing action handlers: " + ", ".join(missing)
            )
        self._frozen = True

    def execute(
        self,
        action_type: ActionType,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        try:
            handler = self._handlers[action_type]
        except KeyError as exc:
            raise ActionHandlerNotFoundError(
                f"no handler registered for {action_type.value}"
            ) from exc

        context.checkpoint()
        result = handler(parameters, context)
        context.checkpoint()
        return result

    @property
    def registered_types(self) -> frozenset[ActionType]:
        return frozenset(self._handlers)


class WaitActionHandler:
    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        wait_seconds = float(parameters.get("wait_seconds", 1.0))
        if wait_seconds <= 0:
            return True

        context.log(f"Waiting: {wait_seconds:.1f}s", "info")
        context.sleep(wait_seconds)
        return True


class InspectActionHandler:
    """Current simulated inspection handler, isolated for later replacement."""

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        sensor_id = parameters.get("Sensor_ID", "")
        threshold = parameters.get("Threshold", 0)
        sensor_timeout = parameters.get("Timeout", 5)
        context.log(
            f"读取传感器 {sensor_id}, 阈值: {threshold}, "
            f"超时: {sensor_timeout}s",
            "info",
        )
        context.sleep(0.8)
        context.log("检测完成 - 结果: 通过", "info")
        return True

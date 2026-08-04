from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Protocol, TypeVar

from ..core.models import ActionType
from ..devices.runtime.errors import normalize_device_error
from ..devices.runtime.models import DeviceErrorCategory
from .action_control import (
    ActionControlPolicy,
    ActionControlPolicyResolver,
)
from .control import ExecutionControl


ActionLog = Callable[[str, str], None]
ActionParameters = Mapping[str, Any]
_ResultT = TypeVar("_ResultT")
logger = logging.getLogger(__name__)


class ActionCancelledError(RuntimeError):
    """Raised at a cooperative cancellation point."""


class ActionTimeoutError(RuntimeError):
    """Raised when an action exceeds its configured deadline."""


class ActionHandlerNotFoundError(LookupError):
    """Raised when no handler is registered for an action type."""


class ActionResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActionResultCode(str, Enum):
    SUCCESS = "success"
    INVALID_PARAMETERS = "invalid_parameters"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    RESOURCE_NOT_FOUND = "resource_not_found"
    DEVICE_UNAVAILABLE = "device_unavailable"
    DEVICE_OPERATION_FAILED = "device_operation_failed"
    TARGET_NOT_REACHED = "target_not_reached"
    OPERATION_REJECTED = "operation_rejected"
    ACTION_TIMEOUT = "action_timeout"
    CONTROL_POLICY_MISMATCH = "control_policy_mismatch"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ActionHandlerResult:
    """Stable outcome returned by every action handler."""

    status: ActionResultStatus
    code: ActionResultCode
    message: str = ""
    operation: str = ""
    device_id: str = ""
    error_category: str = ""
    raw_error_code: str = ""

    def __post_init__(self) -> None:
        if self.status is ActionResultStatus.SUCCEEDED:
            if self.code is not ActionResultCode.SUCCESS:
                raise ValueError(
                    "successful action result must use SUCCESS code"
                )
            return
        if self.code is ActionResultCode.SUCCESS:
            raise ValueError("failed action result cannot use SUCCESS code")
        if not self.message.strip():
            raise ValueError("failed action result must include a message")

    @property
    def successful(self) -> bool:
        return self.status is ActionResultStatus.SUCCEEDED

    @classmethod
    def succeeded(
        cls,
        *,
        message: str = "",
        operation: str = "",
        device_id: str = "",
        error_category: str = "",
        raw_error_code: str = "",
    ) -> ActionHandlerResult:
        return cls(
            status=ActionResultStatus.SUCCEEDED,
            code=ActionResultCode.SUCCESS,
            message=message,
            operation=operation,
            device_id=device_id,
            error_category=error_category,
            raw_error_code=raw_error_code,
        )

    @classmethod
    def failed(
        cls,
        code: ActionResultCode,
        message: str,
        *,
        operation: str = "",
        device_id: str = "",
        error_category: str = "",
        raw_error_code: str = "",
    ) -> ActionHandlerResult:
        if not error_category and device_id:
            error_category = {
                ActionResultCode.DEVICE_UNAVAILABLE: (
                    DeviceErrorCategory.UNAVAILABLE.value
                ),
                ActionResultCode.DEVICE_OPERATION_FAILED: (
                    DeviceErrorCategory.INTERNAL.value
                ),
                ActionResultCode.OPERATION_REJECTED: (
                    DeviceErrorCategory.REJECTED.value
                ),
            }.get(code, "")
        return cls(
            status=ActionResultStatus.FAILED,
            code=code,
            message=message,
            operation=operation,
            device_id=device_id,
            error_category=error_category,
            raw_error_code=raw_error_code,
        )

    def to_event_data(self) -> dict[str, str]:
        return {
            "status": self.status.value,
            "code": self.code.value,
            "operation": self.operation,
            "device_id": self.device_id,
            "error_category": self.error_category,
            "raw_error_code": self.raw_error_code,
        }

    def __bool__(self) -> bool:
        raise TypeError(
            "ActionHandlerResult has no boolean compatibility; "
            "use '.successful'"
        )


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
        try:
            result = callback()
        except Exception:
            # Cancellation or timeout takes precedence when it happened while
            # the external call was in flight; otherwise preserve the original
            # device exception and traceback for the handler to classify.
            self.checkpoint()
            raise
        if self.timed_out:
            raise ActionTimeoutError(
                f"{self._timeout_message()}; device operation "
                f"'{operation}' returned after the deadline"
            )
        self.checkpoint()
        return result

    def success(
        self,
        *,
        message: str = "",
        operation: str = "",
        device_id: str = "",
    ) -> ActionHandlerResult:
        return ActionHandlerResult.succeeded(
            message=message,
            operation=operation,
            device_id=device_id,
        )

    def failure(
        self,
        code: ActionResultCode,
        message: str,
        *,
        operation: str = "",
        device_id: str = "",
        log: bool = True,
        error: Exception | None = None,
    ) -> ActionHandlerResult:
        error_category = ""
        raw_error_code = ""
        if error is not None:
            fallback_category = (
                DeviceErrorCategory.UNAVAILABLE
                if code is ActionResultCode.DEVICE_UNAVAILABLE
                else DeviceErrorCategory.INTERNAL
            )
            normalized = normalize_device_error(
                error,
                device_id=device_id,
                operation=operation,
                fallback_category=fallback_category,
            )
            message = normalized.user_message
            error_category = normalized.category.value
            raw_error_code = normalized.raw_error_code
            if (
                normalized.category is DeviceErrorCategory.REJECTED
                and code is ActionResultCode.DEVICE_OPERATION_FAILED
            ):
                code = ActionResultCode.OPERATION_REJECTED
            logger.error(
                "Device failure: device_id=%s operation=%s category=%s "
                "raw_error_code=%s diagnostic=%s",
                device_id,
                operation,
                error_category,
                raw_error_code,
                normalized.diagnostic_message,
                exc_info=(
                    type(error),
                    error,
                    error.__traceback__,
                ),
            )
        if log:
            self.log(message, "error")
        return ActionHandlerResult.failed(
            code,
            message,
            operation=operation,
            device_id=device_id,
            error_category=error_category,
            raw_error_code=raw_error_code,
        )

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
    ) -> ActionHandlerResult: ...


@dataclass(frozen=True, slots=True)
class _ActionHandlerRegistration:
    handler: ActionHandler
    control_policy: ActionControlPolicyResolver


class ActionHandlerRegistry:
    """The only ActionType-to-handler and control-policy dispatch table."""

    def __init__(self) -> None:
        self._registrations: dict[
            ActionType,
            _ActionHandlerRegistration,
        ] = {}
        self._frozen = False

    def register(
        self,
        action_type: ActionType,
        handler: ActionHandler,
        control_policy: ActionControlPolicyResolver,
    ) -> None:
        if self._frozen:
            raise RuntimeError("action handler registry is frozen")
        if action_type in self._registrations:
            raise ValueError(
                f"handler already registered for {action_type.value}"
            )
        self._registrations[action_type] = _ActionHandlerRegistration(
            handler=handler,
            control_policy=control_policy,
        )

    def validate_complete(self) -> None:
        missing = [
            action_type.value
            for action_type in ActionType
            if action_type not in self._registrations
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
    ) -> ActionHandlerResult:
        registration = self._registration(action_type)

        context.checkpoint()
        result = registration.handler(parameters, context)
        if not isinstance(result, ActionHandlerResult):
            raise TypeError(
                f"handler for {action_type.value} returned "
                f"{type(result).__name__}, expected ActionHandlerResult"
            )
        context.checkpoint()
        return result

    def control_policy(
        self,
        action_type: ActionType,
        parameters: ActionParameters,
    ) -> ActionControlPolicy:
        policy = self._registration(action_type).control_policy(parameters)
        if not isinstance(policy, ActionControlPolicy):
            raise TypeError(
                f"control policy for {action_type.value} returned "
                f"{type(policy).__name__}, expected ActionControlPolicy"
            )
        return policy

    @property
    def registered_types(self) -> frozenset[ActionType]:
        return frozenset(self._registrations)

    def _registration(
        self,
        action_type: ActionType,
    ) -> _ActionHandlerRegistration:
        try:
            return self._registrations[action_type]
        except KeyError as exc:
            raise ActionHandlerNotFoundError(
                f"no handler registered for {action_type.value}"
            ) from exc


class WaitActionHandler:
    _OPERATION = "wait"

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            wait_seconds = float(parameters.get("wait_seconds", 1.0))
        except (TypeError, ValueError) as exc:
            message = f"等待时间无效: {exc}"
            return context.failure(
                ActionResultCode.INVALID_PARAMETERS,
                message,
                operation=self._OPERATION,
            )
        if wait_seconds <= 0:
            return context.success(operation=self._OPERATION)

        context.log(f"Waiting: {wait_seconds:.1f}s", "info")
        context.sleep(wait_seconds)
        return context.success(operation=self._OPERATION)


class InspectActionHandler:
    """Current simulated inspection handler, isolated for later replacement."""

    _OPERATION = "inspect"

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
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
        return context.success(operation=self._OPERATION)

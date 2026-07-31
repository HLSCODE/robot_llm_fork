from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from ..device_runtime import (
    DeviceRuntime,
    DeviceSafeStateResult,
    DeviceStopResult,
    DeviceStopStatus,
    StopMode,
)
from ..device_runtime.ids import ROBOT_SYSTEM
from ..execution import ExecutionSnapshot, ExecutionStateError


class _ExecutionPort(Protocol):
    def cancel(self) -> None: ...
    def snapshot(self) -> ExecutionSnapshot: ...
    def wait(self, timeout: float | None = None) -> ExecutionSnapshot: ...


class _TeleoperationPort(Protocol):
    def stop(self) -> None: ...
    def release_after_safety_stop(self) -> None: ...


class _TrajectoryTeachingPort(Protocol):
    @property
    def active(self) -> bool: ...

    def cancel(self) -> None: ...
    def release_after_safety_stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SafetyStopReport:
    mode: StopMode
    execution_before: ExecutionSnapshot
    execution_after: ExecutionSnapshot
    devices: tuple[DeviceStopResult, ...] = ()
    safe_devices: tuple[DeviceSafeStateResult, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            not self.execution_after.active
            and not self.errors
            and all(result.successful for result in self.devices)
            and all(result.successful for result in self.safe_devices)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "complete": self.complete,
            "execution": {
                "before": self.execution_before.state.value,
                "after": self.execution_after.state.value,
                "run_id": self.execution_after.run_id,
                "error": self.execution_after.error,
                "error_code": self.execution_after.error_code,
                "error_operation": (self.execution_after.error_operation),
                "error_device_id": (self.execution_after.error_device_id),
            },
            "devices": [
                {
                    "device_id": result.device_id,
                    "mode": result.mode.value,
                    "status": result.status.value,
                    "error": result.error,
                }
                for result in self.devices
            ],
            "safe_devices": [
                {
                    "device_id": result.device_id,
                    "status": result.status.value,
                    "error": result.error,
                }
                for result in self.safe_devices
            ],
            "errors": list(self.errors),
        }


class SafetyService:
    """Coordinate task cancellation, sessions, and device-level stops."""

    def __init__(
        self,
        execution: _ExecutionPort,
        runtime: DeviceRuntime,
        teleoperation: _TeleoperationPort,
        trajectory_teaching: _TrajectoryTeachingPort,
        *,
        wait_timeout_seconds: float,
    ) -> None:
        if wait_timeout_seconds <= 0:
            raise ValueError("safety stop wait timeout must be positive")
        self._execution = execution
        self._runtime = runtime
        self._teleoperation = teleoperation
        self._trajectory_teaching = trajectory_teaching
        self._wait_timeout_seconds = wait_timeout_seconds
        self._control_sessions: list[tuple[str, Callable[[], None]]] = []
        self._lock = RLock()

    def register_control_session(
        self,
        name: str,
        close: Callable[[], None],
    ) -> None:
        """Register an application session that must close before shutdown."""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("control session name must not be empty")
        with self._lock:
            self._control_sessions.append((normalized_name, close))

    def stop(
        self,
        mode: StopMode,
        *,
        wait_timeout_seconds: float | None = None,
    ) -> SafetyStopReport:
        if not isinstance(mode, StopMode):
            raise TypeError("mode must be a StopMode")
        timeout = (
            self._wait_timeout_seconds
            if wait_timeout_seconds is None
            else wait_timeout_seconds
        )
        if timeout <= 0:
            raise ValueError("safety stop wait timeout must be positive")

        with self._lock:
            before = self._execution.snapshot()
            errors: list[str] = []
            self._request_execution_cancel(before, errors)

            if mode is StopMode.CONTROLLED:
                devices: tuple[DeviceStopResult, ...] = ()
                self._close_control_sessions(errors)
            else:
                devices = self._runtime.stop_all(mode)
                self._release_sessions_after_device_stop(devices, errors)
            safe_devices = self._runtime.enter_safe_states()

            after = self._wait_for_execution(before, timeout, errors)
            return SafetyStopReport(
                mode=mode,
                execution_before=before,
                execution_after=after,
                devices=devices,
                safe_devices=safe_devices,
                errors=tuple(errors),
            )

    def _request_execution_cancel(
        self,
        before: ExecutionSnapshot,
        errors: list[str],
    ) -> None:
        if not before.active:
            return
        try:
            self._execution.cancel()
        except ExecutionStateError:
            return
        except Exception as exc:
            errors.append(f"execution cancellation failed: {exc}")

    def _close_control_sessions(self, errors: list[str]) -> None:
        self._close_registered_sessions(errors)
        self._try_session_action(
            "teleoperation release",
            self._teleoperation.stop,
            errors,
        )
        self._try_session_action(
            "trajectory teaching cancellation",
            self._trajectory_teaching.cancel,
            errors,
        )

    def _release_sessions_after_device_stop(
        self,
        devices: tuple[DeviceStopResult, ...],
        errors: list[str],
    ) -> None:
        self._close_registered_sessions(errors)
        self._try_session_action(
            "teleoperation release",
            self._teleoperation.release_after_safety_stop,
            errors,
        )
        if not self._trajectory_teaching.active:
            return

        robot_stopped = any(
            result.device_id == ROBOT_SYSTEM
            and result.status is DeviceStopStatus.STOPPED
            for result in devices
        )
        action = (
            self._trajectory_teaching.release_after_safety_stop
            if robot_stopped
            else self._trajectory_teaching.cancel
        )
        self._try_session_action(
            "trajectory teaching release",
            action,
            errors,
        )

    def _close_registered_sessions(self, errors: list[str]) -> None:
        for name, close in self._control_sessions:
            self._try_session_action(
                f"{name} release",
                close,
                errors,
            )

    def _wait_for_execution(
        self,
        before: ExecutionSnapshot,
        timeout: float,
        errors: list[str],
    ) -> ExecutionSnapshot:
        if not before.active:
            return self._execution.snapshot()
        try:
            after = self._execution.wait(timeout)
        except ExecutionStateError:
            after = self._execution.snapshot()
        except Exception as exc:
            errors.append(f"execution wait failed: {exc}")
            return self._execution.snapshot()
        if after.active:
            errors.append(f"execution did not stop within {timeout:g} seconds")
        return after

    @staticmethod
    def _try_session_action(
        name: str,
        action: Callable[[], None],
        errors: list[str],
    ) -> None:
        try:
            action()
        except Exception as exc:
            errors.append(f"{name} failed: {exc}")

"""Explicit controller state for GUI startup initialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import logging
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from ...devices.runtime.ids import BODY_AXIS, MOBILE_BASE, PIPETTE, ROBOT_SYSTEM


if TYPE_CHECKING:
    from ...application import ApplicationServices


logger = logging.getLogger(__name__)


class GuiStartupState(str, Enum):
    NOT_STARTED = "not_started"
    WAITING_FOR_SPEECH = "waiting_for_speech"
    INITIALIZING_HARDWARE = "initializing_hardware"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


class GuiStartupLifecycle:
    """Own valid startup transitions on the Qt application thread."""

    def __init__(self) -> None:
        self._state = GuiStartupState.NOT_STARTED

    @property
    def state(self) -> GuiStartupState:
        return self._state

    def begin(self) -> bool:
        if self._state is not GuiStartupState.NOT_STARTED:
            return False
        self._state = GuiStartupState.WAITING_FOR_SPEECH
        return True

    def begin_hardware_initialization(self) -> bool:
        if self._state is not GuiStartupState.WAITING_FOR_SPEECH:
            return False
        self._state = GuiStartupState.INITIALIZING_HARDWARE
        return True

    def mark_ready(self) -> None:
        self._require_state(GuiStartupState.INITIALIZING_HARDWARE)
        self._state = GuiStartupState.READY

    def mark_failed(self) -> None:
        if self._state not in {
            GuiStartupState.WAITING_FOR_SPEECH,
            GuiStartupState.INITIALIZING_HARDWARE,
        }:
            return
        self._state = GuiStartupState.FAILED

    def close(self) -> None:
        self._state = GuiStartupState.CLOSED

    def _require_state(self, expected: GuiStartupState) -> None:
        if self._state is not expected:
            raise RuntimeError(
                f"GUI startup transition requires {expected.value}, "
                f"current state is {self._state.value}"
            )


@dataclass(frozen=True, slots=True)
class HardwareStartupStepResult:
    device_id: str
    succeeded: bool
    error: str | None = None


class GuiHardwareStartupWorker(QObject):
    """Initialize startup-owned hardware without blocking the Qt event loop."""

    step_started = Signal(str)
    step_completed = Signal(object)
    completed = Signal(object)

    def __init__(
        self,
        services: "ApplicationServices",
        *,
        initialize_mobile_base: bool,
    ) -> None:
        super().__init__()
        self._services = services
        self._initialize_mobile_base = initialize_mobile_base
        self._stop_requested = Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        results: list[HardwareStartupStepResult] = []
        for device_id, operation in self._operations():
            if self._stop_requested.is_set():
                break
            self.step_started.emit(device_id)
            result = self._run_step(device_id, operation)
            results.append(result)
            self.step_completed.emit(result)
        self.completed.emit(tuple(results))

    def _operations(self) -> tuple[tuple[str, Callable[[], object]], ...]:
        operations: list[tuple[str, Callable[[], object]]] = [
            (
                ROBOT_SYSTEM,
                lambda: self._services.devices.initialize(ROBOT_SYSTEM),
            )
        ]
        if self._initialize_mobile_base:
            operations.append(
                (
                    MOBILE_BASE,
                    lambda: self._services.devices.initialize(MOBILE_BASE),
                )
            )
        operations.extend(
            (
                (
                    BODY_AXIS,
                    lambda: self._services.devices.initialize(BODY_AXIS),
                ),
                (
                    PIPETTE,
                    self._services.manual_control.initialize_pipette,
                ),
            )
        )
        return tuple(operations)

    @staticmethod
    def _run_step(
        device_id: str,
        operation: Callable[[], object],
    ) -> HardwareStartupStepResult:
        try:
            result = operation()
        except Exception as exc:
            logger.exception(
                "Hardware startup step failed: device_id=%s, error=%s",
                device_id,
                exc,
            )
            return HardwareStartupStepResult(
                device_id=device_id,
                succeeded=False,
                error=str(exc),
            )
        if device_id == PIPETTE and result is not True:
            logger.warning(
                "Hardware startup step was not confirmed: device_id=%s",
                device_id,
            )
            return HardwareStartupStepResult(
                device_id=device_id,
                succeeded=False,
                error="设备未确认初始化成功",
            )
        return HardwareStartupStepResult(device_id=device_id, succeeded=True)


class GuiAuxiliaryServiceStartupWorker(QObject):
    """Wait for optional service startup outside the Qt application thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, start_services: Callable[[], object]) -> None:
        super().__init__()
        self._start_services = start_services

    @Slot()
    def run(self) -> None:
        try:
            snapshots = self._start_services()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(snapshots)


class GuiAuxiliaryStartupResultReceiver(QObject):
    """Deliver auxiliary startup results on the receiver's Qt thread."""

    def __init__(
        self,
        on_completed: Callable[[object], None],
        on_failed: Callable[[str], None],
        on_thread_finished: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_completed = on_completed
        self._on_failed = on_failed
        self._on_thread_finished = on_thread_finished

    @Slot(object)
    def handle_completed(self, snapshots: object) -> None:
        self._on_completed(snapshots)

    @Slot(str)
    def handle_failed(self, error: str) -> None:
        self._on_failed(error)

    @Slot()
    def handle_thread_finished(self) -> None:
        self._on_thread_finished()

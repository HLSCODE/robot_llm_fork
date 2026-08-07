from __future__ import annotations

from threading import Condition, Event
import time


class ExecutionControl:
    """Thread-safe cooperative pause and cancellation state for one run."""

    def __init__(self, parent: ExecutionControl | None = None) -> None:
        self._cancelled = Event()
        self._pause_condition = Condition()
        self._paused = False
        self._parent = parent

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled.is_set() or bool(
            self._parent is not None and self._parent.cancel_requested
        )

    @property
    def paused(self) -> bool:
        with self._pause_condition:
            return self._paused or bool(
                self._parent is not None and self._parent.paused
            )

    def child(self) -> ExecutionControl:
        """Create a branch-local cancellation scope sharing parent pause/cancel."""
        return ExecutionControl(parent=self)

    def cancel(self) -> None:
        self._cancelled.set()
        with self._pause_condition:
            self._paused = False
            self._pause_condition.notify_all()

    def pause(self) -> None:
        with self._pause_condition:
            if not self._cancelled.is_set():
                self._paused = True

    def resume(self) -> None:
        with self._pause_condition:
            self._paused = False
            self._pause_condition.notify_all()

    def wait_if_paused(
        self,
        poll_seconds: float = 0.1,
        *,
        deadline: float | None = None,
    ) -> bool:
        with self._pause_condition:
            while self.paused and not self.cancel_requested:
                wait_seconds = poll_seconds
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait_seconds = min(wait_seconds, remaining)
                self._pause_condition.wait(timeout=wait_seconds)
        return not self.cancel_requested

    def sleep(
        self,
        seconds: float,
        poll_seconds: float = 0.05,
        *,
        deadline: float | None = None,
    ) -> bool:
        sleep_deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < sleep_deadline:
            if self.cancel_requested or not self.wait_if_paused(
                deadline=deadline
            ):
                return False
            remaining = sleep_deadline - time.monotonic()
            if deadline is not None:
                remaining = min(remaining, deadline - time.monotonic())
            if remaining <= 0:
                break
            self._cancelled.wait(
                timeout=min(poll_seconds, max(0.0, remaining))
            )
        return not self.cancel_requested

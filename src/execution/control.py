from __future__ import annotations

from threading import Condition, Event
import time


class ExecutionControl:
    """Thread-safe cooperative pause and cancellation state for one run."""

    def __init__(self) -> None:
        self._cancelled = Event()
        self._pause_condition = Condition()
        self._paused = False

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled.is_set()

    @property
    def paused(self) -> bool:
        with self._pause_condition:
            return self._paused

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
            while self._paused and not self._cancelled.is_set():
                wait_seconds = poll_seconds
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait_seconds = min(wait_seconds, remaining)
                self._pause_condition.wait(timeout=wait_seconds)
        return not self._cancelled.is_set()

    def sleep(
        self,
        seconds: float,
        poll_seconds: float = 0.05,
        *,
        deadline: float | None = None,
    ) -> bool:
        sleep_deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < sleep_deadline:
            if self._cancelled.is_set() or not self.wait_if_paused(
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
        return not self._cancelled.is_set()

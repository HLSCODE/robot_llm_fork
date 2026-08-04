"""Explicit controller state for GUI startup initialization."""

from __future__ import annotations

from enum import Enum


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

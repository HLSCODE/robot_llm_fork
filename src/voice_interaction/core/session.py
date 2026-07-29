"""
Wake-session state management.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from typing import Any, Dict, List, Optional

from .types import VoiceSessionState


@dataclass
class VoiceSession:
    """Mutable state for one wake-session conversation."""

    state: VoiceSessionState = VoiceSessionState.SLEEPING
    timeout_s: float = 30.0
    last_activity_at: float = field(default_factory=monotonic)
    history: List[Dict[str, Any]] = field(default_factory=list)
    current_task_id: Optional[str] = None
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def wake(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.AWAKE
            self._touch_unlocked()

    def sleep(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.SLEEPING
            self.history.clear()
            self.current_task_id = None
            self._touch_unlocked()

    def pause(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.PAUSED
            self._touch_unlocked()

    def resume(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.AWAKE
            self._touch_unlocked()

    def responding(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.RESPONDING
            self._touch_unlocked()

    def touch(self) -> None:
        with self._lock:
            self._touch_unlocked()

    def is_expired(self) -> bool:
        with self._lock:
            if self.state == VoiceSessionState.SLEEPING:
                return False
            return monotonic() - self.last_activity_at > self.timeout_s

    def add_history(self, role: str, content: str) -> None:
        with self._lock:
            self.history.append({"role": role, "content": content})

    def recent_history(self, max_turns: int) -> List[Dict[str, Any]]:
        """Return the most recent user/assistant conversation window."""
        if max_turns <= 0:
            return []

        with self._lock:
            entries = self.history[-(max_turns * 2):]
            if entries and entries[0].get("role") == "assistant":
                entries = entries[1:]
            return [dict(entry) for entry in entries]

    def _touch_unlocked(self) -> None:
        self.last_activity_at = monotonic()

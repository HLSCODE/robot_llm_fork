"""
Wake-session state management.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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

    def wake(self) -> None:
        self.state = VoiceSessionState.AWAKE
        self.touch()

    def sleep(self) -> None:
        self.state = VoiceSessionState.SLEEPING
        self.history.clear()
        self.current_task_id = None
        self.touch()

    def pause(self) -> None:
        self.state = VoiceSessionState.PAUSED
        self.touch()

    def resume(self) -> None:
        self.state = VoiceSessionState.AWAKE
        self.touch()

    def responding(self) -> None:
        self.state = VoiceSessionState.RESPONDING
        self.touch()

    def touch(self) -> None:
        self.last_activity_at = monotonic()

    def is_expired(self) -> bool:
        if self.state == VoiceSessionState.SLEEPING:
            return False
        return monotonic() - self.last_activity_at > self.timeout_s

    def add_history(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

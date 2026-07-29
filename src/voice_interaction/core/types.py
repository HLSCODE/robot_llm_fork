"""
Common types for wake-session voice interaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Literal, Optional


class VoiceSessionState(str, Enum):
    """Runtime state of a wake-session interaction."""

    SLEEPING = "sleeping"
    AWAKE = "awake"
    RESPONDING = "responding"
    PAUSED = "paused"


VoiceEventType = Literal[
    "session_started",
    "session_ended",
    "session_paused",
    "session_resumed",
    "speech_runtime_started",
    "speech_runtime_stopped",
    "listening_started",
    "speech_started",
    "asr_started",
    "asr_result",
    "wake_welcome_requested",
    "ignored",
    "intent",
    "text_delta",
    "audio_delta",
    "command_preview",
    "vision_started",
    "error",
    "done",
]


@dataclass
class VoiceEvent:
    """Unified output event for GUI, WebSocket, and tests."""

    type: VoiceEventType
    text: str = ""
    text_delta: str = ""
    audio_data: Optional[str] = None
    intent: Optional[Dict[str, Any]] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "text_delta": self.text_delta,
            "audio_data": self.audio_data,
            "intent": self.intent,
            "data": self.data,
        }

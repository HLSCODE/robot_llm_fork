"""
Wake-session voice interaction orchestration.

The first implementation supports manual text input as a stand-in for ASR.
Wake word, ASR, and TTS integrations can be attached later as adapters.
"""
from .adapters import CameraCaptureError, CamerasModuleProvider
from .controller import VoiceInteractionController
from .session import VoiceSession
from .types import VoiceEvent, VoiceSessionState

__all__ = [
    "CamerasModuleProvider",
    "CameraCaptureError",
    "VoiceEvent",
    "VoiceSession",
    "VoiceSessionState",
    "VoiceInteractionController",
]

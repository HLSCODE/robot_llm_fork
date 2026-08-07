"""
Wake-session voice interaction orchestration.
"""
from typing import Any

from .adapters import CameraCaptureError, CamerasModuleProvider
from .core import WakeFeedback, VoiceEvent, VoiceInteractionController, VoiceSession, VoiceSessionState

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ASREngine": (".speech", "ASREngine"),
    "AudioCapture": (".speech", "AudioCapture"),
    "AudioOutputGate": (".speech", "AudioOutputGate"),
    "DummyWakeWordEngine": (".speech", "DummyWakeWordEngine"),
    "FunASRConfig": (".speech", "FunASRConfig"),
    "FunASRRecognizer": (".speech", "FunASRRecognizer"),
    "FunASRVAD": (".speech", "FunASRVAD"),
    "FunASRVADConfig": (".speech", "FunASRVADConfig"),
    "OpenWakeWordEngine": (".speech", "OpenWakeWordEngine"),
    "SherpaOnnxWakeWordEngine": (".speech", "SherpaOnnxWakeWordEngine"),
    "VADDetector": (".speech", "VADDetector"),
    "VoiceAudioConfig": (".speech", "VoiceAudioConfig"),
    "VoiceSpeechRuntime": (".speech", "VoiceSpeechRuntime"),
    "VoiceSpeechRuntimeConfig": (".speech", "VoiceSpeechRuntimeConfig"),
    "WakeWordEngine": (".speech", "WakeWordEngine"),
    "build_voice_speech_runtime": (".speech", "build_voice_speech_runtime"),
    "list_input_devices": (".speech", "list_input_devices"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

__all__ = [
    "ASREngine",
    "AudioCapture",
    "AudioOutputGate",
    "CamerasModuleProvider",
    "CameraCaptureError",
    "DummyWakeWordEngine",
    "FunASRConfig",
    "FunASRRecognizer",
    "FunASRVAD",
    "FunASRVADConfig",
    "OpenWakeWordEngine",
    "SherpaOnnxWakeWordEngine",
    "VADDetector",
    "VoiceAudioConfig",
    "VoiceEvent",
    "VoiceSpeechRuntime",
    "VoiceSpeechRuntimeConfig",
    "VoiceSession",
    "VoiceSessionState",
    "VoiceInteractionController",
    "WakeFeedback",
    "WakeWordEngine",
    "build_voice_speech_runtime",
    "list_input_devices",
]

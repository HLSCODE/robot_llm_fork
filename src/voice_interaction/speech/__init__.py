"""
Speech input pipeline: microphone, wake word, VAD, ASR, and runtime.
"""
_LAZY_EXPORTS = {
    "ASREngine": (".asr", "ASREngine"),
    "AudioCapture": (".audio", "AudioCapture"),
    "DummyWakeWordEngine": (".wake_word", "DummyWakeWordEngine"),
    "FunASRConfig": (".asr", "FunASRConfig"),
    "FunASRRecognizer": (".asr", "FunASRRecognizer"),
    "FunASRVAD": (".vad", "FunASRVAD"),
    "FunASRVADConfig": (".vad", "FunASRVADConfig"),
    "OpenWakeWordEngine": (".wake_word", "OpenWakeWordEngine"),
    "AudioOutputGate": (".output_gate", "AudioOutputGate"),
    "SherpaOnnxWakeWordEngine": (".wake_word", "SherpaOnnxWakeWordEngine"),
    "VADDetector": (".vad", "VADDetector"),
    "VoiceAudioConfig": (".audio", "VoiceAudioConfig"),
    "VoiceSpeechRuntime": (".runtime", "VoiceSpeechRuntime"),
    "VoiceSpeechRuntimeConfig": (".runtime", "VoiceSpeechRuntimeConfig"),
    "WakeWordEngine": (".wake_word", "WakeWordEngine"),
    "build_voice_speech_runtime": (".runtime", "build_voice_speech_runtime"),
    "list_input_devices": (".audio", "list_input_devices"),
}


def __getattr__(name: str):
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
    "DummyWakeWordEngine",
    "FunASRConfig",
    "FunASRRecognizer",
    "FunASRVAD",
    "FunASRVADConfig",
    "OpenWakeWordEngine",
    "SherpaOnnxWakeWordEngine",
    "VADDetector",
    "VoiceAudioConfig",
    "VoiceSpeechRuntime",
    "VoiceSpeechRuntimeConfig",
    "WakeWordEngine",
    "build_voice_speech_runtime",
    "list_input_devices",
]

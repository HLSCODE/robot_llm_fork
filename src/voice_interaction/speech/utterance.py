"""
Utterance buffering and endpoint detection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio import rms


@dataclass(frozen=True)
class UtteranceEndpointConfig:
    min_utterance_ms: int = 500
    max_utterance_ms: int = 30_000
    end_silence_ms: int = 800
    silence_rms_threshold: float = 0.01


class UtteranceBuffer:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []

    def clear(self) -> None:
        self._chunks.clear()

    def append(self, chunk: np.ndarray) -> None:
        audio = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if audio.size:
            self._chunks.append(audio.copy())

    def get_audio(self) -> np.ndarray:
        if not self._chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(self._chunks).astype(np.float32, copy=False)

    @property
    def samples(self) -> int:
        return sum(chunk.size for chunk in self._chunks)


class UtteranceEndpoint:
    """Combines VAD endpoints with conservative timing fallbacks."""

    def __init__(
        self,
        config: UtteranceEndpointConfig,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.config = config
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_start_at = 0.0
        self.last_voice_at = 0.0

    def observe(self, chunk: np.ndarray, vad_result: dict[str, object]) -> tuple[bool, str]:
        now = self._clock()
        speech_started = bool(vad_result.get("speech_started"))
        speech_ended = bool(vad_result.get("speech_ended"))
        has_energy = rms(chunk) >= self.config.silence_rms_threshold

        if speech_started and not self.in_speech:
            self.in_speech = True
            self.speech_start_at = now
            self.last_voice_at = now
            return False, "started"

        if not self.in_speech:
            return False, "idle"

        if speech_started or has_energy:
            self.last_voice_at = now

        duration_ms = (now - self.speech_start_at) * 1000
        silence_ms = (now - self.last_voice_at) * 1000

        if duration_ms >= self.config.max_utterance_ms:
            self.in_speech = False
            return True, "max_duration"

        if speech_ended:
            self.in_speech = False
            return duration_ms >= self.config.min_utterance_ms, "vad_end"

        if silence_ms >= self.config.end_silence_ms:
            self.in_speech = False
            return duration_ms >= self.config.min_utterance_ms, "silence"

        return False, "speaking"

    def current_duration_ms(self) -> float:
        if not self.in_speech:
            return 0.0
        return (self._clock() - self.speech_start_at) * 1000

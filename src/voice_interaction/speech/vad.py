"""
Voice activity detection adapters for speech endpointing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass

import numpy as np

from .model_output import suppress_output


@dataclass(frozen=True)
class FunASRVADConfig:
    model: str = "fsmn-vad"
    chunk_size_ms: int = 200
    suppress_model_output: bool = True


@dataclass(frozen=True)
class VADResult:
    speech_started: bool
    speech_ended: bool
    raw: list[list[int]]

    def as_dict(self) -> dict[str, object]:
        return {
            "speech_started": self.speech_started,
            "speech_ended": self.speech_ended,
            "raw": self.raw,
        }


class VADDetector(ABC):
    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:
        raise NotImplementedError


class FunASRVAD(VADDetector):
    def __init__(
        self,
        *,
        model: str = "fsmn-vad",
        chunk_size_ms: int = 200,
        suppress_model_output: bool = True,
    ) -> None:
        try:
            from funasr import AutoModel
        except ImportError as exc:  # pragma: no cover - optional local model path
            raise RuntimeError(
                "FunASR VAD requires funasr and torch. "
                "Install the voice dependencies before enabling ASR."
            ) from exc

        self.chunk_size_ms = chunk_size_ms
        self.suppress_model_output = suppress_model_output
        kwargs = {
            "model": model,
            "disable_pbar": suppress_model_output,
            "disable_update": suppress_model_output,
        }
        output_context = suppress_output() if suppress_model_output else nullcontext()
        with output_context:
            self._model = AutoModel(**kwargs)
        self._cache: dict[str, object] = {}

    @classmethod
    def from_config(cls, config: FunASRVADConfig) -> "FunASRVAD":
        return cls(
            model=config.model,
            chunk_size_ms=config.chunk_size_ms,
            suppress_model_output=config.suppress_model_output,
        )

    def reset(self) -> None:
        self._cache = {}

    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:
        if sample_rate != 16_000:
            raise ValueError("FunASR fsmn-vad expects 16 kHz audio")

        audio = np.asarray(audio_float32, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return VADResult(False, False, []).as_dict()

        result = self._model.generate(
            input=audio,
            cache=self._cache,
            is_final=False,
            chunk_size=self.chunk_size_ms,
            disable_pbar=self.suppress_model_output,
        )
        values = _extract_vad_values(result)
        speech_started = any(item[0] != -1 for item in values if len(item) == 2)
        speech_ended = any(item[1] != -1 for item in values if len(item) == 2)
        return VADResult(speech_started, speech_ended, values).as_dict()


def _extract_vad_values(result: object) -> list[list[int]]:
    if not result or not isinstance(result, list):
        return []

    first = result[0]
    if not isinstance(first, dict):
        return []

    raw_values = first.get("value", [])
    values: list[list[int]] = []
    if not isinstance(raw_values, list):
        return values

    for item in raw_values:
        if isinstance(item, list) and len(item) == 2:
            values.append([int(item[0]), int(item[1])])
    return values

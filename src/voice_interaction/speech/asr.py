"""
ASR adapters used by the voice interaction runtime.
"""
from __future__ import annotations

import os
import tempfile
import wave
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass

import numpy as np

from .audio import float32_to_int16
from .model_output import suppress_output


@dataclass(frozen=True)
class FunASRConfig:
    model: str = "iic/SenseVoiceSmall"
    punc_model: str | None = "ct-punc"
    device: str | None = None
    batch_size_s: int = 60
    suppress_model_output: bool = True


class ASREngine(ABC):
    @abstractmethod
    def transcribe(self, audio_float32: np.ndarray, sample_rate: int) -> str:
        raise NotImplementedError


class FunASRRecognizer(ASREngine):
    """FunASR offline recognizer for one complete utterance."""

    def __init__(
        self,
        *,
        model: str = "iic/SenseVoiceSmall",
        punc_model: str | None = "ct-punc",
        device: str | None = None,
        batch_size_s: int = 60,
        suppress_model_output: bool = True,
    ) -> None:
        try:
            from funasr import AutoModel
        except ImportError as exc:  # pragma: no cover - optional local model path
            raise RuntimeError(
                "FunASR ASR requires funasr and torch. "
                "Install the voice dependencies before enabling ASR."
            ) from exc

        kwargs: dict[str, object] = {"model": model}
        if punc_model:
            kwargs["punc_model"] = punc_model
        if device:
            kwargs["device"] = device
        if suppress_model_output:
            kwargs["disable_pbar"] = True
            kwargs["disable_update"] = True

        output_context = suppress_output() if suppress_model_output else nullcontext()
        with output_context:
            self._model = AutoModel(**kwargs)
        self.batch_size_s = batch_size_s
        self.suppress_model_output = suppress_model_output

    @classmethod
    def from_config(cls, config: FunASRConfig) -> "FunASRRecognizer":
        return cls(
            model=config.model,
            punc_model=config.punc_model,
            device=config.device,
            batch_size_s=config.batch_size_s,
            suppress_model_output=config.suppress_model_output,
        )

    def transcribe(self, audio_float32: np.ndarray, sample_rate: int) -> str:
        audio = np.asarray(audio_float32, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return ""

        wav_path = _write_temp_wav(audio, sample_rate)
        try:
            output_context = suppress_output() if self.suppress_model_output else nullcontext()
            with output_context:
                result = self._model.generate(
                    input=wav_path,
                    batch_size_s=self.batch_size_s,
                    disable_pbar=self.suppress_model_output,
                )
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

        return _extract_text(result)


def _write_temp_wav(audio_float32: np.ndarray, sample_rate: int) -> str:
    fd, path = tempfile.mkstemp(prefix="robot_voice_", suffix=".wav")
    os.close(fd)
    pcm = float32_to_int16(audio_float32)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return path


def _extract_text(result: object) -> str:
    if not result:
        return ""

    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text.strip())
        return " ".join(part for part in parts if part).strip()

    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()

    return ""

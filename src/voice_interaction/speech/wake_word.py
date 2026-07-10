"""
Wake word adapters used by the voice interaction runtime.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import float32_to_int16


@dataclass(frozen=True)
class WakeWordResult:
    triggered: bool
    keyword: str | None = None
    score: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "triggered": self.triggered,
            "keyword": self.keyword,
            "score": self.score,
        }


class WakeWordEngine(ABC):
    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:
        raise NotImplementedError


class DummyWakeWordEngine(WakeWordEngine):
    """Debug wake engine controlled by tests or development UI."""

    def __init__(self, *, auto_trigger: bool = False) -> None:
        self.auto_trigger = auto_trigger
        self._triggered = False

    def trigger(self) -> None:
        self._triggered = True

    def reset(self) -> None:
        self._triggered = False

    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:  # noqa: ARG002
        if self.auto_trigger or self._triggered:
            self._triggered = False
            return WakeWordResult(True, "dummy", 1.0).as_dict()
        return WakeWordResult(False).as_dict()


class SherpaOnnxWakeWordEngine(WakeWordEngine):
    """sherpa-onnx keyword spotting wrapper."""

    def __init__(
        self,
        *,
        tokens: str | Path,
        encoder: str | Path,
        decoder: str | Path,
        joiner: str | Path,
        keywords_file: str | Path,
        provider: str = "cpu",
        num_threads: int = 1,
        max_active_paths: int = 4,
        keywords_score: float = 1.5,
        keywords_threshold: float = 0.35,
    ) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "sherpa-onnx is required for wake word detection. "
                "Install the KWS dependencies before enabling wake words."
            ) from exc

        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(tokens),
                encoder=str(encoder),
                decoder=str(decoder),
                joiner=str(joiner),
                num_threads=num_threads,
                max_active_paths=max_active_paths,
                keywords_file=str(keywords_file),
                keywords_score=keywords_score,
                keywords_threshold=keywords_threshold,
                provider=provider,
            )
        except Exception as exc:
            message = str(exc)
            if "requested API version" in message and "ORT Version" in message:
                raise RuntimeError(
                    "Sherpa KWS 模型与当前 sherpa-onnx/ONNX Runtime 不兼容。"
                    "可以升级 sherpa-onnx/ORT 构建，或改用 wenetspeech 2024 中文模型。"
                ) from exc
            raise
        self._stream = self._spotter.create_stream()

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()

    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:
        audio = np.asarray(audio_float32, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return WakeWordResult(False).as_dict()

        self._stream.accept_waveform(sample_rate, audio)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)

        result = self._spotter.get_result(self._stream)
        keyword = _extract_keyword(result)
        if not keyword:
            return WakeWordResult(False).as_dict()

        self.reset()
        return WakeWordResult(True, keyword=keyword, score=1.0).as_dict()


class OpenWakeWordEngine(WakeWordEngine):
    """Optional openWakeWord backend, mainly useful for English wake words."""

    def __init__(
        self,
        *,
        model_paths: list[str | Path] | None = None,
        threshold: float = 0.6,
    ) -> None:
        try:
            from openwakeword.model import Model
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "openWakeWord is not installed. It depends on tflite-runtime, "
                "which does not provide Python 3.12+ wheels; use sherpa KWS "
                "for this project unless you manage that runtime separately."
            ) from exc

        paths = [str(path) for path in model_paths] if model_paths else None
        self.threshold = threshold
        self._model = Model(wakeword_models=paths)

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()

    def accept_audio(self, audio_float32: np.ndarray, sample_rate: int) -> dict[str, object]:
        if sample_rate != 16_000:
            raise ValueError("openWakeWord expects 16 kHz audio")

        prediction = self._model.predict(float32_to_int16(audio_float32))
        if not prediction:
            return WakeWordResult(False).as_dict()

        keyword, score = max(prediction.items(), key=lambda item: float(item[1]))
        score = float(score)
        return WakeWordResult(
            triggered=score >= self.threshold,
            keyword=keyword if score >= self.threshold else None,
            score=score,
        ).as_dict()


def _extract_keyword(result: object) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        return result.strip() or None

    for attr in ("keyword", "text"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(result, dict):
        for key in ("keyword", "text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None

"""
Microphone capture and audio conversion helpers for voice interaction.
"""
from __future__ import annotations

import queue
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import numpy as np


@dataclass(frozen=True)
class VoiceAudioConfig:
    """Audio input settings shared by wake word, VAD, and ASR."""

    sample_rate: int = 16_000
    channels: int = 1
    audio_block_ms: int = 100
    queue_size: int = 300
    latency: float | str | None = "high"
    device: int | str | None = None
    show_status: bool = False

    @property
    def block_samples(self) -> int:
        return int(self.sample_rate * self.audio_block_ms / 1000)


class AudioCapture:
    """Single microphone input stream shared by KWS, VAD, and ASR buffering."""

    def __init__(self, config: VoiceAudioConfig) -> None:
        self.config = config
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=config.queue_size)
        self._stream: AbstractContextManager[Any] | None = None

    def __enter__(self) -> Self:
        try:
            import sounddevice as sd
        except ImportError as exc:  # pragma: no cover - depends on local audio env
            raise RuntimeError(
                "sounddevice is required for microphone capture. "
                "Install the voice dependencies before enabling ASR."
            ) from exc

        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype="float32",
            blocksize=self.config.block_samples,
            device=self.config.device,
            latency=self.config.latency,
            callback=self._callback,
        )
        self._stream.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stream is not None:
            self._stream.__exit__(exc_type, exc, traceback)
            self._stream = None

    def read(self, timeout: float | None = None) -> np.ndarray:
        return self._queue.get(timeout=timeout)

    def chunks(self) -> Iterator[np.ndarray]:
        while True:
            yield self.read()

    def clear(self) -> int:
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
                cleared += 1
            except queue.Empty:
                return cleared

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        if status and self.config.show_status:
            print(f"Audio status: {status}")

        chunk = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(chunk)


def list_input_devices() -> str:
    """Return sounddevice input/output device information as text."""
    try:
        import sounddevice as sd
    except ImportError as exc:  # pragma: no cover - depends on local audio env
        raise RuntimeError(
            "sounddevice is required to list audio devices. "
            "Install the voice dependencies before using microphone input."
        ) from exc

    return str(sd.query_devices())


def ensure_float32_mono(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio)
    original_dtype = data.dtype
    if data.ndim > 1:
        data = data.mean(axis=1)

    if data.dtype == np.float32:
        return data.reshape(-1)

    if np.issubdtype(original_dtype, np.integer):
        info = np.iinfo(original_dtype)
        scale = max(abs(info.min), info.max)
        return (data.astype(np.float32) / scale).reshape(-1)

    return data.astype(np.float32).reshape(-1)


def float32_to_int16(audio: np.ndarray) -> np.ndarray:
    data = ensure_float32_mono(audio)
    data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
    data = np.clip(data, -1.0, 1.0)
    return (data * 32767.0).astype(np.int16)


def duration_ms(audio: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return np.asarray(audio).size / sample_rate * 1000.0


def rms(audio: np.ndarray) -> float:
    data = ensure_float32_mono(audio)
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(data, dtype=np.float32))))

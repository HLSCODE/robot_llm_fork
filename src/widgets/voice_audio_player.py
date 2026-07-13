"""
Qt audio playback helper for MiniCPM TTS chunks.

MiniCPM Realtime returns base64 encoded float32 PCM at 24 kHz mono. This
player feeds those chunks to QAudioSink and falls back to int16 PCM if the
default output device does not support float samples.
"""
from __future__ import annotations

import array
import base64
import logging
import sys
from collections import deque
from typing import Deque, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..voice_interaction.speech.output_gate import AudioOutputGate

try:
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
except Exception as exc:  # pragma: no cover - depends on local Qt install
    QAudioFormat = None
    QAudioSink = None
    QMediaDevices = None
    _QT_MULTIMEDIA_IMPORT_ERROR: Optional[Exception] = exc
else:
    _QT_MULTIMEDIA_IMPORT_ERROR = None


logger = logging.getLogger(__name__)


class VoiceAudioPlayer(QObject):
    """Play base64 float32 PCM chunks through the default Qt audio device."""

    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        sample_rate: int = 24000,
        channel_count: int = 1,
        output_gate: Optional[AudioOutputGate] = None,
    ) -> None:
        super().__init__(parent)
        self._sample_rate = sample_rate
        self._channel_count = channel_count
        self._queue: Deque[bytes] = deque()
        self._sink = None
        self._audio_io = None
        self._format = None
        self._sample_format = "float32"
        self._reported_unavailable = False
        self._output_gate = output_gate or AudioOutputGate()

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(10)
        self._flush_timer.timeout.connect(self._flush)

    def stop(self) -> None:
        """Stop playback and discard pending chunks."""
        self._output_gate.end_playback()
        self._flush_timer.stop()
        self._queue.clear()
        self._audio_io = None
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None

    def enqueue_base64(self, audio_data: str) -> bool:
        """Decode one base64 PCM chunk and enqueue it for playback."""
        if not audio_data:
            return False
        if not self._ensure_started():
            return False

        try:
            raw = self._decode_base64(audio_data)
            playable = self._prepare_audio_bytes(raw)
        except Exception as exc:
            logger.warning("语音数据解码失败: %s", exc)
            self.error_occurred.emit(f"语音数据解码失败: {exc}")
            return False

        if not playable:
            return False

        self._queue.append(playable)
        self._flush()
        if self._queue:
            self._flush_timer.start()
        return True

    def _ensure_started(self) -> bool:
        if self._sink is not None and self._audio_io is not None:
            return True

        if QAudioFormat is None or QAudioSink is None or QMediaDevices is None:
            if not self._reported_unavailable:
                self._reported_unavailable = True
                message = f"QtMultimedia 不可用，无法播放语音: {_QT_MULTIMEDIA_IMPORT_ERROR}"
                logger.warning(message)
                self.error_occurred.emit(message)
            return False

        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.error_occurred.emit("未找到可用的音频输出设备")
            return False

        audio_format = self._build_format(QAudioFormat.SampleFormat.Float)
        self._sample_format = "float32"
        if not device.isFormatSupported(audio_format):
            audio_format = self._build_format(QAudioFormat.SampleFormat.Int16)
            self._sample_format = "int16"

        if not device.isFormatSupported(audio_format):
            self.error_occurred.emit("默认音频设备不支持 24 kHz 单声道 PCM 播放")
            return False

        self._format = audio_format
        self._sink = QAudioSink(device, audio_format, self)
        self._sink.setBufferSize(self._sample_rate * self._channel_count * 4)
        self._audio_io = self._sink.start()
        if self._audio_io is None:
            self.error_occurred.emit("音频输出启动失败")
            self.stop()
            return False

        logger.info("语音播放已启动: %s PCM, %d Hz", self._sample_format, self._sample_rate)
        return True

    def _build_format(self, sample_format) -> QAudioFormat:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self._sample_rate)
        audio_format.setChannelCount(self._channel_count)
        audio_format.setSampleFormat(sample_format)
        return audio_format

    def _flush(self) -> None:
        if self._sink is None or self._audio_io is None:
            self._flush_timer.stop()
            return

        while self._queue and self._sink.bytesFree() > 0:
            chunk = self._queue[0]
            writable = min(len(chunk), self._sink.bytesFree())
            written = self._audio_io.write(chunk[:writable])
            if written <= 0:
                break
            self._output_gate.begin_playback()
            if written == len(chunk):
                self._queue.popleft()
            else:
                self._queue[0] = chunk[written:]

        if self._queue:
            self._flush_timer.start()
        else:
            self._finish_when_drained()

    def _finish_when_drained(self) -> None:
        """Keep the gate closed only after Qt's output buffer has drained."""
        if self._sink is None:
            self._output_gate.end_playback()
            self._flush_timer.stop()
            return

        buffer_size = self._sink.bufferSize()
        if buffer_size > 0 and self._sink.bytesFree() >= buffer_size:
            self._output_gate.end_playback()
            self._flush_timer.stop()
            return
        self._flush_timer.start()

    @staticmethod
    def _decode_base64(audio_data: str) -> bytes:
        payload = "".join(audio_data.strip().split())
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        payload += "=" * (-len(payload) % 4)
        return base64.b64decode(payload, validate=False)

    def _prepare_audio_bytes(self, raw: bytes) -> bytes:
        if self._sample_format == "float32":
            return raw
        return self._float32_to_int16(raw)

    @staticmethod
    def _float32_to_int16(raw: bytes) -> bytes:
        usable_len = len(raw) - (len(raw) % 4)
        if usable_len <= 0:
            return b""

        samples = array.array("f")
        samples.frombytes(raw[:usable_len])
        if sys.byteorder != "little":
            samples.byteswap()

        converted = array.array(
            "h",
            (
                max(-32768, min(32767, int(max(-1.0, min(1.0, sample)) * 32767)))
                for sample in samples
            ),
        )
        if sys.byteorder != "little":
            converted.byteswap()
        return converted.tobytes()

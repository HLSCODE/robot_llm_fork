"""
Speech runtime that connects microphone, wake word, VAD, ASR, and controller.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .asr import ASREngine, FunASRConfig, FunASRRecognizer
from .audio import AudioCapture, VoiceAudioConfig, duration_ms
from .output_gate import AudioOutputGate
from ..core.controller import VoiceInteractionController
from ..core.types import VoiceEvent, VoiceSessionState
from .utterance import UtteranceBuffer, UtteranceEndpoint, UtteranceEndpointConfig
from .vad import FunASRVAD, FunASRVADConfig, VADDetector
from .wake_word import (
    DummyWakeWordEngine,
    OpenWakeWordEngine,
    SherpaOnnxWakeWordEngine,
    WakeWordEngine,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceSpeechRuntimeConfig:
    audio: VoiceAudioConfig = field(default_factory=VoiceAudioConfig)
    endpoint: UtteranceEndpointConfig = field(default_factory=UtteranceEndpointConfig)
    vad_chunk_ms: int = 200
    listening_timeout_s: float = 8.0
    follow_up_listening_timeout_s: float = 25.0
    wake_cooldown_s: float = 1.5
    wake_welcome_enabled: bool = False
    wake_welcome_workflow: str = ""
    show_asr_timing: bool = False

    @property
    def vad_chunk_samples(self) -> int:
        return int(self.audio.sample_rate * self.vad_chunk_ms / 1000)


class VoiceSpeechRuntime:
    """
    Runs the physical speech pipeline and feeds recognized text into the
    existing VoiceInteractionController.
    """

    def __init__(
        self,
        *,
        controller: VoiceInteractionController,
        asr: ASREngine,
        vad: VADDetector,
        wake_word: Optional[WakeWordEngine] = None,
        audio_output_gate: Optional[AudioOutputGate] = None,
        config: VoiceSpeechRuntimeConfig | None = None,
    ) -> None:
        self.controller = controller
        self.asr = asr
        self.vad = vad
        self.wake_word = wake_word
        self.audio_output_gate = audio_output_gate
        self.config = config or VoiceSpeechRuntimeConfig()
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    async def run(self) -> AsyncIterator[VoiceEvent]:
        """Run until stop() is called or microphone capture raises."""
        self._stop_requested = False
        endpoint = UtteranceEndpoint(self.config.endpoint, clock=time.monotonic)
        buffer = UtteranceBuffer()
        vad_accumulator: list[np.ndarray] = []
        wake_cooldown_until = 0.0
        listening_started_at = 0.0
        has_valid_user_input = False

        with AudioCapture(self.config.audio) as capture:
            for chunk in capture.chunks():
                if self._stop_requested:
                    break
                await asyncio.sleep(0)

                now = time.monotonic()
                if self._is_robot_speaking():
                    # Discard speaker echo instead of allowing it into KWS, VAD, or ASR.
                    self._reset_detectors(endpoint, buffer, vad_accumulator)
                    capture.clear()
                    continue

                session = self.controller.session

                if session.state == VoiceSessionState.SLEEPING:
                    listening_started_at = 0.0
                    if self.wake_word is None:
                        continue
                    wake_result = self.wake_word.accept_audio(chunk, self.config.audio.sample_rate)
                    if bool(wake_result.get("triggered")):
                        has_valid_user_input = False
                        event = self.controller.wake()
                        event.data.update({"wake_word": wake_result})
                        yield event
                        async for feedback_event in self.controller.stream_wake_feedback():
                            feedback_event.data["wake_word"] = wake_result
                            yield feedback_event
                        if (
                            self.config.wake_welcome_enabled
                            and self.config.wake_welcome_workflow
                        ):
                            yield VoiceEvent(
                                type="wake_welcome_requested",
                                text="请求执行唤醒欢迎动作",
                                data={
                                    "workflow_name": self.config.wake_welcome_workflow
                                },
                            )
                        self._reset_detectors(endpoint, buffer, vad_accumulator)
                        wake_cooldown_until = now + self.config.wake_cooldown_s
                        listening_started_at = now
                        yield VoiceEvent(type="listening_started", text="开始监听用户语音")
                    continue

                timeout_event = self.controller.check_timeout()
                if timeout_event is not None:
                    self._reset_all(endpoint, buffer, vad_accumulator)
                    yield timeout_event
                    continue

                if session.state != VoiceSessionState.AWAKE:
                    continue

                if listening_started_at <= 0.0:
                    listening_started_at = now
                    yield VoiceEvent(type="listening_started", text="开始监听用户语音")

                if now < wake_cooldown_until:
                    continue

                if (
                    not endpoint.in_speech
                    and now - listening_started_at
                    > self._listening_timeout_s(has_valid_user_input)
                ):
                    self.controller.sleep()
                    self._reset_all(endpoint, buffer, vad_accumulator)
                    yield VoiceEvent(type="session_ended", text="会话已超时")
                    continue

                vad_accumulator.append(chunk)
                vad_audio = np.concatenate(vad_accumulator)
                if vad_audio.size < self.config.vad_chunk_samples:
                    continue

                vad_chunk = vad_audio[: self.config.vad_chunk_samples]
                remainder = vad_audio[self.config.vad_chunk_samples :]
                vad_accumulator.clear()
                if remainder.size:
                    vad_accumulator.append(remainder.copy())

                vad_result = self.vad.accept_audio(vad_chunk, self.config.audio.sample_rate)
                should_finish, reason = endpoint.observe(vad_chunk, vad_result)

                if reason == "started":
                    buffer.clear()
                    yield VoiceEvent(type="speech_started", text="检测到用户说话")

                if endpoint.in_speech or should_finish:
                    buffer.append(vad_chunk)

                if should_finish:
                    async for event in self._handle_utterance(
                        buffer=buffer,
                        endpoint=endpoint,
                        capture=capture,
                        reason=reason,
                    ):
                        if event.type == "asr_result" and event.text.strip():
                            has_valid_user_input = True
                        yield event
                    vad_accumulator.clear()
                    listening_started_at = time.monotonic()
                    wake_cooldown_until = listening_started_at + self.config.wake_cooldown_s

    async def _handle_utterance(
        self,
        *,
        buffer: UtteranceBuffer,
        endpoint: UtteranceEndpoint,
        capture: AudioCapture,
        reason: str,
    ) -> AsyncIterator[VoiceEvent]:
        utterance = buffer.get_audio()
        buffer.clear()
        endpoint.reset()

        utterance_ms = duration_ms(utterance, self.config.audio.sample_rate)
        if utterance_ms < self.config.endpoint.min_utterance_ms:
            yield VoiceEvent(
                type="done",
                text="",
                data={"ignored": True, "reason": "utterance_too_short", "utterance_ms": utterance_ms},
            )
            return

        started_at = time.monotonic()
        yield VoiceEvent(
            type="asr_started",
            text="开始识别语音",
            data={"utterance_ms": utterance_ms, "reason": reason},
        )
        try:
            text = self.asr.transcribe(utterance, self.config.audio.sample_rate)
        except Exception as exc:
            logger.warning("ASR failed: %s", exc, exc_info=True)
            yield VoiceEvent(type="error", text=f"语音识别失败: {exc}")
            return

        elapsed_ms = (time.monotonic() - started_at) * 1000
        yield VoiceEvent(
            type="asr_result",
            text=text,
            data={"elapsed_ms": elapsed_ms, "utterance_ms": utterance_ms},
        )
        if not text:
            return

        async for event in self.controller.handle_text(text):
            yield event
        capture.clear()

    def _reset_all(
        self,
        endpoint: UtteranceEndpoint,
        buffer: UtteranceBuffer,
        vad_accumulator: list[np.ndarray],
    ) -> None:
        if self.wake_word is not None:
            self.wake_word.reset()
        self._reset_detectors(endpoint, buffer, vad_accumulator)

    def _reset_detectors(
        self,
        endpoint: UtteranceEndpoint,
        buffer: UtteranceBuffer,
        vad_accumulator: list[np.ndarray],
    ) -> None:
        self.vad.reset()
        endpoint.reset()
        buffer.clear()
        vad_accumulator.clear()

    def _is_robot_speaking(self) -> bool:
        return self.audio_output_gate is not None and self.audio_output_gate.is_playing()

    def _listening_timeout_s(self, has_valid_user_input: bool) -> float:
        """Use a longer follow-up window after the user has started a conversation."""
        if has_valid_user_input:
            return self.config.follow_up_listening_timeout_s
        return self.config.listening_timeout_s


def build_voice_speech_runtime(
    controller: VoiceInteractionController,
    config: dict[str, Any],
    audio_output_gate: Optional[AudioOutputGate] = None,
) -> VoiceSpeechRuntime:
    """Create a speech runtime from an injected settings snapshot."""
    if not bool(config.get("speech_input_enabled", False)):
        raise RuntimeError("VOICE_INPUT_ENABLED=false，未启用真实语音输入。")

    runtime_config = _runtime_config_from_dict(config)
    suppress_output = bool(config.get("suppress_model_output", True))

    vad = FunASRVAD.from_config(
        FunASRVADConfig(
            model=str(config.get("vad_model") or "fsmn-vad"),
            chunk_size_ms=int(config.get("vad_chunk_ms") or runtime_config.vad_chunk_ms),
            suppress_model_output=suppress_output,
        )
    )
    asr = FunASRRecognizer.from_config(
        FunASRConfig(
            model=str(config.get("asr_model") or "iic/SenseVoiceSmall"),
            punc_model=_optional_str(config.get("asr_punc_model")),
            device=_optional_str(config.get("asr_device")),
            batch_size_s=int(config.get("asr_batch_size_s") or 60),
            suppress_model_output=suppress_output,
        )
    )
    wake_word = _create_wake_word_from_config(config) if config.get("wake_word_enabled") else None
    return VoiceSpeechRuntime(
        controller=controller,
        asr=asr,
        vad=vad,
        wake_word=wake_word,
        audio_output_gate=audio_output_gate,
        config=runtime_config,
    )


def _runtime_config_from_dict(config: dict[str, Any]) -> VoiceSpeechRuntimeConfig:
    audio = VoiceAudioConfig(
        sample_rate=int(config.get("audio_sample_rate") or 16_000),
        channels=int(config.get("audio_channels") or 1),
        audio_block_ms=int(config.get("audio_block_ms") or 100),
        queue_size=int(config.get("audio_queue_size") or 300),
        latency=_parse_latency(config.get("audio_latency", "high")),
        device=_parse_device(config.get("audio_device")),
        show_status=bool(config.get("audio_show_status", False)),
    )
    endpoint = UtteranceEndpointConfig(
        min_utterance_ms=int(config.get("min_utterance_ms") or 500),
        max_utterance_ms=int(config.get("max_utterance_ms") or 30_000),
        end_silence_ms=int(config.get("end_silence_ms") or 800),
        speech_start_rms_threshold=float(
            config.get("speech_start_rms_threshold") or 0.025
        ),
        speech_start_confirm_chunks=int(
            config.get("speech_start_confirm_chunks") or 1
        ),
        silence_rms_threshold=float(config.get("silence_rms_threshold") or 0.01),
    )
    return VoiceSpeechRuntimeConfig(
        audio=audio,
        endpoint=endpoint,
        vad_chunk_ms=int(config.get("vad_chunk_ms") or 200),
        listening_timeout_s=float(config.get("listening_timeout_s") or 8.0),
        follow_up_listening_timeout_s=float(
            config.get("follow_up_listening_timeout_s") or 25.0
        ),
        wake_cooldown_s=float(config.get("wake_cooldown_s") or 1.5),
        wake_welcome_enabled=bool(config.get("wake_welcome_enabled", False)),
        wake_welcome_workflow=str(
            config.get("wake_welcome_workflow") or ""
        ).strip(),
        show_asr_timing=bool(config.get("show_asr_timing", False)),
    )


def _create_wake_word_from_config(config: dict[str, Any]) -> WakeWordEngine:
    engine = str(config.get("wake_engine") or "sherpa").lower()
    if engine == "dummy":
        return DummyWakeWordEngine(auto_trigger=bool(config.get("wake_auto_trigger", False)))

    if engine == "openwakeword":
        model_paths: list[str | Path] = [
            resolved
            for path in _split_csv(str(config.get("openwakeword_model_paths") or ""))
            if (resolved := _resolve_project_path(path)) is not None
        ]
        return OpenWakeWordEngine(
            model_paths=model_paths or None,
            threshold=float(config.get("openwakeword_threshold") or 0.6),
        )

    if engine != "sherpa":
        raise ValueError(f"Unsupported wake word engine: {engine}")

    required = {
        "VOICE_KWS_ENCODER": _resolve_project_path(config.get("kws_encoder")),
        "VOICE_KWS_DECODER": _resolve_project_path(config.get("kws_decoder")),
        "VOICE_KWS_JOINER": _resolve_project_path(config.get("kws_joiner")),
        "VOICE_KWS_TOKENS": _resolve_project_path(config.get("kws_tokens")),
        "VOICE_KWS_KEYWORDS_FILE": _resolve_project_path(config.get("kws_keywords_file")),
    }
    missing = [name for name, path in required.items() if path is None or not path.is_file()]
    if missing:
        detail = ", ".join(missing)
        raise FileNotFoundError(
            f"Sherpa KWS 模型或关键词文件不存在: {detail}。"
            "请将模型放到 models/kws/，或更新 VOICE_KWS_* 配置。"
        )

    validated_required = {
        name: path
        for name, path in required.items()
        if path is not None
    }

    return SherpaOnnxWakeWordEngine(
        encoder=validated_required["VOICE_KWS_ENCODER"],
        decoder=validated_required["VOICE_KWS_DECODER"],
        joiner=validated_required["VOICE_KWS_JOINER"],
        tokens=validated_required["VOICE_KWS_TOKENS"],
        keywords_file=validated_required["VOICE_KWS_KEYWORDS_FILE"],
        provider=str(config.get("kws_provider") or "cpu"),
        num_threads=int(config.get("kws_num_threads") or 1),
        max_active_paths=int(config.get("kws_max_active_paths") or 4),
        keywords_score=float(config.get("kws_score") or 1.5),
        keywords_threshold=float(config.get("kws_threshold") or 0.35),
    )


def _resolve_project_path(value: object) -> Path | None:
    text = _optional_str(value)
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_latency(value: object) -> float | str | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _parse_device(value: object) -> int | str | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]

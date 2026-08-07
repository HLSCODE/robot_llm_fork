"""
MiniCPM-o Realtime Chat provider。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
import struct
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import websockets
except ImportError:
    websockets = None

from ..base import BaseLLMClient
from ..errors import LLMProviderError
from ..types import (
    LLMCapability,
    LLMChatResult,
    LLMContentPart,
    LLMMessage,
    LLMStreamEvent,
)

logger = logging.getLogger(__name__)
DEFAULT_REF_AUDIO_PATH = (
    Path(__file__).resolve().parents[3]
    / "assets"
    / "ref_audio"
    / "ref_minicpm_signature.wav"
)


class MiniCPMRealtimeClient(BaseLLMClient):
    """MiniCPM-o Realtime Chat 客户端。"""

    def __init__(
        self,
        gateway_host: str = "localhost",
        gateway_port: int = 8006,
        ws_scheme: str = "wss",
        gateway_path_prefix: str = "",
        realtime_path: str = "/v1/realtime",
        model: str = "minicpm-o",
        timeout_s: float = 60.0,
        verify_ssl: bool = False,
        default_ref_audio_path: Optional[str | Path] = None,
    ) -> None:
        self._gateway_host = gateway_host
        self._gateway_port = int(gateway_port)
        self._ws_scheme = self._normalize_ws_scheme(ws_scheme)
        self._gateway_path_prefix = gateway_path_prefix.rstrip("/")
        self._realtime_path = self._normalize_realtime_path(realtime_path)
        self._model = model or "minicpm-o"
        self._timeout_s = float(timeout_s)
        self._verify_ssl = verify_ssl
        self._default_ref_audio_path = (
            Path(default_ref_audio_path)
            if default_ref_audio_path is not None
            else DEFAULT_REF_AUDIO_PATH
        )
        self._default_ref_audio_data: Optional[str] = None
        self._default_ref_audio_loaded = False
        self._available = websockets is not None and bool(gateway_host)

        if websockets is None:
            logger.error("websockets 库未安装，MiniCPM Realtime 不可用")
        else:
            logger.info("MiniCPM Realtime 客户端已配置: %s", self._build_realtime_url())

    def is_available(self) -> bool:
        return self._available

    def get_model_name(self) -> str:
        return self._model

    def get_provider_name(self) -> str:
        return "minicpm"

    def capabilities(self) -> set[LLMCapability]:
        return {
            LLMCapability.CHAT,
            LLMCapability.STREAM_CHAT,
            LLMCapability.VISION_CHAT,
            LLMCapability.AUDIO_CHAT,
            LLMCapability.TTS,
            LLMCapability.PLANNING,
        }

    async def chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        text_parts: List[str] = []
        final_text = ""
        raw_done = None
        metrics = None

        options = dict(options)
        options["streaming"] = False

        async for event in self.stream_chat(messages, **options):
            if event.type == "text_delta":
                text_parts.append(event.text_delta)
            elif event.type == "done":
                final_text = event.text or "".join(text_parts)
                raw_done = event.raw
                metrics = event.metrics
            elif event.type == "error":
                raise LLMProviderError(event.error or "MiniCPM Realtime 调用失败")

        return LLMChatResult(
            text=final_text or "".join(text_parts),
            model=self._model,
            provider=self.get_provider_name(),
            raw=raw_done,
            metrics=metrics,
        )

    async def stream_chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        if not self.is_available():
            yield LLMStreamEvent(type="error", error="MiniCPM Realtime 不可用")
            return

        url = self._build_realtime_url()
        try:
            async with websockets.connect(
                url,
                ssl=self._ssl_ctx(),
                max_size=100 * 1024 * 1024,
                open_timeout=min(self._timeout_s, 30.0),
            ) as ws:
                await self._wait_for_type(ws, "session.queue_done")
                await self._send_json(ws, {"type": "session.init", "payload": {}})
                created = await self._wait_for_type(ws, "session.created")
                yield LLMStreamEvent(type="session_started", raw=created)

                await self._send_json(ws, self._build_input_append(messages, options))

                async for event in self._read_response_events(ws):
                    yield event
                    if event.type in ("done", "error"):
                        break

                await self._close_session(ws)
        except asyncio.TimeoutError:
            yield LLMStreamEvent(type="error", error="MiniCPM Realtime 响应超时")
        except Exception as exc:
            message = (
                f"MiniCPM Realtime WebSocket 连接失败: {url} ({exc})。"
                "请确认该网关支持 Realtime Chat，且 MINICPM_WS_SCHEME/MINICPM_GATEWAY_PORT/"
                "MINICPM_GATEWAY_PATH_PREFIX/MINICPM_REALTIME_PATH 配置正确。"
            )
            logger.warning(message)
            yield LLMStreamEvent(type="error", error=message)

    def _build_realtime_url(self) -> str:
        ws_scheme = self._ws_scheme
        default_port = 443 if ws_scheme == "wss" else 80
        port_suffix = "" if self._gateway_port == default_port else f":{self._gateway_port}"
        prefix = self._gateway_path_prefix
        path = self._realtime_path
        separator = "&" if "?" in path else "?"
        if "mode=" not in path:
            path = f"{path}{separator}mode=chat"
        return f"{ws_scheme}://{self._gateway_host}{port_suffix}{prefix}{path}"

    @staticmethod
    def _normalize_realtime_path(path: str) -> str:
        path = (path or "/v1/realtime").strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    @staticmethod
    def _normalize_ws_scheme(scheme: str) -> str:
        scheme = (scheme or "wss").strip().lower()
        if scheme in ("https", "wss"):
            return "wss"
        if scheme in ("http", "ws"):
            return "ws"
        logger.warning("未知 MiniCPM WebSocket scheme: %s，使用 wss", scheme)
        return "wss"

    def _ssl_ctx(self) -> Optional[ssl.SSLContext]:
        if self._ws_scheme != "wss":
            return None
        if self._verify_ssl:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _send_json(self, ws: Any, payload: Dict[str, Any]) -> None:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def _recv_json(self, ws: Any) -> Dict[str, Any]:
        raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout_s)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        try:
            packet = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"MiniCPM 返回非法 JSON: {text[:200]}") from exc
        if not isinstance(packet, dict):
            raise LLMProviderError("MiniCPM 返回非对象 JSON")
        return packet

    async def _wait_for_type(self, ws: Any, expected_type: str) -> Dict[str, Any]:
        while True:
            packet = await self._recv_json(ws)
            packet_type = packet.get("type")
            if packet_type == expected_type:
                return packet
            if packet_type == "error":
                raise LLMProviderError(packet.get("error") or packet.get("message") or "MiniCPM error")
            logger.debug("MiniCPM 等待 %s 时收到: %s", expected_type, packet_type)

    def _build_input_append(
        self,
        messages: List[LLMMessage],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        max_new_tokens = (
            options.get("max_new_tokens")
            or options.get("max_tokens")
            or 512
        )
        streaming = bool(options.get("streaming", True))
        raw_tts_options = options.get("tts")
        tts_options: Dict[str, Any] = (
            raw_tts_options if isinstance(raw_tts_options, dict) else {}
        )

        tts_enabled = bool(options.get("tts_enabled", tts_options.get("enabled", False)))
        input_payload: Dict[str, Any] = {
            "messages": [self._convert_message(message) for message in messages],
            "streaming": streaming,
            "generation": {
                "max_new_tokens": max_new_tokens,
                "length_penalty": options.get("length_penalty", 1.1),
            },
            "image": {
                "max_slice_nums": options.get("image_max_slice_nums", 1),
            },
            "omni_mode": bool(options.get("omni_mode", False)),
            "tts": {
                "enabled": tts_enabled,
            },
            "use_tts_template": bool(options.get("use_tts_template", False)),
            "enable_thinking": bool(options.get("enable_thinking", False)),
        }

        ref_audio_data = self._resolve_ref_audio_data(options, tts_options, tts_enabled)
        if ref_audio_data:
            input_payload["tts"]["ref_audio_data"] = ref_audio_data

        return {
            "type": "input.append",
            "input": input_payload,
        }

    def _resolve_ref_audio_data(
        self,
        options: Dict[str, Any],
        tts_options: Dict[str, Any],
        tts_enabled: bool,
    ) -> Optional[str]:
        explicit = (
            options.get("ref_audio_data")
            or options.get("tts_ref_audio_data")
            or tts_options.get("ref_audio_data")
        )
        if explicit:
            return str(explicit)
        if not tts_enabled:
            return None
        return self._load_default_ref_audio_data()

    def _load_default_ref_audio_data(self) -> Optional[str]:
        if self._default_ref_audio_loaded:
            return self._default_ref_audio_data

        self._default_ref_audio_loaded = True
        try:
            self._default_ref_audio_data = self._encode_wav_as_float32_base64(
                self._default_ref_audio_path
            )
        except FileNotFoundError:
            logger.warning("MiniCPM 默认参考音频不存在: %s", self._default_ref_audio_path)
            return None
        except Exception as exc:
            logger.warning("读取 MiniCPM 默认参考音频失败: %s", exc)
            return None

        logger.info("MiniCPM 默认参考音频已按 float32 PCM 编码: %s", self._default_ref_audio_path)
        return self._default_ref_audio_data

    @staticmethod
    def _encode_wav_as_float32_base64(path: Path) -> str:
        """Encode 16-bit PCM wav as raw little-endian float32 base64."""
        with wave.open(str(path), "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            frame_count = wav_file.getnframes()
            channels = wav_file.getnchannels()
            frames = wav_file.readframes(frame_count)

        if sample_width != 2:
            raise ValueError(f"MiniCPM 默认参考音频仅支持 16-bit PCM WAV，当前 sample_width={sample_width}")

        sample_count = len(frames) // sample_width
        samples = struct.unpack(f"<{sample_count}h", frames)
        float_bytes = struct.pack(
            f"<{sample_count}f",
            *(sample / 32768.0 for sample in samples),
        )
        logger.debug(
            "MiniCPM 参考音频编码: channels=%s samples=%s bytes=%s",
            channels,
            sample_count,
            len(float_bytes),
        )
        return base64.b64encode(float_bytes).decode("ascii")

    def _convert_message(self, message: LLMMessage) -> Dict[str, Any]:
        return {
            "role": message.role,
            "content": self._convert_content(message.content),
        }

    def _convert_content(self, content: str | List[LLMContentPart]) -> Any:
        if isinstance(content, str):
            return content

        parts: list[dict[str, Any]] = []
        for part in content:
            if part.type == "text":
                parts.append({"type": "text", "text": part.text or ""})
            elif part.type == "image":
                image_part = {"type": "image", "data": part.data or ""}
                if part.mime_type:
                    image_part["mime_type"] = part.mime_type
                parts.append(image_part)
            elif part.type == "audio":
                audio_part = {"type": "audio", "data": part.data or ""}
                if part.mime_type:
                    audio_part["mime_type"] = part.mime_type
                parts.append(audio_part)
        return parts

    async def _read_response_events(self, ws: Any) -> AsyncIterator[LLMStreamEvent]:
        while True:
            packet = await self._recv_json(ws)
            packet_type = packet.get("type")

            if packet_type == "response.output.delta":
                kind = packet.get("kind")
                if kind == "text":
                    yield LLMStreamEvent(
                        type="text_delta",
                        text_delta=packet.get("text", ""),
                        raw=packet,
                    )
                elif kind == "audio":
                    yield LLMStreamEvent(
                        type="audio_delta",
                        audio_data=packet.get("audio"),
                        raw=packet,
                    )
                else:
                    logger.debug("未知 MiniCPM delta kind: %s", kind)
            elif packet_type == "response.done":
                yield LLMStreamEvent(
                    type="done",
                    text=packet.get("text", ""),
                    audio_data=packet.get("audio"),
                    metrics=packet.get("metrics"),
                    raw=packet,
                )
                return
            elif packet_type == "error":
                yield LLMStreamEvent(
                    type="error",
                    error=packet.get("error") or packet.get("message") or "MiniCPM error",
                    raw=packet,
                )
                return
            elif packet_type == "session.closed":
                return
            else:
                logger.debug("忽略 MiniCPM 事件: %s", packet_type)

    async def _close_session(self, ws: Any) -> None:
        try:
            await self._send_json(ws, {"type": "session.close", "reason": "turn_done"})
        except Exception:
            return

"""
OpenAI-compatible HTTP provider。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Dict, List, Optional

from ..base import LLMClient, LLMPlanResult
from ..errors import LLMConfigError, LLMProviderError
from ..planner import SkillPlanner
from ..types import (
    LLMCapability,
    LLMChatResult,
    LLMContentPart,
    LLMMessage,
    LLMStreamEvent,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    """OpenAI Chat Completions 兼容 provider。"""

    def __init__(
        self,
        provider_name: str,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        default_model: str = "gpt-4o",
    ) -> None:
        self._provider_name = provider_name
        self._api_key = api_key or ""
        self._model = model or default_model
        self._base_url = base_url or ""
        self._async_client = None
        self._available = False

        if not self._api_key:
            logger.warning("%s API Key 未配置", provider_name)
            return

        try:
            from openai import AsyncOpenAI

            kwargs: Dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._async_client = AsyncOpenAI(**kwargs)
            self._available = True
            url_info = f", base_url={self._base_url}" if self._base_url else ""
            logger.info(
                "%s LLM 客户端初始化成功，使用模型: %s%s",
                provider_name,
                self._model,
                url_info,
            )
        except ImportError:
            logger.error("OpenAI SDK 未安装，请运行: pip install openai")
        except Exception as exc:
            logger.error("%s LLM 客户端初始化失败: %s", provider_name, exc)

    def is_available(self) -> bool:
        return self._available and self._async_client is not None

    def get_model_name(self) -> str:
        return self._model

    def get_provider_name(self) -> str:
        return self._provider_name

    def capabilities(self) -> set[LLMCapability]:
        return {
            LLMCapability.CHAT,
            LLMCapability.STREAM_CHAT,
            LLMCapability.PLANNING,
        }

    async def chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        if not self.is_available():
            raise LLMConfigError(f"{self._provider_name} LLM 不可用")

        request = self._build_chat_request(messages, options, stream=False)

        try:
            response = await self._async_client.chat.completions.create(**request)
            message = response.choices[0].message
            text = (message.content or "").strip()
            return LLMChatResult(
                text=text,
                model=self._model,
                provider=self._provider_name,
                raw=_model_dump(response),
                usage=_model_dump(getattr(response, "usage", None)),
            )
        except Exception as exc:
            raise LLMProviderError(str(exc)) from exc

    async def stream_chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        if not self.is_available():
            yield LLMStreamEvent(
                type="error",
                error=f"{self._provider_name} LLM 不可用",
            )
            return

        request = self._build_chat_request(messages, options, stream=True)
        text_parts: List[str] = []

        try:
            stream = await self._async_client.chat.completions.create(**request)
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                text_delta = getattr(delta, "content", None) if delta else None
                if text_delta:
                    text_parts.append(text_delta)
                    yield LLMStreamEvent(
                        type="text_delta",
                        text_delta=text_delta,
                        raw=_model_dump(chunk),
                    )

            yield LLMStreamEvent(
                type="done",
                text="".join(text_parts),
                raw=None,
            )
        except Exception as exc:
            yield LLMStreamEvent(type="error", error=str(exc))

    def plan(self, user_text: str, skill_summaries: List[Dict[str, Any]]) -> LLMPlanResult:
        return SkillPlanner(self).plan_sync(user_text, skill_summaries)

    def _build_chat_request(
        self,
        messages: List[LLMMessage],
        options: Dict[str, Any],
        stream: bool,
    ) -> Dict[str, Any]:
        request: Dict[str, Any] = {
            "model": self._model,
            "messages": [self._convert_message(message) for message in messages],
            "temperature": options.get("temperature", 0.3),
            "stream": stream,
        }

        max_tokens = options.get("max_tokens")
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        response_format = options.get("response_format")
        if response_format == "json":
            request["response_format"] = {"type": "json_object"}
        elif isinstance(response_format, dict):
            request["response_format"] = response_format

        return request

    def _convert_message(self, message: LLMMessage) -> Dict[str, Any]:
        return {
            "role": message.role,
            "content": self._convert_content(message.content),
        }

    def _convert_content(self, content: str | List[LLMContentPart]) -> Any:
        if isinstance(content, str):
            return content

        parts = []
        for part in content:
            if part.type == "text":
                parts.append({"type": "text", "text": part.text or ""})
            elif part.type == "image":
                image_url = part.data or ""
                if image_url and not image_url.startswith(("http://", "https://", "data:")):
                    mime_type = part.mime_type or "image/jpeg"
                    image_url = f"data:{mime_type};base64,{image_url}"
                parts.append({"type": "image_url", "image_url": {"url": image_url}})
            elif part.type == "audio":
                parts.append({
                    "type": "input_audio",
                    "input_audio": {
                        "data": part.data or "",
                        "format": part.mime_type or "wav",
                    },
                })
        return parts


def _model_dump(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return None

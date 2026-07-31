"""Reusable deterministic LLM provider for tests and offline simulations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

from .base import BaseLLMClient
from .types import (
    LLMCapability,
    LLMChatResult,
    LLMMessage,
    LLMStreamEvent,
)


class FakeLLMClient(BaseLLMClient):
    def __init__(
        self,
        provider: str,
        *,
        model: str | None = None,
        chat_text: str = "ok",
        chat_error: Exception | None = None,
        stream_events: Iterable[LLMStreamEvent] | None = None,
        available: bool = True,
        capabilities: set[LLMCapability] | None = None,
    ) -> None:
        self._provider = provider
        self._model = model or f"{provider}-model"
        self.chat_text = chat_text
        self.chat_error = chat_error
        self.stream_events = tuple(
            stream_events
            or (LLMStreamEvent(type="done", text=chat_text),)
        )
        self.available = available
        self._capabilities = capabilities or {
            LLMCapability.CHAT,
            LLMCapability.STREAM_CHAT,
            LLMCapability.PLANNING,
        }
        self.chat_calls: list[tuple[tuple[LLMMessage, ...], dict[str, Any]]] = []
        self.stream_calls: list[
            tuple[tuple[LLMMessage, ...], dict[str, Any]]
        ] = []
        self.closed = False

    def is_available(self) -> bool:
        return self.available and not self.closed

    def get_model_name(self) -> str:
        return self._model

    def get_provider_name(self) -> str:
        return self._provider

    def capabilities(self) -> set[LLMCapability]:
        return set(self._capabilities)

    async def chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        self.chat_calls.append((tuple(messages), dict(options)))
        if self.chat_error is not None:
            raise self.chat_error
        return LLMChatResult(
            text=self.chat_text,
            model=self._model,
            provider=self._provider,
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.stream_calls.append((tuple(messages), dict(options)))
        for event in self.stream_events:
            yield event

    async def close(self) -> None:
        self.closed = True

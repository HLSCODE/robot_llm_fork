"""
Generic LLM task runner.

TaskRunner applies a TaskProfile to normal chat calls. It does not parse
business results; specialized tasks such as CommandPlanner still own that part.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, List, Optional, Sequence, Union

from ...configuration.settings import TaskRouteSettings
from ..base import BaseLLMClient
from ..response_pipeline import ResponsePipeline
from ..types import LLMChatResult, LLMContentPart, LLMMessage, LLMStreamEvent
from .profiles import GENERAL_CHAT_PROFILE, TaskProfile

MessageContent = Union[str, List[LLMContentPart]]
ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


class TaskRunner:
    """Run generic LLM chat tasks with optional TaskProfile injection."""

    def __init__(
        self,
        llm: Optional[BaseLLMClient] = None,
        default_profile: TaskProfile = GENERAL_CHAT_PROFILE,
        client_resolver: Optional[ClientResolver] = None,
        response_pipeline: ResponsePipeline | None = None,
        route_resolver: Callable[[TaskProfile], TaskRouteSettings] | None = None,
    ) -> None:
        self._llm = llm
        self._default_profile = default_profile
        self._client_resolver = client_resolver
        self._response_pipeline = response_pipeline
        self._route_resolver = route_resolver

    async def chat(
        self,
        user_text: Optional[MessageContent] = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Run a non-streaming chat call with a TaskProfile."""
        active_profile = profile or self._default_profile
        llm = self._resolve_llm(active_profile, provider)
        final_messages = self._build_messages(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=active_profile,
            prompt_context=prompt_context,
        )
        return await llm.chat(
            final_messages,
            **active_profile.chat_options(**chat_options),
        )

    async def stream_chat(
        self,
        user_text: Optional[MessageContent] = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        voice_response: bool = False,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Run text generation and optionally synthesize its final text as speech."""
        active_profile = profile or self._default_profile
        llm = self._resolve_llm(active_profile, provider)
        final_messages = self._build_messages(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=active_profile,
            prompt_context=prompt_context,
        )
        if self._response_pipeline is None or self._route_resolver is None:
            async for event in llm.stream_chat(
                final_messages,
                **active_profile.stream_options(**chat_options),
            ):
                yield event
            return

        route = self._route_resolver(active_profile)
        async for event in self._response_pipeline.stream(
            llm,
            final_messages,
            active_profile,
            route,
            voice_response=voice_response,
            chat_options=chat_options,
        ):
            yield event

    async def stream(
        self,
        user_text: Optional[MessageContent] = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        voice_response: bool = False,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Alias for stream_chat()."""
        async for event in self.stream_chat(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            voice_response=voice_response,
            provider=provider,
            **chat_options,
        ):
            yield event

    def _resolve_llm(
        self,
        profile: TaskProfile,
        provider: Optional[str],
    ) -> BaseLLMClient:
        if self._client_resolver is not None:
            return self._client_resolver(profile, provider)
        if self._llm is None:
            raise ValueError("TaskRunner 未配置 LLM client")
        return self._llm

    def _build_messages(
        self,
        user_text: Optional[MessageContent],
        messages: Optional[Sequence[LLMMessage]],
        system_prompt: Optional[str],
        profile: TaskProfile,
        prompt_context: Optional[dict[str, Any]],
    ) -> List[LLMMessage]:
        if messages is None and user_text is None:
            raise ValueError("TaskRunner.chat 需要 user_text 或 messages")

        final_messages = list(messages or [])
        if user_text is not None:
            final_messages.append(LLMMessage(role="user", content=user_text))

        rendered_system_prompt = system_prompt
        if rendered_system_prompt is None:
            rendered_system_prompt = profile.render_system_prompt(**(prompt_context or {}))

        if not rendered_system_prompt:
            return final_messages

        system_message = LLMMessage(role="system", content=rendered_system_prompt)
        if final_messages and final_messages[0].role == "system":
            if system_prompt is not None:
                final_messages[0] = system_message
            return final_messages

        return [system_message, *final_messages]

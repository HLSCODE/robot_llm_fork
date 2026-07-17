"""
Generic LLM task runner.

TaskRunner applies a TaskProfile to normal chat calls. It does not parse
business results; specialized tasks such as SkillPlanner still own that part.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, List, Optional, Sequence, Union

from ..base import BaseLLMClient
from ..sync_utils import run_coro_sync
from ..types import LLMChatResult, LLMContentPart, LLMMessage, LLMStreamEvent
from .profiles import GENERAL_CHAT_PROFILE, TaskProfile
from .repeat import RepeatTask


MessageContent = Union[str, List[LLMContentPart]]
ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


class TaskRunner:
    """Run generic LLM chat tasks with optional TaskProfile injection."""

    def __init__(
        self,
        llm: Optional[BaseLLMClient] = None,
        default_profile: TaskProfile = GENERAL_CHAT_PROFILE,
        client_resolver: Optional[ClientResolver] = None,
        voice_repeater: Optional[RepeatTask] = None,
    ) -> None:
        self._llm = llm
        self._default_profile = default_profile
        self._client_resolver = client_resolver
        self._voice_repeater = voice_repeater

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

    def chat_sync(
        self,
        user_text: Optional[MessageContent] = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Synchronous wrapper for normal chat tasks."""
        return run_coro_sync(
            self.chat(
                user_text=user_text,
                messages=messages,
                system_prompt=system_prompt,
                profile=profile,
                prompt_context=prompt_context,
                provider=provider,
                **chat_options,
            )
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
        if not voice_response:
            async for event in llm.stream_chat(
                final_messages,
                **active_profile.stream_options(**chat_options),
            ):
                yield event
            return

        if self._voice_repeater is None:
            raise ValueError("TaskRunner 未配置 RepeatTask，无法生成语音响应")

        text_parts: list[str] = []
        final_text = ""
        async for event in llm.stream_chat(
            final_messages,
            **active_profile.stream_options(**chat_options),
        ):
            if event.type == "text_delta":
                text_parts.append(event.text_delta)
                yield event
                continue
            if event.type == "done":
                final_text = event.text or "".join(text_parts)
                continue
            if event.type == "error":
                yield event
                return

        final_text = final_text or "".join(text_parts)
        if not final_text:
            yield LLMStreamEvent(type="done", text="")
            return

        async for event in self._voice_repeater.stream_repeat(
            final_text,
            voice_response=True,
        ):
            if event.type == "audio_delta":
                yield event
                continue
            if event.type == "done":
                # DashScope text was already emitted above; only finish playback here.
                yield LLMStreamEvent(
                    type="done",
                    audio_data=event.audio_data,
                    metrics=event.metrics,
                    raw=event.raw,
                )
                return
            if event.type == "error":
                yield event
                return

        yield LLMStreamEvent(type="done", text="")

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

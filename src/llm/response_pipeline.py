"""Composable text and speech response output pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from ..configuration.settings import TaskRouteSettings
from .base import BaseLLMClient
from .errors import LLMConfigError
from .types import LLMCapability, LLMMessage, LLMStreamEvent

if TYPE_CHECKING:
    from .tasks.profiles import TaskProfile


class SpeechClientResolver(Protocol):
    def __call__(
        self,
        profile: TaskProfile,
        provider: str,
        fallback_providers: tuple[str, ...],
    ) -> BaseLLMClient: ...


class ResponseOutputMode(str, Enum):
    TEXT = "text"
    NATIVE_AUDIO = "native_audio"
    TEXT_THEN_TTS = "text_then_tts"


class SpeechSynthesizer(Protocol):
    """Convert final text into the shared LLM audio event stream."""

    def synthesize(
        self,
        text: str,
        route: TaskRouteSettings,
    ) -> AsyncIterator[LLMStreamEvent]: ...


class LLMSpeechSynthesizer:
    """Adapt a speech-capable LLM provider to the synthesizer contract."""

    def __init__(
        self,
        client_resolver: SpeechClientResolver,
        speech_profile: TaskProfile,
    ) -> None:
        self._client_resolver = client_resolver
        self._speech_profile = speech_profile

    async def synthesize(
        self,
        text: str,
        route: TaskRouteSettings,
    ) -> AsyncIterator[LLMStreamEvent]:
        provider = route.speech_provider.strip().lower()
        if not provider:
            raise LLMConfigError("text_then_tts 模式缺少 speech_provider")
        client = self._client_resolver(
            self._speech_profile,
            provider,
            route.speech_fallback_providers,
        )
        async for event in client.stream_chat(
            [
                LLMMessage(
                    role="system",
                    content=self._speech_profile.render_system_prompt(),
                ),
                LLMMessage(role="user", content=text),
            ],
            **self._speech_profile.stream_options(
                tts_enabled=True,
                use_tts_template=True,
            ),
        ):
            yield event


class ResponsePipeline:
    """Apply one explicit output policy to a model response stream."""

    def __init__(self, speech_synthesizer: SpeechSynthesizer) -> None:
        self._speech_synthesizer = speech_synthesizer

    async def stream(
        self,
        client: BaseLLMClient,
        messages: Sequence[LLMMessage],
        profile: TaskProfile,
        route: TaskRouteSettings,
        *,
        voice_response: bool,
        chat_options: dict[str, Any],
    ) -> AsyncIterator[LLMStreamEvent]:
        mode = (
            ResponseOutputMode(route.output_mode)
            if voice_response
            else ResponseOutputMode.TEXT
        )
        if mode is ResponseOutputMode.TEXT:
            async for event in client.stream_chat(
                list(messages),
                **profile.stream_options(**chat_options),
            ):
                yield event
            return

        if mode is ResponseOutputMode.NATIVE_AUDIO:
            if LLMCapability.TTS not in client.capabilities():
                raise LLMConfigError(
                    f"provider {client.get_provider_name()} 不支持原生语音输出"
                )
            async for event in client.stream_chat(
                list(messages),
                **profile.stream_options(
                    tts_enabled=True,
                    use_tts_template=True,
                    **chat_options,
                ),
            ):
                yield event
            return

        async for event in self._stream_text_then_tts(
            client,
            messages,
            profile,
            route,
            chat_options,
        ):
            yield event

    async def _stream_text_then_tts(
        self,
        client: BaseLLMClient,
        messages: Sequence[LLMMessage],
        profile: TaskProfile,
        route: TaskRouteSettings,
        chat_options: dict[str, Any],
    ) -> AsyncIterator[LLMStreamEvent]:
        text_parts: list[str] = []
        generation_done: LLMStreamEvent | None = None
        async for event in client.stream_chat(
            list(messages),
            **profile.stream_options(**chat_options),
        ):
            if event.type == "text_delta":
                text_parts.append(event.text_delta)
                yield event
            elif event.type == "done":
                generation_done = event
            elif event.type == "error":
                yield event
                return

        final_text = (
            generation_done.text
            if generation_done is not None and generation_done.text
            else "".join(text_parts)
        )
        if not final_text:
            yield generation_done or LLMStreamEvent(type="done", text="")
            return

        async for speech_event in self._speech_synthesizer.synthesize(
            final_text,
            route,
        ):
            if speech_event.type == "audio_delta":
                yield speech_event
                continue
            if speech_event.type == "error":
                yield speech_event
                return
            if speech_event.type == "done":
                yield self._combined_done(
                    final_text,
                    generation_done,
                    speech_event,
                )
                return

        yield self._combined_done(final_text, generation_done, None)

    @staticmethod
    def _combined_done(
        text: str,
        generation: LLMStreamEvent | None,
        speech: LLMStreamEvent | None,
    ) -> LLMStreamEvent:
        return LLMStreamEvent(
            type="done",
            text=text,
            audio_data=speech.audio_data if speech is not None else None,
            metrics=generation.metrics if generation is not None else None,
            raw={
                "generation": generation.raw if generation is not None else None,
                "speech": speech.raw if speech is not None else None,
                "speech_provenance": (
                    speech.provenance.to_dict()
                    if speech is not None and speech.provenance is not None
                    else None
                ),
            },
            provenance=generation.provenance if generation is not None else None,
        )

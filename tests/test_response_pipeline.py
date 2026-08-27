from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest

from src.configuration.config_loader import load_application_settings
from src.configuration.errors import ConfigLoadError
from src.configuration.settings import (
    LLMSettings,
    ModelRoutingSettings,
    SecretSettings,
    TaskRouteSettings,
)
from src.llm.base import BaseLLMClient
from src.llm.registry import LLMRegistry
from src.llm.response_pipeline import ResponsePipeline
from src.llm.tasks.profiles import GENERAL_CHAT_PROFILE
from src.llm.types import (
    LLMCapability,
    LLMChatResult,
    LLMMessage,
    LLMStreamEvent,
)


class _StreamingClient(BaseLLMClient):
    def __init__(
        self,
        events: Sequence[LLMStreamEvent],
        *,
        provider: str = "inference",
        tts: bool = False,
    ) -> None:
        self.events = tuple(events)
        self.provider = provider
        self.options: list[dict[str, Any]] = []
        self._tts = tts

    def is_available(self) -> bool:
        return True

    def get_model_name(self) -> str:
        return f"{self.provider}-model"

    def get_provider_name(self) -> str:
        return self.provider

    def capabilities(self) -> set[LLMCapability]:
        capabilities = {LLMCapability.CHAT, LLMCapability.STREAM_CHAT}
        if self._tts:
            capabilities.add(LLMCapability.TTS)
        return capabilities

    async def chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.options.append(options)
        for event in self.events:
            yield event


class _SpeechSynthesizer:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(
        self,
        text: str,
        route: TaskRouteSettings,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.texts.append(text)
        yield LLMStreamEvent(type="audio_delta", audio_data="audio")
        yield LLMStreamEvent(type="done", audio_data="tail")


class ResponsePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_uses_configured_inference_and_speech_routes(self) -> None:
        inference = _StreamingClient(
            [
                LLMStreamEvent(type="text_delta", text_delta="回答"),
                LLMStreamEvent(type="done", text="回答"),
            ],
            provider="openai",
        )
        speech = _StreamingClient(
            [
                LLMStreamEvent(type="audio_delta", audio_data="audio"),
                LLMStreamEvent(type="done"),
            ],
            provider="minicpm",
            tts=True,
        )
        routes = ModelRoutingSettings(
            general_chat=TaskRouteSettings(
                provider="openai",
                output_mode="text_then_tts",
                speech_provider="minicpm",
            )
        )
        registry = LLMRegistry(
            settings=LLMSettings(default_provider="dashscope"),
            secrets=SecretSettings(),
            model_routing=routes,
            providers={"openai": inference, "minicpm": speech},
        )

        events = [
            event
            async for event in registry.stream_chat(
                user_text="hello",
                profile=GENERAL_CHAT_PROFILE,
                voice_response=True,
            )
        ]

        self.assertEqual(
            ["text_delta", "audio_delta", "done"],
            [event.type for event in events],
        )
        self.assertEqual(1, len(inference.options))
        self.assertEqual(1, len(speech.options))
        self.assertTrue(speech.options[0]["tts_enabled"])

    async def test_voice_disabled_forces_plain_text_output(self) -> None:
        client = _StreamingClient([LLMStreamEvent(type="done", text="ok")])
        speech = _SpeechSynthesizer()
        pipeline = ResponsePipeline(speech)

        events = [
            event
            async for event in pipeline.stream(
                client,
                [LLMMessage(role="user", content="hello")],
                GENERAL_CHAT_PROFILE,
                TaskRouteSettings(
                    provider="dashscope",
                    output_mode="text_then_tts",
                    speech_provider="minicpm",
                ),
                voice_response=False,
                chat_options={},
            )
        ]

        self.assertEqual(["done"], [event.type for event in events])
        self.assertEqual([], speech.texts)
        self.assertNotIn("tts_enabled", client.options[0])

    async def test_native_audio_uses_inference_provider_directly(self) -> None:
        client = _StreamingClient(
            [LLMStreamEvent(type="audio_delta", audio_data="audio")],
            provider="minicpm",
            tts=True,
        )
        speech = _SpeechSynthesizer()
        pipeline = ResponsePipeline(speech)

        events = [
            event
            async for event in pipeline.stream(
                client,
                [LLMMessage(role="user", content="hello")],
                GENERAL_CHAT_PROFILE,
                TaskRouteSettings(
                    provider="minicpm",
                    output_mode="native_audio",
                ),
                voice_response=True,
                chat_options={},
            )
        ]

        self.assertEqual(["audio_delta"], [event.type for event in events])
        self.assertTrue(client.options[0]["tts_enabled"])
        self.assertTrue(client.options[0]["use_tts_template"])
        self.assertEqual([], speech.texts)

    async def test_text_then_tts_keeps_text_and_synthesizes_final_answer(self) -> None:
        client = _StreamingClient(
            [
                LLMStreamEvent(type="text_delta", text_delta="你"),
                LLMStreamEvent(type="text_delta", text_delta="好"),
                LLMStreamEvent(type="done", text="你好", raw={"id": "generation"}),
            ]
        )
        speech = _SpeechSynthesizer()
        pipeline = ResponsePipeline(speech)

        events = [
            event
            async for event in pipeline.stream(
                client,
                [LLMMessage(role="user", content="hello")],
                GENERAL_CHAT_PROFILE,
                TaskRouteSettings(
                    provider="dashscope",
                    output_mode="text_then_tts",
                    speech_provider="minicpm",
                ),
                voice_response=True,
                chat_options={},
            )
        ]

        self.assertEqual(
            ["text_delta", "text_delta", "audio_delta", "done"],
            [event.type for event in events],
        )
        self.assertEqual(["你好"], speech.texts)
        self.assertEqual("你好", events[-1].text)
        self.assertEqual("tail", events[-1].audio_data)
        self.assertEqual({"id": "generation"}, events[-1].raw["generation"])


class ModelRoutingConfigurationTests(unittest.TestCase):
    def test_nested_model_routes_load_as_typed_settings(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = root / "config.toml"
            config_path.write_text(
                """schema_version = 6

[model_routing.general_chat]
provider = "openai"
fallback_providers = ["deepseek"]
output_mode = "text_then_tts"
speech_provider = "minicpm"
speech_fallback_providers = []
""",
                encoding="utf-8",
            )

            settings = load_application_settings(
                config_path,
                env_file=root / "missing.env",
            )

        route = settings.model_routing.general_chat
        self.assertEqual("openai", route.provider)
        self.assertEqual(("deepseek",), route.fallback_providers)
        self.assertEqual("text_then_tts", route.output_mode)
        self.assertEqual("minicpm", route.speech_provider)

    def test_unknown_nested_route_field_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = root / "config.toml"
            config_path.write_text(
                """schema_version = 6

[model_routing.general_chat]
provider = "dashscope"
output_mode = "text"
unexpected = true
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigLoadError, "unexpected"):
                load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )


if __name__ == "__main__":
    unittest.main()

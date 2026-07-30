from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from src.llm import (
    BaseLLMClient,
    LLMCapability,
    LLMChatResult,
    LLMMessage,
    LLMRegistry,
    LLMStreamEvent,
    TaskProfile,
)
from src.llm.errors import LLMProviderError, LLMResponseParseError
from src.llm.regression import run_regression_suite
from src.llm.routing import ProviderHealthTracker
from src.llm.tasks.classifier import InstructionClassifier


TEST_PROFILE = TaskProfile(
    name="governance_test",
    version="1.0.0",
    system_prompt_template="You are a test model.",
    default_provider="openai",
    required_capabilities=(
        LLMCapability.CHAT,
        LLMCapability.STREAM_CHAT,
    ),
)


class _FakeProvider(BaseLLMClient):
    def __init__(
        self,
        name: str,
        *,
        chat_text: str = "ok",
        chat_error: Exception | None = None,
        stream_events: list[LLMStreamEvent] | None = None,
        available: bool = True,
    ) -> None:
        self.name = name
        self.chat_text = chat_text
        self.chat_error = chat_error
        self.stream_events = stream_events or [
            LLMStreamEvent(type="done", text=chat_text)
        ]
        self.available = available
        self.chat_calls = 0
        self.stream_calls = 0

    def is_available(self) -> bool:
        return self.available

    def get_model_name(self) -> str:
        return f"{self.name}-model"

    def get_provider_name(self) -> str:
        return self.name

    def capabilities(self) -> set[LLMCapability]:
        return {
            LLMCapability.CHAT,
            LLMCapability.STREAM_CHAT,
            LLMCapability.PLANNING,
        }

    async def chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        self.chat_calls += 1
        if self.chat_error is not None:
            raise self.chat_error
        return LLMChatResult(
            text=self.chat_text,
            model=self.get_model_name(),
            provider=self.name,
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ):
        self.stream_calls += 1
        for event in self.stream_events:
            yield event


def _config(
    *,
    fallbacks: tuple[str, ...] = (),
    threshold: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        LLM_FALLBACK_PROVIDERS=fallbacks,
        LLM_CIRCUIT_FAILURE_THRESHOLD=threshold,
        LLM_CIRCUIT_RECOVERY_SECONDS=30.0,
    )


class LLMProviderGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_circuit_allows_only_one_half_open_probe(self):
        now = [100.0]
        tracker = ProviderHealthTracker(
            failure_threshold=1,
            recovery_seconds=10.0,
            clock=lambda: now[0],
        )

        admitted, _ = tracker.admit("openai", available=True)
        self.assertTrue(admitted)
        tracker.record_failure(
            "openai",
            LLMProviderError("failed"),
        )
        self.assertEqual(
            "open",
            tracker.snapshot("openai", available=True).status.value,
        )

        now[0] += 11.0
        self.assertEqual(
            "half_open",
            tracker.snapshot("openai", available=True).status.value,
        )
        first_probe, _ = tracker.admit("openai", available=True)
        second_probe, reason = tracker.admit("openai", available=True)
        self.assertTrue(first_probe)
        self.assertFalse(second_probe)
        self.assertEqual("half_open_probe_in_flight", reason)

        tracker.record_success("openai")
        self.assertEqual(
            "healthy",
            tracker.snapshot("openai", available=True).status.value,
        )

    async def test_success_records_prompt_model_and_provider_provenance(self):
        primary = _FakeProvider("openai")
        registry = LLMRegistry(
            config=_config(),
            providers={"openai": primary},
        )

        result = await registry.chat(
            user_text="hello",
            profile=TEST_PROFILE,
        )

        self.assertIsNotNone(result.provenance)
        provenance = result.provenance
        assert provenance is not None
        self.assertEqual("governance_test", provenance.task_profile)
        self.assertEqual("1.0.0", provenance.prompt_version)
        self.assertEqual("openai", provenance.provider)
        self.assertEqual("openai-model", provenance.model)
        self.assertEqual(("openai",), provenance.attempted_providers)
        self.assertFalse(provenance.fallback_used)
        self.assertEqual(64, len(provenance.prompt_template_sha256))
        self.assertEqual(64, len(provenance.request_sha256))
        self.assertEqual(
            "healthy",
            registry.get_provider_health()["openai"]["status"],
        )

    async def test_fallback_is_configured_and_explicit_provider_is_pinned(self):
        primary = _FakeProvider(
            "openai",
            chat_error=LLMProviderError("primary failed"),
        )
        fallback = _FakeProvider("dashscope", chat_text="fallback")
        registry = LLMRegistry(
            config=_config(fallbacks=("dashscope",)),
            providers={
                "openai": primary,
                "dashscope": fallback,
            },
        )

        result = await registry.chat(
            user_text="hello",
            profile=TEST_PROFILE,
        )

        self.assertEqual("fallback", result.text)
        assert result.provenance is not None
        self.assertEqual(
            ("openai", "dashscope"),
            result.provenance.attempted_providers,
        )
        self.assertTrue(result.provenance.fallback_used)

        with self.assertRaises(LLMProviderError):
            await registry.chat(
                user_text="hello",
                profile=TEST_PROFILE,
                provider="openai",
            )
        self.assertEqual(1, fallback.chat_calls)

    async def test_circuit_opens_and_skips_repeated_primary_calls(self):
        primary = _FakeProvider(
            "openai",
            chat_error=LLMProviderError("primary failed"),
        )
        fallback = _FakeProvider("dashscope", chat_text="fallback")
        registry = LLMRegistry(
            config=_config(
                fallbacks=("dashscope",),
                threshold=2,
            ),
            providers={
                "openai": primary,
                "dashscope": fallback,
            },
        )

        for _ in range(3):
            result = await registry.chat(
                user_text="hello",
                profile=TEST_PROFILE,
            )
            self.assertEqual("fallback", result.text)

        self.assertEqual(2, primary.chat_calls)
        self.assertEqual(3, fallback.chat_calls)
        health = registry.get_provider_health()["openai"]
        self.assertEqual("open", health["status"])
        self.assertEqual(2, health["consecutive_failures"])
        self.assertGreater(health["circuit_retry_after_s"], 0)

    async def test_stream_falls_back_only_before_output_is_exposed(self):
        primary = _FakeProvider(
            "openai",
            stream_events=[
                LLMStreamEvent(type="error", error="primary failed")
            ],
        )
        fallback = _FakeProvider(
            "dashscope",
            stream_events=[
                LLMStreamEvent(type="text_delta", text_delta="fallback"),
                LLMStreamEvent(type="done", text="fallback"),
            ],
        )
        registry = LLMRegistry(
            config=_config(fallbacks=("dashscope",)),
            providers={
                "openai": primary,
                "dashscope": fallback,
            },
        )

        events = [
            event
            async for event in registry.stream_chat(
                user_text="hello",
                profile=TEST_PROFILE,
            )
        ]

        self.assertEqual(["text_delta", "done"], [
            event.type
            for event in events
        ])
        assert events[-1].provenance is not None
        self.assertEqual(
            ("openai", "dashscope"),
            events[-1].provenance.attempted_providers,
        )

        primary.stream_events = [
            LLMStreamEvent(type="text_delta", text_delta="partial"),
            LLMStreamEvent(type="error", error="late failure"),
        ]
        before = fallback.stream_calls
        late_events = [
            event
            async for event in registry.stream_chat(
                user_text="hello again",
                profile=TEST_PROFILE,
            )
        ]
        self.assertEqual(["text_delta", "error"], [
            event.type
            for event in late_events
        ])
        self.assertEqual(before, fallback.stream_calls)


class LLMPlanningRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_records_skill_catalog_version(self):
        provider = _FakeProvider(
            "dashscope",
            chat_text=(
                '{"skill_id":"move_to_home","skill_name":"回到安全位置",'
                '"parameters":{},"reasoning":"matched","confidence":0.99}'
            ),
        )
        registry = LLMRegistry(
            config=_config(),
            providers={"dashscope": provider},
        )

        plan = await registry.skill_planner.plan(
            "回到安全位置",
            [{
                "id": "move_to_home",
                "name": "回到安全位置",
                "category": "移动",
                "description": "回到安全位置",
                "parameters": [],
                "examples": [],
            }],
        )

        self.assertTrue(plan.is_valid())
        assert plan.provenance is not None
        artifacts = {
            artifact.name: artifact
            for artifact in plan.provenance.artifacts
        }
        self.assertEqual("1", artifacts["skill_catalog"].version)
        self.assertEqual(64, len(artifacts["skill_catalog"].sha256))

    async def test_classifier_does_not_silently_accept_invalid_json(self):
        classifier = InstructionClassifier(
            llm=_FakeProvider("openai", chat_text="not json")
        )

        with self.assertRaises(LLMResponseParseError):
            await classifier.classify("hello")

    async def test_offline_golden_dataset_passes(self):
        report = run_regression_suite()

        self.assertTrue(report.succeeded)
        self.assertEqual(14, report.total)
        self.assertEqual(14, report.passed)

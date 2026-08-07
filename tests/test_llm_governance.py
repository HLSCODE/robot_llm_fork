from __future__ import annotations

import unittest
from typing import Any

from src.configuration.settings import LLMSettings, SecretSettings
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
        usage: dict[str, Any] | None = None,
        available: bool = True,
    ) -> None:
        self.name = name
        self.chat_text = chat_text
        self.chat_error = chat_error
        self.stream_events = stream_events or [
            LLMStreamEvent(type="done", text=chat_text)
        ]
        self.usage = usage
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
            usage=self.usage,
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
) -> LLMSettings:
    return LLMSettings(
        llm_fallback_providers=fallbacks,
        llm_circuit_failure_threshold=threshold,
        llm_circuit_recovery_seconds=30.0,
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
            settings=_config(),
            secrets=SecretSettings(),
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

    async def test_metrics_track_latency_usage_reported_cost_and_failure(self):
        provider = _FakeProvider(
            "openai",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
                "cost_usd": 0.004,
            },
        )
        registry = LLMRegistry(
            settings=_config(),
            secrets=SecretSettings(),
            providers={"openai": provider},
        )

        await registry.chat(user_text="hello", profile=TEST_PROFILE)
        provider.chat_error = LLMProviderError("failed")
        with self.assertRaises(LLMProviderError):
            await registry.chat(
                user_text="hello",
                profile=TEST_PROFILE,
                provider="openai",
            )

        snapshot = registry.metrics_snapshot()
        self.assertEqual(2, snapshot.calls_total)
        self.assertEqual(1, snapshot.calls_succeeded_total)
        self.assertEqual(1, snapshot.calls_failed_total)
        self.assertEqual(12, snapshot.input_tokens_total)
        self.assertEqual(8, snapshot.output_tokens_total)
        self.assertEqual(20, snapshot.tokens_total)
        self.assertEqual(1, snapshot.reported_cost_calls_total)
        self.assertAlmostEqual(0.004, snapshot.reported_cost_usd_total)
        self.assertEqual(
            {"openai": 1},
            dict(snapshot.successful_provider_calls),
        )
        self.assertEqual(
            {"openai-model": 1},
            dict(snapshot.successful_model_calls),
        )
        self.assertAlmostEqual(0.5, snapshot.to_dict()["failure_rate"])

    async def test_stream_metrics_use_terminal_event_without_storing_payload(self):
        provider = _FakeProvider(
            "openai",
            stream_events=[
                LLMStreamEvent(type="text_delta", text_delta="secret-response"),
                LLMStreamEvent(
                    type="done",
                    text="secret-response",
                    metrics={
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "total_tokens": 5,
                        }
                    },
                ),
            ],
        )
        registry = LLMRegistry(
            settings=_config(),
            secrets=SecretSettings(),
            providers={"openai": provider},
        )

        events = [
            event
            async for event in registry.stream_chat(
                user_text="secret-request",
                profile=TEST_PROFILE,
            )
        ]

        self.assertEqual("done", events[-1].type)
        payload = registry.metrics_snapshot().to_dict()
        self.assertEqual(1, payload["calls_succeeded_total"])
        self.assertEqual(5, payload["tokens_total"])
        self.assertNotIn("secret-request", str(payload))
        self.assertNotIn("secret-response", str(payload))

    async def test_fallback_is_configured_and_explicit_provider_is_pinned(self):
        primary = _FakeProvider(
            "openai",
            chat_error=LLMProviderError("primary failed"),
        )
        fallback = _FakeProvider("dashscope", chat_text="fallback")
        registry = LLMRegistry(
            settings=_config(fallbacks=("dashscope",)),
            secrets=SecretSettings(),
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
            settings=_config(
                fallbacks=("dashscope",),
                threshold=2,
            ),
            secrets=SecretSettings(),
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
            settings=_config(fallbacks=("dashscope",)),
            secrets=SecretSettings(),
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
    async def test_planner_records_command_catalog_version(self):
        provider = _FakeProvider(
            "dashscope",
            chat_text=(
                '{"command":{"kind":"skill","skill_id":"move_to_home",'
                '"parameters":{}},"reasoning":"matched","confidence":0.99}'
            ),
        )
        registry = LLMRegistry(
            settings=_config(),
            secrets=SecretSettings(),
            providers={"dashscope": provider},
        )

        plan = await registry.command_planner.plan(
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
        self.assertEqual("2", artifacts["command_catalog"].version)
        self.assertEqual(64, len(artifacts["command_catalog"].sha256))

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

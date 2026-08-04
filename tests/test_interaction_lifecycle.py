from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from src.application import CommandRuntime
from src.configuration.settings import LLMSettings, SecretSettings
from src.execution import ExecutionSnapshot, ExecutionState
from src.llm.providers.openai_compatible import OpenAICompatibleClient
from src.llm.registry import LLMRegistry
from src.llm.tasks.classifier import normalize_instruction_classification
from src.llm.types import LLMMessage
from src.voice_interaction import VoiceInteractionController


class _Execution:
    def snapshot(self):
        return ExecutionSnapshot(None, ExecutionState.IDLE)


class _SkillEngine:
    def list_all_skills(self):
        return []


class _BlockingClassifier:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def classify(self, _text):
        self.started.set()
        await asyncio.Event().wait()


class _CloseableProvider:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1

    def get_provider_name(self) -> str:
        return "fake"


class _BlockingStream:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.close_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.started.set()
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.close_count += 1


class _Completions:
    def __init__(self, stream: _BlockingStream) -> None:
        self._stream = stream

    async def create(self, **_request):
        return self._stream


def _controller(
    classifier: _BlockingClassifier,
    *,
    timeout_s: float,
) -> VoiceInteractionController:
    registry = SimpleNamespace(instruction_classifier=classifier)
    runtime = CommandRuntime(
        execution=_Execution(),
        skill_engine=_SkillEngine(),
    )
    return VoiceInteractionController(
        registry,
        command_runtime=runtime,
        source="test",
        turn_timeout_s=timeout_s,
    )


class InteractionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_timeout_cancels_blocked_classifier(self):
        controller = _controller(
            _BlockingClassifier(),
            timeout_s=0.01,
        )

        events = [
            event
            async for event in controller.handle_text(
                "hello",
                require_awake=False,
            )
        ]

        self.assertEqual("interaction_timeout", events[-1].data["code"])

    async def test_active_turn_can_be_cancelled(self):
        classifier = _BlockingClassifier()
        controller = _controller(classifier, timeout_s=5)

        async def collect():
            return [
                event
                async for event in controller.handle_text(
                    "hello",
                    require_awake=False,
                )
            ]

        task = asyncio.create_task(collect())
        await classifier.started.wait()
        self.assertTrue(controller.cancel_active_turn())
        events = await task

        self.assertEqual("interaction_cancelled", events[-1].data["code"])

    async def test_concurrent_turn_is_rejected(self):
        classifier = _BlockingClassifier()
        controller = _controller(classifier, timeout_s=5)
        first = asyncio.create_task(
            _collect(controller, "first")
        )
        await classifier.started.wait()

        second = await _collect(controller, "second")
        controller.cancel_active_turn()
        await first

        self.assertEqual("interaction_busy", second[0].data["code"])


class ClassifierContractTests(unittest.TestCase):
    def test_session_and_execution_control_are_distinct(self):
        session = normalize_instruction_classification({
            "intent": "session_control",
            "session_action": "pause_session",
        })
        execution = normalize_instruction_classification({
            "intent": "execution_control",
            "execution_action": "pause",
        })

        self.assertEqual("pause_session", session["session_action"])
        self.assertEqual("none", session["execution_action"])
        self.assertEqual("pause", execution["execution_action"])
        self.assertNotIn("Instruction", execution)
        self.assertNotIn("is_Instruction", execution)


class LLMRegistryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_is_idempotent_and_releases_loaded_providers(self):
        provider = _CloseableProvider()
        registry = LLMRegistry(
            settings=LLMSettings(),
            secrets=SecretSettings(),
            providers={"openai": provider},
        )

        await registry.close()
        await registry.close()

        self.assertEqual(1, provider.close_count)
        self.assertEqual((), registry.loaded_provider_names)
        with self.assertRaises(RuntimeError):
            registry.get_provider()

    async def test_cancelled_stream_closes_upstream_response(self):
        stream = _BlockingStream()
        client = OpenAICompatibleClient(
            provider_name="test",
            api_key="",
        )
        client._async_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions(stream))
        )
        client._available = True

        async def consume() -> None:
            async for _event in client.stream_chat(
                [LLMMessage(role="user", content="hello")]
            ):
                pass

        task = asyncio.create_task(consume())
        await stream.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(1, stream.close_count)


async def _collect(
    controller: VoiceInteractionController,
    text: str,
):
    return [
        event
        async for event in controller.handle_text(
            text,
            require_awake=False,
        )
    ]

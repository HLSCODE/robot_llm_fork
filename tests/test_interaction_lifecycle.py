from __future__ import annotations

import asyncio
import json
from threading import Event, Thread
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.application import CommandRuntime
from src.configuration.settings import LLMSettings, SecretSettings
from src.execution import ExecutionSnapshot, ExecutionState
from src.llm.providers.openai_compatible import OpenAICompatibleClient
from src.llm.providers.minicpm_realtime import MiniCPMRealtimeClient
from src.llm.registry import LLMRegistry
from src.llm.routing import ProviderHealthTracker, RoutedLLMClient
from src.llm.tasks.classifier import normalize_instruction_classification
from src.llm.tasks.profiles import GENERAL_CHAT_PROFILE
from src.llm.types import LLMMessage
from src.voice_interaction import VoiceInteractionController
from src.gui.views.ai_assistant import VoiceSessionWorker


class _Execution:
    def snapshot(self):
        return ExecutionSnapshot(None, ExecutionState.IDLE)


class _SkillEngine:
    def list_all_skills(self):
        return []


class _Catalog:
    def entries(self):
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


class _BlockingVoiceController:
    def __init__(self) -> None:
        self.started = Event()
        self.closed = Event()

    async def handle_text(self, _text, *, require_awake):
        del require_awake
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed.set()
        if False:
            yield None


class _RealtimeWebSocket:
    def __init__(self) -> None:
        self._packets = iter((
            {"type": "session.queue_done"},
            {"type": "session.created"},
            {"type": "response.done", "text": "ok"},
        ))

    async def recv(self):
        return json.dumps(next(self._packets))

    async def send(self, _payload):
        return None


class _RealtimeConnection:
    def __init__(self, websocket: _RealtimeWebSocket) -> None:
        self._websocket = websocket
        self.close_count = 0

    async def __aenter__(self):
        return self._websocket

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.close_count += 1
        return False


class _BlockingRealtimeWebSocket(_RealtimeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self._initial_packets = iter((
            {"type": "session.queue_done"},
            {"type": "session.created"},
        ))
        self.response_started = asyncio.Event()

    async def recv(self):
        try:
            packet = next(self._initial_packets)
        except StopIteration:
            self.response_started.set()
            await asyncio.Event().wait()
        return json.dumps(packet)


def _controller(
    classifier: _BlockingClassifier,
    *,
    timeout_s: float,
) -> VoiceInteractionController:
    registry = SimpleNamespace(instruction_classifier=classifier)
    runtime = CommandRuntime(
        execution=_Execution(),
        skill_engine=_SkillEngine(),
        composition=object(),
        workflow_compiler=object(),
        catalog=_Catalog(),
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


class VoiceWorkerLifecycleTests(unittest.TestCase):
    def test_stop_waits_for_root_task_and_async_generator_cleanup(self):
        controller = _BlockingVoiceController()
        worker = VoiceSessionWorker(controller, "hello", require_awake=False)
        thread = Thread(target=worker.run, daemon=True)

        thread.start()
        self.assertTrue(controller.started.wait(timeout=1))
        worker.stop()
        worker.stop()
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(controller.closed.is_set())


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
    async def test_routed_minicpm_cancellation_closes_connection_once(self):
        websocket = _BlockingRealtimeWebSocket()
        connection = _RealtimeConnection(websocket)
        provider = MiniCPMRealtimeClient(
            gateway_host="localhost",
            gateway_port=8006,
            ws_scheme="ws",
        )
        routed = RoutedLLMClient(
            profile=GENERAL_CHAT_PROFILE,
            primary_provider="minicpm",
            fallback_providers=(),
            explicit_provider=True,
            provider_loader=lambda _name: provider,
            health=ProviderHealthTracker(
                failure_threshold=3,
                recovery_seconds=30,
            ),
        )

        async def consume() -> None:
            async for _event in routed.stream_chat(
                [LLMMessage(role="user", content="hello")]
            ):
                pass

        with patch(
            "src.llm.providers.minicpm_realtime.websockets",
            SimpleNamespace(connect=lambda _url, **_options: connection),
        ):
            task = asyncio.create_task(consume())
            await websocket.response_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(1, connection.close_count)
        await asyncio.get_running_loop().shutdown_asyncgens()

    async def test_minicpm_close_cancels_active_read_without_asyncgen_race(self):
        websocket = _BlockingRealtimeWebSocket()
        connection = _RealtimeConnection(websocket)

        client = MiniCPMRealtimeClient(
            gateway_host="localhost",
            gateway_port=8006,
            ws_scheme="ws",
        )
        with patch(
            "src.llm.providers.minicpm_realtime.websockets",
            SimpleNamespace(connect=lambda _url, **_options: connection),
        ):
            stream = client.stream_chat(
                [LLMMessage(role="user", content="hello")]
            )
            first_event = await anext(stream)
            read_task = asyncio.create_task(anext(stream))
            await websocket.response_started.wait()
            await stream.aclose()

        with self.assertRaises(asyncio.CancelledError):
            await read_task
        self.assertEqual("session_started", first_event.type)
        self.assertEqual(1, connection.close_count)
        await asyncio.get_running_loop().shutdown_asyncgens()

    async def test_minicpm_realtime_uses_bounded_transport_close(self):
        websocket = _RealtimeWebSocket()
        connect_options = {}

        def connect(_url, **options):
            connect_options.update(options)
            return _RealtimeConnection(websocket)

        client = MiniCPMRealtimeClient(
            gateway_host="localhost",
            gateway_port=8006,
            ws_scheme="ws",
            timeout_s=60,
        )
        with patch(
            "src.llm.providers.minicpm_realtime.websockets",
            SimpleNamespace(connect=connect),
        ):
            events = [
                event
                async for event in client.stream_chat(
                    [LLMMessage(role="user", content="hello")]
                )
            ]

        self.assertEqual(["session_started", "done"], [event.type for event in events])
        self.assertEqual(2.0, connect_options["close_timeout"])

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

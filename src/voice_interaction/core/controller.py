"""Shared interaction controller for GUI text, speech, and remote surfaces."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from threading import Lock, RLock

from ...application.command_runtime import CommandRuntime
from ...llm.errors import LLMError
from .router import VoiceIntentRouter
from .session import VoiceSession
from .types import VoiceEvent, VoiceSessionState
from .wake_feedback import WakeFeedback

logger = logging.getLogger(__name__)


class VoiceInteractionController:
    """Coordinate session state, intent classification, and task routing."""

    def __init__(
        self,
        llm_registry,
        command_runtime: CommandRuntime,
        source: str,
        camera_provider=None,
        session: VoiceSession | None = None,
        timeout_s: float = 30.0,
        turn_timeout_s: float = 90.0,
        tts_enabled: bool = False,
        history_turns: int = 6,
        wake_feedback: WakeFeedback | None = None,
    ) -> None:
        self.llm_registry = llm_registry
        self.command_runtime = command_runtime
        self.source = str(source)
        self.camera_provider = camera_provider
        self.session = session or VoiceSession(timeout_s=timeout_s)
        if turn_timeout_s <= 0:
            raise ValueError("turn_timeout_s must be positive")
        self.turn_timeout_s = float(turn_timeout_s)
        self.wake_feedback = wake_feedback or WakeFeedback()
        self._turn_lock = Lock()
        self._active_lock = RLock()
        self._active_task: asyncio.Task | None = None
        self._active_loop: asyncio.AbstractEventLoop | None = None
        self.router = VoiceIntentRouter(
            llm_registry=llm_registry,
            session=self.session,
            command_runtime=command_runtime,
            source=source,
            camera_provider=camera_provider,
            tts_enabled=tts_enabled,
            history_turns=history_turns,
        )

    def wake(self) -> VoiceEvent:
        self.session.wake()
        return VoiceEvent(type="session_started", text="机器人已唤醒")

    async def stream_wake_feedback(self) -> AsyncIterator[VoiceEvent]:
        async for event in self.wake_feedback.stream(self.llm_registry):
            yield event

    def sleep(self) -> VoiceEvent:
        self.cancel_active_turn()
        self.session.sleep()
        return VoiceEvent(type="session_ended", text="会话已结束")

    def resume(self) -> VoiceEvent:
        self.session.resume()
        return VoiceEvent(type="session_resumed", text="会话已恢复")

    def check_timeout(self) -> VoiceEvent | None:
        if not self.session.is_expired():
            return None
        self.cancel_active_turn()
        self.session.sleep()
        return VoiceEvent(type="session_ended", text="会话已超时")

    def cancel_active_turn(self) -> bool:
        with self._active_lock:
            task = self._active_task
            loop = self._active_loop
        if task is None or loop is None or task.done():
            return False
        loop.call_soon_threadsafe(task.cancel)
        return True

    async def handle_text(
        self,
        text: str,
        *,
        require_awake: bool = True,
    ) -> AsyncIterator[VoiceEvent]:
        if not self._turn_lock.acquire(blocking=False):
            yield VoiceEvent(
                type="error",
                text="另一个交互请求正在处理中",
                data={"code": "interaction_busy"},
            )
            return

        current_task = asyncio.current_task()
        with self._active_lock:
            self._active_task = current_task
            self._active_loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(self.turn_timeout_s):
                async for event in self._run_turn(
                    text,
                    require_awake=require_awake,
                ):
                    yield event
        except TimeoutError:
            yield VoiceEvent(
                type="error",
                text="交互处理超时",
                data={"code": "interaction_timeout"},
            )
        except asyncio.CancelledError:
            yield VoiceEvent(
                type="error",
                text="交互已取消",
                data={"code": "interaction_cancelled"},
            )
        finally:
            with self._active_lock:
                if self._active_task is current_task:
                    self._active_task = None
                    self._active_loop = None
            self._turn_lock.release()

    async def _run_turn(
        self,
        text: str,
        *,
        require_awake: bool,
    ) -> AsyncIterator[VoiceEvent]:
        text = (text or "").strip()
        was_paused = self.session.state is VoiceSessionState.PAUSED
        if require_awake:
            if self.session.state is VoiceSessionState.SLEEPING:
                yield VoiceEvent(type="error", text="机器人未唤醒")
                return
            if self.session.is_expired():
                self.session.sleep()
                yield VoiceEvent(type="session_ended", text="会话已超时")
                return
        elif self.session.state is VoiceSessionState.SLEEPING:
            self.session.wake()
            yield VoiceEvent(
                type="session_started",
                text="GUI 对话会话已开始",
            )

        self.session.touch()
        self.session.add_history("user", text)
        try:
            intent = await self.llm_registry.instruction_classifier.classify(
                text
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except LLMError as exc:
            logger.warning(
                "intent classification service failed: %s",
                type(exc).__name__,
            )
            yield VoiceEvent(
                type="error",
                text="AI 意图识别服务暂时不可用，请稍后重试。",
                data={
                    "code": "llm_classification_unavailable",
                    "error_type": type(exc).__name__,
                },
            )
            return
        except ValueError as exc:
            logger.warning("intent classification failed: %s", exc)
            intent = {
                "intent": "chat",
                "is_addressed_to_robot": True,
                "should_end_session": False,
                "session_action": "none",
                "execution_action": "none",
            }

        yield VoiceEvent(type="intent", intent=intent, data={"input": text})
        if was_paused and not (
            intent.get("intent") == "session_control"
            and (
                intent.get("session_action")
                in {"resume_session", "end_session"}
            )
        ):
            yield VoiceEvent(
                type="error",
                text="会话已暂停，请先恢复或结束会话",
                data={"code": "session_paused"},
            )
            return
        assistant_parts: list[str] = []
        assistant_done = ""
        try:
            async for event in self.router.route(text, intent):
                if event.type == "text_delta" and event.text_delta:
                    assistant_parts.append(event.text_delta)
                elif event.type == "done" and event.text:
                    assistant_done = event.text
                yield event
        finally:
            assistant_text = "".join(assistant_parts).strip()
            assistant_text = assistant_text or assistant_done.strip()
            if assistant_text:
                self.session.add_history("assistant", assistant_text)
            if self.session.state is VoiceSessionState.RESPONDING:
                self.session.resume()

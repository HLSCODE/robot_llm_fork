"""
Wake-session voice interaction controller.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Callable, Optional

from .router import VoiceIntentRouter
from .session import VoiceSession
from .types import VoiceEvent, VoiceSessionState
from .wake_feedback import WakeFeedback

logger = logging.getLogger(__name__)


class VoiceInteractionController:
    """Coordinate wake-session state, intent classification, and task routing."""

    def __init__(
        self,
        llm_registry,
        skill_engine=None,
        camera_provider=None,
        session: Optional[VoiceSession] = None,
        timeout_s: float = 30.0,
        cancel_callback: Optional[Callable[[], Any]] = None,
        tts_enabled: bool = False,
        history_turns: int = 6,
        wake_feedback: Optional[WakeFeedback] = None,
    ) -> None:
        self.llm_registry = llm_registry
        self.skill_engine = skill_engine
        self.camera_provider = camera_provider
        self.session = session or VoiceSession(timeout_s=timeout_s)
        self.wake_feedback = wake_feedback or WakeFeedback()
        self.router = VoiceIntentRouter(
            llm_registry=llm_registry,
            session=self.session,
            skill_engine=skill_engine,
            camera_provider=camera_provider,
            cancel_callback=cancel_callback,
            tts_enabled=tts_enabled,
            history_turns=history_turns,
        )

    def wake(self) -> VoiceEvent:
        self.session.wake()
        return VoiceEvent(type="session_started", text="机器人已唤醒")

    async def stream_wake_feedback(self) -> AsyncIterator[VoiceEvent]:
        """Stream the acknowledgement for a physical wake-word trigger."""
        async for event in self.wake_feedback.stream(self.llm_registry):
            yield event

    def sleep(self) -> VoiceEvent:
        self.session.sleep()
        return VoiceEvent(type="session_ended", text="会话已结束")

    def resume(self) -> VoiceEvent:
        self.session.resume()
        return VoiceEvent(type="session_resumed", text="会话已恢复")

    def check_timeout(self) -> Optional[VoiceEvent]:
        """Return a session_ended event if the active session has timed out."""
        if self.session.is_expired():
            self.session.sleep()
            return VoiceEvent(type="session_ended", text="会话已超时")
        return None

    async def handle_text(
        self,
        text: str,
        *,
        require_awake: bool = True,
    ) -> AsyncIterator[VoiceEvent]:
        text = text or ""
        if require_awake:
            if self.session.state == VoiceSessionState.SLEEPING:
                yield VoiceEvent(type="error", text="机器人未唤醒")
                return

            if self.session.is_expired():
                self.session.sleep()
                yield VoiceEvent(type="session_ended", text="会话已超时")
                return

            if self.session.state == VoiceSessionState.PAUSED:
                self.session.resume()
                yield VoiceEvent(type="session_resumed", text="会话已恢复")

        self.session.touch()
        self.session.add_history("user", text)

        try:
            intent = await self.llm_registry.instruction_classifier.classify(text)
        except Exception as exc:
            logger.warning("语音会话意图识别失败: %s", exc)
            intent = {
                "intent": "chat",
                "is_addressed_to_robot": True,
                "should_end_session": False,
                "session_action": "none",
                "Instruction": text,
                "is_Instruction": False,
            }

        yield VoiceEvent(type="intent", intent=intent, data={"input": text})

        assistant_text_parts: list[str] = []
        assistant_done_text = ""
        try:
            async for event in self.router.route(text, intent):
                if event.type == "text_delta" and event.text_delta:
                    assistant_text_parts.append(event.text_delta)
                elif event.type == "done" and event.text:
                    assistant_done_text = event.text
                yield event
        except Exception as exc:
            logger.error("语音会话路由失败: %s", exc, exc_info=True)
            yield VoiceEvent(type="error", text=f"语音会话处理失败: {exc}")
        finally:
            assistant_text = "".join(assistant_text_parts).strip() or assistant_done_text.strip()
            if assistant_text:
                self.session.add_history("assistant", assistant_text)
            if self.session.state == VoiceSessionState.RESPONDING:
                self.session.resume()

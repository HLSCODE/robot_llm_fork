"""
Intent router for wake-session interaction.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Callable, Optional

from ...llm import LLMCapability, LLMPlanResult, LLMStreamEvent, VOICE_FEEDBACK_PROFILE
from ...skill_system.models import SkillMatchResult
from ..adapters import CameraCaptureError
from .session import VoiceSession
from .types import VoiceEvent, VoiceSessionState

logger = logging.getLogger(__name__)

CancelCallback = Callable[[], Any]


class VoiceIntentRouter:
    """Route classified user intent to the corresponding LLM or robot task."""

    def __init__(
        self,
        llm_registry,
        session: VoiceSession,
        skill_engine=None,
        camera_provider=None,
        cancel_callback: Optional[CancelCallback] = None,
        tts_enabled: bool = False,
        auto_execute_command: bool = False,
    ) -> None:
        self.llm_registry = llm_registry
        self.session = session
        self.skill_engine = skill_engine
        self.camera_provider = camera_provider
        self.cancel_callback = cancel_callback
        self.tts_enabled = bool(tts_enabled)
        self.auto_execute_command = bool(auto_execute_command)

    async def route(
        self,
        text: str,
        intent: dict[str, Any],
    ) -> AsyncIterator[VoiceEvent]:
        if not intent.get("is_addressed_to_robot", True):
            yield VoiceEvent(type="ignored", text="这句话不是对机器人说的", intent=intent)
            return

        intent_type = str(intent.get("intent", "chat") or "chat")
        if intent_type == "session_control":
            async for event in self._handle_session_control(intent):
                yield event
            return

        if intent_type == "chat":
            async for event in self._handle_chat(text):
                yield event
            return

        if intent_type == "command":
            async for event in self._handle_command(text):
                yield event
            return

        if intent_type == "vision_question":
            async for event in self._handle_vision(text):
                yield event
            return

        yield VoiceEvent(type="error", text=f"未知意图: {intent_type}", intent=intent)

    async def _handle_chat(self, text: str) -> AsyncIterator[VoiceEvent]:
        self.session.responding()
        try:
            async for event in self.llm_registry.task_runner.stream_chat(
                user_text=text,
                voice_response=self._chat_voice_response_enabled(),
            ):
                yield self._from_llm_event(event)
        finally:
            self.session.resume()

    async def _handle_command(self, text: str) -> AsyncIterator[VoiceEvent]:
        if self.skill_engine is None:
            async for event in self._stream_feedback("技能系统未初始化，暂时无法执行动作。"):
                yield event
            return

        self.session.responding()
        try:
            skill_summaries = self.skill_engine.list_all_skills()
            plan: LLMPlanResult = await self.llm_registry.skill_planner.plan(
                text,
                skill_summaries,
            )

            if not plan.is_valid():
                feedback = plan.error or "没有匹配到可执行技能，请换一种说法。"
                async for event in self._stream_feedback(
                    feedback,
                    data={"plan": plan.__dict__},
                ):
                    yield event
                return

            skill_match = SkillMatchResult(
                skill_id=plan.skill_id,
                skill_name=plan.skill_name,
                confidence=plan.confidence,
                extracted_params=plan.parameters,
                reasoning=plan.reasoning,
            )
            skill_info = self.skill_engine.get_skill_info(plan.skill_id)
            sequence, validation = self.skill_engine.parse_and_expand(skill_match)

            if not validation.is_valid:
                async for event in self._stream_feedback(
                    validation.message or "动作参数校验未通过，暂时不能执行。",
                    data={"plan": plan.__dict__, "warnings": validation.warnings},
                ):
                    yield event
                return

            feedback_text = f"已生成动作预览，共 {len(sequence)} 步，请确认是否执行。"
            yield VoiceEvent(
                type="command_preview",
                text=feedback_text,
                data={
                    "plan": plan.__dict__,
                    "skill_info": skill_info or {},
                    "sequence": [item.to_dict() for item in sequence],
                    "validation": {
                        "is_valid": validation.is_valid,
                        "message": validation.message,
                        "warnings": validation.warnings,
                    },
                    "auto_execute": self.auto_execute_command,
                    "suppress_message": True,
                },
            )
            async for event in self._stream_feedback(
                feedback_text,
                humanize=True,
            ):
                yield event

            if self.auto_execute_command:
                yield VoiceEvent(type="command_started", text="自动执行暂未接入 GUI 第一阶段")
                async for event in self._stream_feedback("自动执行暂未接入当前界面，请手动确认执行。"):
                    yield event
        finally:
            self.session.resume()

    async def _handle_vision(self, text: str) -> AsyncIterator[VoiceEvent]:
        if self.camera_provider is None:
            async for event in self._stream_feedback("视觉系统未初始化，暂时看不到环境。"):
                yield event
            return

        self.session.responding()
        try:
            try:
                images = list(self.camera_provider.capture_llm_parts())
            except CameraCaptureError as exc:
                logger.warning("语音视觉取帧失败: %s", exc.technical_detail or exc.user_message)
                async for event in self._stream_feedback(
                    exc.user_message,
                    data={"technical_detail": exc.technical_detail},
                    humanize_detail=exc.technical_detail,
                ):
                    yield event
                return
            except Exception as exc:
                logger.warning("语音视觉取帧失败: %s", exc)
                async for event in self._stream_feedback(
                    "我现在还没看到摄像头画面，暂时没法回答这个问题。",
                    data={"technical_detail": str(exc)},
                    humanize_detail=str(exc),
                ):
                    yield event
                return

            if not images:
                async for event in self._stream_feedback("没有获取到摄像头画面，暂时无法回答视觉问题。"):
                    yield event
                return

            yield VoiceEvent(
                type="vision_started",
                text=f"已采集 {len(images)} 路视觉输入",
            )
            async for event in self.llm_registry.vision_fusion.stream_observe(
                images=images,
                question=text,
                voice_response=self._vision_voice_response_enabled(),
            ):
                yield self._from_llm_event(event)
        finally:
            self.session.resume()

    async def _handle_session_control(self, intent: dict[str, Any]) -> AsyncIterator[VoiceEvent]:
        action = str(intent.get("session_action", "none") or "none")
        self.session.responding()
        if action == "end_session" or intent.get("should_end_session"):
            async for event in self._stream_feedback("好的，会话已结束。", intent=intent):
                yield event
            self.session.sleep()
            yield VoiceEvent(
                type="session_ended",
                text="会话已结束",
                intent=intent,
                data={"preserve_audio": True},
            )
            return

        if action == "cancel_task":
            await self._cancel_current_task()
            self.session.resume()
            async for event in self._stream_feedback("已取消当前任务。", intent=intent):
                yield event
            return

        if action == "pause":
            async for event in self._stream_feedback("好的，我先暂停回应。", intent=intent):
                yield event
            self.session.pause()
            yield VoiceEvent(type="session_paused", text="会话已暂停", intent=intent)
            return

        async for event in self._stream_feedback("已收到会话控制指令。", intent=intent):
            yield event

    async def _cancel_current_task(self) -> None:
        if self.cancel_callback is None:
            return
        result = self.cancel_callback()
        if hasattr(result, "__await__"):
            await result

    def _chat_voice_response_enabled(self) -> bool:
        return self._voice_response_enabled("get_chat_client", "chat")

    def _vision_voice_response_enabled(self) -> bool:
        return self._voice_response_enabled("get_vision_client", "vision")

    def _feedback_voice_response_enabled(self) -> bool:
        return self._voice_response_enabled("get_feedback_client", "feedback")

    def _voice_response_enabled(self, getter_name: str, task_name: str) -> bool:
        if not self.tts_enabled:
            logger.info("%s voice stream disabled: VOICE_TTS_ENABLED=false", task_name)
            return False

        if not self._registry_client_supports_tts(getter_name):
            logger.info("%s voice stream disabled: current LLM provider does not support TTS", task_name)
            return False

        return True

    def _registry_client_supports_tts(self, getter_name: str) -> bool:
        getter = getattr(self.llm_registry, getter_name, None)
        if getter is None:
            return False
        try:
            return LLMCapability.TTS in getter().capabilities()
        except Exception:
            logger.debug("无法判断 LLM client 是否支持 TTS: %s", getter_name, exc_info=True)
            return False

    async def _stream_feedback(
        self,
        text: str,
        intent: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        humanize_detail: Optional[str] = None,
        humanize: bool = False,
    ) -> AsyncIterator[VoiceEvent]:
        feedback_text = (text or "").strip()
        if not feedback_text:
            return

        if humanize or humanize_detail:
            emitted = False
            try:
                user_text = f"建议回复：{feedback_text}"
                if humanize_detail:
                    user_text = f"{user_text}\n内部详情：{humanize_detail}"
                async for llm_event in self.llm_registry.task_runner.stream_chat(
                    user_text=user_text,
                    profile=VOICE_FEEDBACK_PROFILE,
                    voice_response=self._feedback_voice_response_enabled(),
                ):
                    event = self._from_llm_event(llm_event)
                    event.intent = intent
                    if data:
                        event.data.update(data)

                    if event.type == "error":
                        logger.warning("拟人化反馈生成失败: %s", event.text)
                        break

                    if event.type in ("text_delta", "audio_delta", "done"):
                        emitted = True
                    yield event
            except Exception as exc:
                logger.warning("拟人化反馈生成失败: %s", exc)

            if emitted:
                return

        if not self._feedback_voice_response_enabled():
            yield VoiceEvent(
                type="done",
                text=feedback_text,
                intent=intent,
                data=data or {},
            )
            return

        emitted = False
        try:
            async for llm_event in self.llm_registry.repeat_task.stream_repeat(
                feedback_text,
                voice_response=True,
            ):
                event = self._from_llm_event(llm_event)
                event.intent = intent
                if data:
                    event.data.update(data)

                if event.type == "error":
                    logger.warning("语音反馈生成失败: %s", event.text)
                    break

                if event.type in ("text_delta", "audio_delta", "done"):
                    emitted = True
                yield event
        except Exception as exc:
            logger.warning("语音反馈生成失败: %s", exc)

        if not emitted:
            yield VoiceEvent(
                type="done",
                text=feedback_text,
                intent=intent,
                data=data or {},
            )

    @staticmethod
    def _from_llm_event(event: LLMStreamEvent) -> VoiceEvent:
        if event.type == "text_delta":
            return VoiceEvent(type="text_delta", text_delta=event.text_delta, data={"raw": event.raw})
        if event.type == "audio_delta":
            return VoiceEvent(type="audio_delta", audio_data=event.audio_data, data={"raw": event.raw})
        if event.type == "done":
            return VoiceEvent(
                type="done",
                text=event.text,
                audio_data=event.audio_data,
                data={"metrics": event.metrics, "raw": event.raw},
            )
        if event.type == "error":
            return VoiceEvent(type="error", text=event.error or "LLM 调用失败", data={"raw": event.raw})
        return VoiceEvent(type="done", text=event.text, data={"raw": event.raw, "event_type": event.type})

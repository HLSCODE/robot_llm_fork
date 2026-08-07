"""Intent routing for text and speech interaction surfaces."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from ...application.command_runtime import (
    CommandRuntime,
)
from ...application.command_catalog import CommandResolutionStatus
from ...domain.commands import ExecutionControlAction, ExecutionControlCommand
from ...execution import ExecutionStateError
from ...llm import (
    VOICE_FEEDBACK_PROFILE,
    LLMCapability,
    LLMMessage,
    CommandPlanResult,
    LLMStreamEvent,
)
from ..adapters import CameraCaptureError
from .session import VoiceSession
from .types import VoiceEvent, VoiceEventType

logger = logging.getLogger(__name__)


class VoiceIntentRouter:
    """Route classified intent through shared application policies."""

    def __init__(
        self,
        llm_registry: Any,
        session: VoiceSession,
        command_runtime: CommandRuntime,
        source: str,
        camera_provider: Any | None = None,
        tts_enabled: bool = False,
        history_turns: int = 6,
    ) -> None:
        self.llm_registry = llm_registry
        self.session = session
        self.command_runtime = command_runtime
        self.source = str(source)
        self.camera_provider = camera_provider
        self.tts_enabled = bool(tts_enabled)
        self.history_turns = max(0, int(history_turns))

    async def route(
        self,
        text: str,
        intent: dict[str, Any],
    ) -> AsyncIterator[VoiceEvent]:
        if not intent.get("is_addressed_to_robot", True):
            yield VoiceEvent(
                type="ignored",
                text="这句话不是对机器人说的",
                intent=intent,
            )
            return

        intent_type = str(intent.get("intent", "chat") or "chat")
        handlers = {
            "chat": lambda: self._handle_chat(text),
            "command": lambda: self._handle_command(text),
            "vision_question": lambda: self._handle_vision(text),
            "session_control": lambda: self._handle_session_control(intent),
            "execution_control": lambda: self._handle_execution_control(intent),
        }
        handler = handlers.get(intent_type)
        if handler is None:
            yield VoiceEvent(
                type="error",
                text=f"未知意图: {intent_type}",
                intent=intent,
            )
            return
        async for event in handler():
            yield event

    async def _handle_chat(self, text: str) -> AsyncIterator[VoiceEvent]:
        self.session.responding()
        history = self.session.recent_history(self.history_turns)
        messages = [
            LLMMessage(role=item["role"], content=str(item["content"]))
            for item in history
            if item.get("role") in ("user", "assistant")
            and item.get("content")
        ]
        try:
            async for event in self.llm_registry.task_runner.stream_chat(
                user_text=text if not messages else None,
                messages=messages or None,
                voice_response=self._chat_voice_response_enabled(),
            ):
                yield self._from_llm_event(event)
        finally:
            self.session.resume()

    async def _handle_command(self, text: str) -> AsyncIterator[VoiceEvent]:
        self.session.responding()
        try:
            resolution = self.command_runtime.resolve_text(text)
            if resolution.status in {
                CommandResolutionStatus.AMBIGUOUS,
                CommandResolutionStatus.INVALID,
            }:
                async for event in self._stream_feedback(
                    resolution.message,
                    data={"resolution": resolution.status.value},
                ):
                    yield event
                return
            if resolution.command is not None:
                plan = CommandPlanResult(
                    command=resolution.command,
                    reasoning="deterministic command catalog match",
                    confidence=resolution.confidence,
                )
            else:
                plan = await self.llm_registry.command_planner.plan(
                    text,
                    self.command_runtime.command_catalog(),
                )
            if not plan.is_valid():
                async for event in self._stream_feedback(
                    plan.error or "没有匹配到可执行命令，请换一种说法。",
                    data={"plan": plan.to_dict()},
                ):
                    yield event
                return

            command = plan.command
            assert command is not None
            if isinstance(command, ExecutionControlCommand):
                try:
                    yield self._execution_control_event(
                        command.action,
                        intent={"intent": "execution_control"},
                    )
                except (ExecutionStateError, ValueError) as exc:
                    yield VoiceEvent(
                        type="error",
                        text=f"执行控制失败: {exc}",
                        data={"action": command.action.value},
                    )
                return
            preparation = self.command_runtime.prepare(
                command,
                source=self.source,
                plan=plan.to_dict(),
            )
            if preparation.preview is None:
                validation = preparation.validation
                async for event in self._stream_feedback(
                    validation.message or "动作参数校验未通过。",
                    data={
                        "plan": plan.to_dict(),
                        "validation": validation.to_dict(),
                    },
                ):
                    yield event
                return

            preview = preparation.preview
            feedback = (
                f"已生成动作预览，共 {len(preview.sequence)} 步，"
                "请确认是否执行。"
            )
            yield VoiceEvent(
                type="command_preview",
                text=feedback,
                data={
                    **preview.to_dict(),
                    "suppress_message": True,
                },
            )
            async for event in self._stream_feedback(
                feedback,
                humanize=True,
            ):
                yield event
        finally:
            self.session.resume()

    async def _handle_vision(self, text: str) -> AsyncIterator[VoiceEvent]:
        if self.camera_provider is None:
            async for event in self._stream_feedback(
                "视觉系统未初始化，暂时无法获取环境画面。"
            ):
                yield event
            return

        self.session.responding()
        try:
            try:
                images = list(self.camera_provider.capture_llm_parts())
            except CameraCaptureError as exc:
                logger.warning(
                    "camera capture failed: %s",
                    exc.technical_detail or exc.user_message,
                )
                async for event in self._stream_feedback(
                    exc.user_message,
                    data={"technical_detail": exc.technical_detail},
                    humanize_detail=exc.technical_detail,
                ):
                    yield event
                return
            except Exception as exc:
                logger.exception("camera capture failed")
                async for event in self._stream_feedback(
                    "摄像头画面获取失败，暂时无法回答视觉问题。",
                    data={"technical_detail": str(exc)},
                    humanize_detail=str(exc),
                ):
                    yield event
                return

            if not images:
                async for event in self._stream_feedback(
                    "没有获取到摄像头画面。"
                ):
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

    async def _handle_session_control(
        self,
        intent: dict[str, Any],
    ) -> AsyncIterator[VoiceEvent]:
        action = str(intent.get("session_action", "none") or "none")
        if action == "end_session" or intent.get("should_end_session"):
            async for event in self._stream_feedback(
                "好的，会话已结束。",
                intent=intent,
            ):
                yield event
            self.session.sleep()
            yield VoiceEvent(
                type="session_ended",
                text="会话已结束",
                intent=intent,
                data={"preserve_audio": True},
            )
            return
        if action == "pause_session":
            self.session.pause()
            yield VoiceEvent(
                type="session_paused",
                text="会话已暂停",
                intent=intent,
            )
            return
        if action == "resume_session":
            self.session.resume()
            yield VoiceEvent(
                type="session_resumed",
                text="会话已恢复",
                intent=intent,
            )
            return
        yield VoiceEvent(
            type="done",
            text="已收到会话控制指令。",
            intent=intent,
        )

    async def _handle_execution_control(
        self,
        intent: dict[str, Any],
    ) -> AsyncIterator[VoiceEvent]:
        raw_action = str(intent.get("execution_action", "none") or "none")
        try:
            action = ExecutionControlAction(raw_action)
        except ValueError:
            yield VoiceEvent(
                type="error",
                text=f"未知执行控制: {raw_action}",
                intent=intent,
            )
            return
        try:
            yield self._execution_control_event(action, intent=intent)
            return
        except (ExecutionStateError, ValueError) as exc:
            yield VoiceEvent(
                type="error",
                text=f"执行控制失败: {exc}",
                intent=intent,
                data={"action": action.value},
            )
            return

    def _execution_control_event(
        self,
        action: ExecutionControlAction,
        *,
        intent: dict[str, Any],
    ) -> VoiceEvent:
        result = self.command_runtime.control_execution(
            action,
            expected_source=self.source,
        )
        event_type: VoiceEventType = (
            "preview_cancelled"
            if result == "preview_cancelled"
            else "execution_controlled"
        )
        return VoiceEvent(
            type=event_type,
            text=result,
            intent=intent,
            data={"action": action.value, "result": result},
        )

    def _chat_voice_response_enabled(self) -> bool:
        return self._repeat_voice_response_enabled("chat")

    def _vision_voice_response_enabled(self) -> bool:
        return self._voice_response_enabled("get_vision_client", "vision")

    def _feedback_voice_response_enabled(self) -> bool:
        return self._repeat_voice_response_enabled("feedback")

    def _repeat_voice_response_enabled(self, task_name: str) -> bool:
        if not self.tts_enabled:
            return False
        if not self._registry_client_supports_tts("get_repeat_client"):
            logger.info("%s TTS disabled: provider lacks TTS", task_name)
            return False
        return True

    def _voice_response_enabled(
        self,
        getter_name: str,
        task_name: str,
    ) -> bool:
        if not self.tts_enabled:
            return False
        if not self._registry_client_supports_tts(getter_name):
            logger.info("%s TTS disabled: provider lacks TTS", task_name)
            return False
        return True

    def _registry_client_supports_tts(self, getter_name: str) -> bool:
        getter = getattr(self.llm_registry, getter_name, None)
        if getter is None:
            return False
        try:
            return LLMCapability.TTS in getter().capabilities()
        except Exception:
            logger.debug("cannot inspect provider TTS capability", exc_info=True)
            return False

    async def _stream_feedback(
        self,
        text: str,
        intent: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        humanize_detail: str | None = None,
        humanize: bool = False,
    ) -> AsyncIterator[VoiceEvent]:
        feedback = (text or "").strip()
        if not feedback:
            return

        if humanize or humanize_detail:
            emitted = False
            try:
                prompt = f"建议回复：{feedback}"
                if humanize_detail:
                    prompt += f"\n内部详情：{humanize_detail}"
                async for raw_event in self.llm_registry.task_runner.stream_chat(
                    user_text=prompt,
                    profile=VOICE_FEEDBACK_PROFILE,
                    voice_response=self._feedback_voice_response_enabled(),
                ):
                    event = self._from_llm_event(raw_event)
                    event.intent = intent
                    if data:
                        event.data.update(data)
                    if event.type == "error":
                        break
                    emitted = True
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("feedback humanization failed", exc_info=True)
            if emitted:
                return

        if not self._feedback_voice_response_enabled():
            yield VoiceEvent(
                type="done",
                text=feedback,
                intent=intent,
                data=data or {},
            )
            return

        emitted = False
        try:
            async for raw_event in self.llm_registry.repeat_task.stream_repeat(
                feedback,
                voice_response=True,
            ):
                event = self._from_llm_event(raw_event)
                event.intent = intent
                if data:
                    event.data.update(data)
                if event.type == "error":
                    break
                emitted = True
                yield event
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("voice feedback failed", exc_info=True)
        if not emitted:
            yield VoiceEvent(
                type="done",
                text=feedback,
                intent=intent,
                data=data or {},
            )

    @staticmethod
    def _from_llm_event(event: LLMStreamEvent) -> VoiceEvent:
        provenance = (
            event.provenance.to_dict()
            if event.provenance is not None
            else None
        )
        if event.type == "text_delta":
            return VoiceEvent(
                type="text_delta",
                text_delta=event.text_delta,
                data={"raw": event.raw, "provenance": provenance},
            )
        if event.type == "audio_delta":
            return VoiceEvent(
                type="audio_delta",
                audio_data=event.audio_data,
                data={"raw": event.raw, "provenance": provenance},
            )
        if event.type == "done":
            return VoiceEvent(
                type="done",
                text=event.text,
                audio_data=event.audio_data,
                data={
                    "metrics": event.metrics,
                    "raw": event.raw,
                    "provenance": provenance,
                },
            )
        if event.type == "error":
            return VoiceEvent(
                type="error",
                text=event.error or "LLM 调用失败",
                data={"raw": event.raw, "provenance": provenance},
            )
        return VoiceEvent(
            type="done",
            text=event.text,
            data={
                "raw": event.raw,
                "event_type": event.type,
                "provenance": provenance,
            },
        )

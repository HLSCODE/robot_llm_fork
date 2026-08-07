"""Voice acknowledgement emitted after a physical wake-word trigger."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...llm import LLMStreamEvent, REPEAT_PROFILE
from .types import VoiceEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...llm.registry import LLMRegistry


@dataclass(frozen=True)
class WakeFeedback:
    """Generate the configured acknowledgement through ``REPEAT_PROFILE``."""

    enabled: bool = True
    text: str = "明德博士在，请说。"

    async def stream(self, llm_registry: "LLMRegistry") -> AsyncIterator[VoiceEvent]:
        """Return acknowledgement text and audio stream events when enabled."""
        if not self.enabled or not self.text.strip():
            return

        emitted_output = False
        completed = False
        try:
            async for event in llm_registry.repeat_task.stream_repeat(
                self.text,
                profile=REPEAT_PROFILE,
                voice_response=True,
            ):
                if event.type == "error":
                    logger.warning("唤醒语音反馈生成失败: %s", event.error)
                    break

                voice_event = self._from_llm_event(event)
                voice_event.data["wake_feedback"] = True
                if voice_event.type in ("text_delta", "audio_delta"):
                    emitted_output = True
                if voice_event.type == "done":
                    completed = True
                yield voice_event
        except Exception as exc:
            logger.warning("唤醒语音反馈生成失败: %s", exc)

        if not completed:
            # The wake session remains usable even when the realtime provider fails.
            yield VoiceEvent(
                type="done",
                text="" if emitted_output else self.text,
                data={"wake_feedback": True, "fallback": True},
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

"""
Multi-camera vision fusion task.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, List, Optional, Sequence

from ...configuration.settings import TaskRouteSettings
from ..base import BaseLLMClient
from ..response_pipeline import ResponsePipeline
from ..types import LLMCapability, LLMChatResult, LLMContentPart, LLMMessage, LLMStreamEvent
from .profiles import TaskProfile


ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


VISION_FUSION_PROFILE = TaskProfile(
    name="vision_fusion",
    version="1.0.0",
    temperature=0.1,
    max_tokens=512,
    required_capabilities=(
        LLMCapability.CHAT,
        LLMCapability.STREAM_CHAT,
        LLMCapability.VISION_CHAT,
    ),
    enable_thinking=False,
    system_prompt_template="""你是机器人多摄像头视觉融合模块。

这些图片来自同一个机器人在同一时刻拍摄的多个摄像头，是同一个办公室环境、同一张桌子的不同角度。请先在内部融合所有图片信息，再输出一个最终综合观察结果。

必须遵守：
1. 只回答最终看到的内容，不要逐张图片描述。
2. 多个视角中重复出现的同一物体只说一次。
3. 只在某个视角出现的主要物体也要合并进最终结果。
4. 不要说“第一张、第二张、另一张、图片、图中、视角、摄像头、画面、另一侧、另一张桌子”。
5. 不要解释分析过程，不要输出 JSON。
6. 机器人自己的机械手可以描述为“桌边有机械手”，但不要重复描述。
""",
)

BALANCE_READING_PROFILE = TaskProfile(
    name="balance_reading",
    version="1.0.0",
    temperature=0.0,
    max_tokens=16,
    required_capabilities=(LLMCapability.CHAT, LLMCapability.VISION_CHAT),
    enable_thinking=False,
    system_prompt_template="""你是电子秤读数识别模块。

只识别电子秤显示屏当前显示的数值。只输出一个十进制数字，可包含正负号和小数点；不要输出单位、解释、Markdown 或其他文字。无法确认时输出 ERROR。""",
)


class VisionFusionTask:
    """Fuse one or more camera images into a single observation."""

    def __init__(
        self,
        llm: Optional[BaseLLMClient] = None,
        profile: TaskProfile = VISION_FUSION_PROFILE,
        client_resolver: Optional[ClientResolver] = None,
        response_pipeline: ResponsePipeline | None = None,
        route_resolver: Callable[[TaskProfile], TaskRouteSettings] | None = None,
    ) -> None:
        self._llm = llm
        self._profile = profile
        self._client_resolver = client_resolver
        self._response_pipeline = response_pipeline
        self._route_resolver = route_resolver

    def _build_observe_messages(
        self,
        images: Sequence[LLMContentPart],
        question: str,
        system_prompt: Optional[str],
        profile: TaskProfile,
    ) -> List[LLMMessage]:
        content: List[LLMContentPart] = [
            LLMContentPart(type="text", text=question),
            *list(images),
        ]
        return [
            LLMMessage(
                role="system",
                content=system_prompt or profile.render_system_prompt(),
            ),
            LLMMessage(role="user", content=content),
        ]

    def _build_chat_messages(
        self,
        messages: Sequence[LLMMessage],
        system_prompt: Optional[str],
        profile: TaskProfile,
    ) -> List[LLMMessage]:
        final_messages = list(messages)
        if final_messages and final_messages[0].role == "system":
            if system_prompt is not None:
                final_messages[0] = LLMMessage(role="system", content=system_prompt)
            return final_messages

        final_messages.insert(
            0,
            LLMMessage(
                role="system",
                content=system_prompt or profile.render_system_prompt(),
            ),
        )
        return final_messages

    async def observe(
        self,
        images: Sequence[LLMContentPart],
        question: str = "请综合观察当前环境。",
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        return await llm.chat(
            self._build_observe_messages(
                images=images,
                question=question,
                system_prompt=system_prompt,
                profile=active_profile,
            ),
            **active_profile.chat_options(**chat_options),
        )

    async def stream_observe(
        self,
        images: Sequence[LLMContentPart],
        question: str = "请综合观察当前环境。",
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        voice_response: bool = False,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        messages = self._build_observe_messages(
            images=images,
            question=question,
            system_prompt=system_prompt,
            profile=active_profile,
        )
        async for event in self._stream_response(
            llm,
            messages,
            active_profile,
            voice_response,
            chat_options,
        ):
            yield event

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        return await llm.chat(
            self._build_chat_messages(
                messages=messages,
                system_prompt=system_prompt,
                profile=active_profile,
            ),
            **active_profile.chat_options(**chat_options),
        )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        voice_response: bool = False,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        final_messages = self._build_chat_messages(
            messages=messages,
            system_prompt=system_prompt,
            profile=active_profile,
        )
        async for event in self._stream_response(
            llm,
            final_messages,
            active_profile,
            voice_response,
            chat_options,
        ):
            yield event

    async def _stream_response(
        self,
        llm: BaseLLMClient,
        messages: Sequence[LLMMessage],
        profile: TaskProfile,
        voice_response: bool,
        chat_options: dict[str, Any],
    ) -> AsyncIterator[LLMStreamEvent]:
        if self._response_pipeline is None or self._route_resolver is None:
            async for event in llm.stream_chat(
                list(messages),
                **profile.stream_options(**chat_options),
            ):
                yield event
            return
        route = self._route_resolver(profile)
        async for event in self._response_pipeline.stream(
            llm,
            messages,
            profile,
            route,
            voice_response=voice_response,
            chat_options=chat_options,
        ):
            yield event

    def _resolve_llm(
        self,
        profile: TaskProfile,
        provider: Optional[str],
    ) -> BaseLLMClient:
        if self._client_resolver is not None:
            return self._client_resolver(profile, provider)
        if self._llm is None:
            raise ValueError("VisionFusionTask 未配置 LLM client")
        return self._llm

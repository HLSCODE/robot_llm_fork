"""
LLM provider registry。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .base import BaseLLMClient
from .providers.minicpm_realtime import MiniCPMRealtimeClient
from .providers.openai_compatible import OpenAICompatibleClient
from .tasks import (
    InstructionClassifier,
    RepeatTask,
    SkillPlanner,
    TaskProfile,
    TaskRunner,
    VisionFusionTask,
)
from .types import LLMChatResult, LLMContentPart, LLMMessage, LLMStreamEvent

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class LLMRegistry:
    """集中管理项目内使用的模型能力。"""

    default_chat: BaseLLMClient
    planner_client: Optional[BaseLLMClient] = None
    vision_client: Optional[BaseLLMClient] = None

    def __post_init__(self) -> None:
        if self.planner_client is None:
            self.planner_client = self.default_chat
        if self.vision_client is None:
            self.vision_client = self.default_chat
        self.task_runner = TaskRunner(self.default_chat)
        self.skill_planner = SkillPlanner(self.planner_client)
        self.instruction_classifier = InstructionClassifier(self.default_chat)
        self.vision_fusion = VisionFusionTask(self.vision_client)
        self.repeat_task = RepeatTask(self.default_chat)

    @classmethod
    def from_config(cls, config) -> "LLMRegistry":
        """根据 Config 创建 registry。"""
        chat_provider = (
            getattr(config, "LLM_CHAT_PROVIDER", "")
            or getattr(config, "MODEL_PROVIDER", "openai")
            or "openai"
        ).lower()
        planner_provider = (
            getattr(config, "LLM_PLANNER_PROVIDER", "")
            or chat_provider
        ).lower()
        vision_provider = (
            getattr(config, "LLM_VISION_PROVIDER", "")
            or chat_provider
        ).lower()

        default_chat = cls._create_provider(config, chat_provider)
        if planner_provider == chat_provider:
            planner_client = default_chat
        else:
            planner_client = cls._create_provider(config, planner_provider)
        if vision_provider == chat_provider:
            vision_client = default_chat
        elif vision_provider == planner_provider:
            vision_client = planner_client
        else:
            vision_client = cls._create_provider(config, vision_provider)

        registry = cls(
            default_chat=default_chat,
            planner_client=planner_client,
            vision_client=vision_client,
        )
        logger.info(
            "LLMRegistry 初始化完成: chat=%s/%s, planner=%s/%s, vision=%s/%s",
            registry.default_chat.get_provider_name(),
            registry.default_chat.get_model_name(),
            registry.planner_client.get_provider_name(),
            registry.planner_client.get_model_name(),
            registry.vision_client.get_provider_name(),
            registry.vision_client.get_model_name(),
        )
        return registry

    @classmethod
    def _create_provider(cls, config, provider: str) -> BaseLLMClient:
        provider = (provider or "openai").lower()
        timeout_s = float(getattr(config, "LLM_REQUEST_TIMEOUT_S", 60.0))

        if provider == "minicpm":
            return MiniCPMRealtimeClient(
                gateway_host=getattr(config, "MINICPM_GATEWAY_HOST", "localhost"),
                gateway_port=getattr(config, "MINICPM_GATEWAY_PORT", 8006),
                ws_scheme=getattr(config, "MINICPM_WS_SCHEME", "wss"),
                gateway_path_prefix=getattr(config, "MINICPM_GATEWAY_PATH_PREFIX", ""),
                realtime_path=getattr(config, "MINICPM_REALTIME_PATH", "/v1/realtime"),
                model=getattr(config, "MINICPM_MODEL", "minicpm-o"),
                timeout_s=timeout_s,
            )

        if provider == "deepseek":
            return OpenAICompatibleClient(
                provider_name="deepseek",
                api_key=getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "OPENAI_MODEL", "") or "deepseek-reasoner",
                base_url=getattr(config, "OPENAI_BASE_URL", "") or DEEPSEEK_BASE_URL,
                default_model="deepseek-reasoner",
            )

        if provider == "dashscope":
            return OpenAICompatibleClient(
                provider_name="dashscope",
                api_key=getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "OPENAI_MODEL", "") or "qwen-plus",
                base_url=getattr(config, "OPENAI_BASE_URL", "") or DASHSCOPE_BASE_URL,
                default_model="qwen-plus",
            )

        return OpenAICompatibleClient(
            provider_name=provider if provider != "openai" else "openai",
            api_key=getattr(config, "OPENAI_API_KEY", ""),
            model=getattr(config, "OPENAI_MODEL", "") or "gpt-4o",
            base_url=getattr(config, "OPENAI_BASE_URL", ""),
            default_model="gpt-4o",
        )

    @staticmethod
    def create_openai_compatible(
        provider_name: str,
        api_key: str,
        model: str,
        base_url: str = "",
        default_model: str = "gpt-4o-mini",
    ) -> OpenAICompatibleClient:
        """为独立 OpenAI-compatible 用途创建 provider，例如 Ask 分类。"""
        return OpenAICompatibleClient(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            default_model=default_model,
        )

    def is_available(self) -> bool:
        return self.default_chat.is_available()

    def get_chat_client(self) -> BaseLLMClient:
        return self.default_chat

    def get_planner_client(self) -> BaseLLMClient:
        return self.planner_client or self.default_chat

    def get_vision_client(self) -> BaseLLMClient:
        return self.vision_client or self.default_chat

    async def chat(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Run a generic chat task through the default chat provider."""
        return await self.task_runner.chat(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            **chat_options,
        )

    def chat_sync(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Synchronous generic chat task through the default chat provider."""
        return self.task_runner.chat_sync(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            **chat_options,
        )

    async def stream_chat(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Run a generic streaming chat task through the default chat provider."""
        async for event in self.task_runner.stream_chat(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            **chat_options,
        ):
            yield event

    def has_credentials_for_provider(self, provider: Optional[str] = None) -> bool:
        provider = (provider or self.default_chat.get_provider_name()).lower()
        if provider == "minicpm":
            return self.default_chat.is_available()
        return self.default_chat.is_available()

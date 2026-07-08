"""
LLM provider registry。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .base import BaseLLMClient
from .classifier import InstructionClassifier
from .deepseek_client import DeepSeekClient
from .dashscope_client import DashScopeClient
from .openai_client import OpenAIClient
from .planner import SkillPlanner
from .providers.minicpm_realtime import MiniCPMRealtimeClient
from .providers.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)


@dataclass
class LLMRegistry:
    """集中管理项目内使用的模型能力。"""

    default_chat: BaseLLMClient
    planner_client: Optional[BaseLLMClient] = None

    def __post_init__(self) -> None:
        if self.planner_client is None:
            self.planner_client = self.default_chat
        self.skill_planner = SkillPlanner(self.planner_client)
        self.instruction_classifier = InstructionClassifier(self.default_chat)

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

        default_chat = cls._create_provider(config, chat_provider)
        if planner_provider == chat_provider:
            planner_client = default_chat
        else:
            planner_client = cls._create_provider(config, planner_provider)

        registry = cls(default_chat=default_chat, planner_client=planner_client)
        logger.info(
            "LLMRegistry 初始化完成: chat=%s/%s, planner=%s/%s",
            registry.default_chat.get_provider_name(),
            registry.default_chat.get_model_name(),
            registry.planner_client.get_provider_name(),
            registry.planner_client.get_model_name(),
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
            return DeepSeekClient(
                api_key=getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "OPENAI_MODEL", "") or "deepseek-reasoner",
                base_url=getattr(config, "OPENAI_BASE_URL", "") or DeepSeekClient.DEFAULT_BASE_URL,
            )

        if provider == "dashscope":
            return DashScopeClient(
                api_key=getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "OPENAI_MODEL", "") or "qwen-plus",
                base_url=getattr(config, "OPENAI_BASE_URL", "") or DashScopeClient.DEFAULT_BASE_URL,
            )

        return OpenAIClient(
            api_key=getattr(config, "OPENAI_API_KEY", ""),
            model=getattr(config, "OPENAI_MODEL", "") or "gpt-4o",
            base_url=getattr(config, "OPENAI_BASE_URL", ""),
            provider_name=provider if provider != "openai" else "openai",
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

    def has_credentials_for_provider(self, provider: Optional[str] = None) -> bool:
        provider = (provider or self.default_chat.get_provider_name()).lower()
        if provider == "minicpm":
            return self.default_chat.is_available()
        return self.default_chat.is_available()

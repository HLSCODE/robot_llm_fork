"""
LLM provider registry.

LLMRegistry owns provider singletons and resolves the concrete client for each
task call. Resolution priority is:

1. Explicit provider passed by the caller.
2. TaskProfile.default_provider.
3. LLM_DEFAULT_PROVIDER from config.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Dict, Optional, Sequence

from .base import BaseLLMClient
from .providers.minicpm_realtime import MiniCPMRealtimeClient
from .providers.openai_compatible import OpenAICompatibleClient
from .tasks import (
    GENERAL_CHAT_PROFILE,
    INSTRUCTION_CLASSIFIER_PROFILE,
    REPEAT_PROFILE,
    ROBOT_PLANNER_PROFILE,
    VISION_FUSION_PROFILE,
    VOICE_FEEDBACK_PROFILE,
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
SUPPORTED_PROVIDERS = ("openai", "deepseek", "dashscope", "minicpm")


class LLMRegistry:
    """Central registry for all supported LLM provider singletons."""

    def __init__(
        self,
        config: Any,
        default_provider: str = "openai",
        providers: Optional[Dict[str, BaseLLMClient]] = None,
    ) -> None:
        self._config = config
        self.default_provider = self._normalize_provider(default_provider)
        self._providers: Dict[str, BaseLLMClient] = {}

        if providers:
            self._providers.update(
                {
                    self._normalize_provider(name): client
                    for name, client in providers.items()
                }
            )

        self.repeat_task = RepeatTask(client_resolver=self.get_client_for_profile)
        self.task_runner = TaskRunner(
            client_resolver=self.get_client_for_profile,
            voice_repeater=self.repeat_task,
        )
        self.skill_planner = SkillPlanner(client_resolver=self.get_client_for_profile)
        self.instruction_classifier = InstructionClassifier(
            client_resolver=self.get_client_for_profile
        )
        self.vision_fusion = VisionFusionTask(client_resolver=self.get_client_for_profile)

    @classmethod
    def from_config(cls, config) -> "LLMRegistry":
        """Create a registry from Config or a Config-like object."""
        config = cls._resolve_config(config)
        default_provider = (
            getattr(config, "LLM_DEFAULT_PROVIDER", "")
            or "openai"
        )
        registry = cls(config=config, default_provider=default_provider)
        logger.info(
            "LLMRegistry 初始化完成: default=%s, providers=%s",
            registry.default_provider,
            registry.describe_providers(),
        )
        return registry

    @staticmethod
    def _resolve_config(config):
        if hasattr(config, "get_instance"):
            return config.get_instance()
        return config

    @staticmethod
    def _normalize_provider(provider: Optional[str]) -> str:
        return (provider or "openai").strip().lower()

    @classmethod
    def _create_provider(cls, config, provider: str) -> BaseLLMClient:
        provider = cls._normalize_provider(provider)
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
                api_key=getattr(config, "DEEPSEEK_API_KEY", "")
                or getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "DEEPSEEK_MODEL", "")
                or getattr(config, "OPENAI_MODEL", "")
                or "deepseek-reasoner",
                base_url=getattr(config, "DEEPSEEK_BASE_URL", "")
                or getattr(config, "OPENAI_BASE_URL", "")
                or DEEPSEEK_BASE_URL,
                default_model="deepseek-reasoner",
            )

        if provider == "dashscope":
            return OpenAICompatibleClient(
                provider_name="dashscope",
                api_key=getattr(config, "DASHSCOPE_API_KEY", "")
                or getattr(config, "OPENAI_API_KEY", ""),
                model=getattr(config, "DASHSCOPE_MODEL", "")
                or getattr(config, "OPENAI_MODEL", "")
                or "qwen-plus",
                base_url=getattr(config, "DASHSCOPE_BASE_URL", "")
                or getattr(config, "OPENAI_BASE_URL", "")
                or DASHSCOPE_BASE_URL,
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
        """Create an independent OpenAI-compatible provider."""
        return OpenAICompatibleClient(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            default_model=default_model,
        )

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*SUPPORTED_PROVIDERS, *self._providers.keys())))

    @property
    def loaded_provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers.keys())

    @property
    def default_chat(self) -> BaseLLMClient:
        """Compatibility accessor for the default provider client."""
        return self.get_provider(self.default_provider)

    @property
    def planner_client(self) -> BaseLLMClient:
        """Compatibility accessor for the planner profile client."""
        return self.get_planner_client()

    @property
    def vision_client(self) -> BaseLLMClient:
        """Compatibility accessor for the vision profile client."""
        return self.get_vision_client()

    def describe_providers(self) -> str:
        parts = []
        for name in self.provider_names:
            client = self._providers.get(name)
            if client is None:
                parts.append(f"{name}:lazy")
                continue
            status = "ok" if client.is_available() else "unavailable"
            parts.append(f"{name}/{client.get_model_name()}:{status}")
        return ", ".join(parts)

    def get_provider(self, provider: Optional[str] = None) -> BaseLLMClient:
        provider_name = self._normalize_provider(provider or self.default_provider)
        if provider_name in self._providers:
            return self._providers[provider_name]

        if provider_name not in SUPPORTED_PROVIDERS:
            supported = ", ".join(SUPPORTED_PROVIDERS)
            raise ValueError(f"未知 LLM provider: {provider_name}，支持: {supported}")

        logger.info("懒加载 LLM provider: %s", provider_name)
        client = self._create_provider(self._config, provider_name)
        self._providers[provider_name] = client
        return client

    def get_client_for_profile(
        self,
        profile: TaskProfile,
        provider: Optional[str] = None,
    ) -> BaseLLMClient:
        provider_name = provider or profile.default_provider or self.default_provider
        client = self.get_provider(provider_name)
        self._warn_if_capabilities_missing(profile, client)
        return client

    def _warn_if_capabilities_missing(
        self,
        profile: TaskProfile,
        client: BaseLLMClient,
    ) -> None:
        required = set(profile.required_capabilities)
        if not required:
            return
        missing = required - client.capabilities()
        if not missing:
            return
        missing_text = ", ".join(capability.value for capability in missing)
        logger.warning(
            "TaskProfile %s 使用 provider %s 时缺少能力: %s",
            profile.name,
            client.get_provider_name(),
            missing_text,
        )

    def is_available(self) -> bool:
        return self.get_provider().is_available()

    def get_chat_client(self, provider: Optional[str] = None) -> BaseLLMClient:
        return self.get_client_for_profile(GENERAL_CHAT_PROFILE, provider)

    def get_planner_client(self, provider: Optional[str] = None) -> BaseLLMClient:
        return self.get_client_for_profile(ROBOT_PLANNER_PROFILE, provider)

    def get_vision_client(self, provider: Optional[str] = None) -> BaseLLMClient:
        return self.get_client_for_profile(VISION_FUSION_PROFILE, provider)

    def get_repeat_client(self, provider: Optional[str] = None) -> BaseLLMClient:
        return self.get_client_for_profile(REPEAT_PROFILE, provider)

    def get_feedback_client(self, provider: Optional[str] = None) -> BaseLLMClient:
        return self.get_client_for_profile(VOICE_FEEDBACK_PROFILE, provider)

    async def chat(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Run a generic chat task through the selected provider."""
        return await self.task_runner.chat(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            provider=provider,
            **chat_options,
        )

    def chat_sync(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> LLMChatResult:
        """Synchronous generic chat task through the selected provider."""
        return self.task_runner.chat_sync(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            provider=provider,
            **chat_options,
        )

    async def stream_chat(
        self,
        user_text: str | list[LLMContentPart] | None = None,
        messages: Optional[Sequence[LLMMessage]] = None,
        system_prompt: Optional[str] = None,
        profile: Optional[TaskProfile] = None,
        prompt_context: Optional[dict[str, Any]] = None,
        voice_response: bool = False,
        provider: Optional[str] = None,
        **chat_options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Run a generic streaming chat task through the selected provider."""
        async for event in self.task_runner.stream_chat(
            user_text=user_text,
            messages=messages,
            system_prompt=system_prompt,
            profile=profile,
            prompt_context=prompt_context,
            voice_response=voice_response,
            provider=provider,
            **chat_options,
        ):
            yield event

    def has_credentials_for_provider(self, provider: Optional[str] = None) -> bool:
        return self.get_provider(provider).is_available()

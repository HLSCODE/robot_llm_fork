"""
OpenAI 兼容客户端。

保留旧 `OpenAIClient` 类名，内部委托给统一的 OpenAI-compatible provider。
"""
from __future__ import annotations

from .providers.openai_compatible import OpenAICompatibleClient


class OpenAIClient(OpenAICompatibleClient):
    """OpenAI 或其他 OpenAI-compatible 服务客户端。"""

    def __init__(self, api_key=None, model=None, base_url=None, provider_name: str = "openai"):
        try:
            from ..core.config_loader import Config

            config = Config.get_instance()
            if config is not None:
                if api_key is None:
                    api_key = config.OPENAI_API_KEY
                if model is None:
                    model = config.OPENAI_MODEL
                if base_url is None:
                    base_url = config.OPENAI_BASE_URL
        except Exception as exc:
            print(f"加载 LLM 配置失败：{exc}，使用传入参数或默认值")

        super().__init__(
            provider_name=provider_name,
            api_key=api_key or "",
            model=model or "gpt-4o",
            base_url=base_url or "",
            default_model="gpt-4o",
        )

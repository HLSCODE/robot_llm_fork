"""
阿里云百炼 DashScope OpenAI-compatible 客户端。
"""
from __future__ import annotations

from .providers.openai_compatible import OpenAICompatibleClient


class DashScopeClient(OpenAICompatibleClient):
    """DashScope 兼容 OpenAI 协议的客户端。"""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, api_key=None, model=None, base_url=None):
        try:
            from ..core.config_loader import Config

            config = Config.get_instance()
            if config is not None:
                if api_key is None:
                    api_key = config.OPENAI_API_KEY
                if model is None:
                    model = config.OPENAI_MODEL
                if base_url is None:
                    base_url = config.OPENAI_BASE_URL or self.DEFAULT_BASE_URL
        except Exception as exc:
            print(f"加载 LLM 配置失败：{exc}，使用传入参数或默认值")

        super().__init__(
            provider_name="dashscope",
            api_key=api_key or "",
            model=model or "qwen-plus",
            base_url=base_url or self.DEFAULT_BASE_URL,
            default_model="qwen-plus",
        )

"""
LLM 抽象基类。

Provider 只暴露通用异步 chat / stream_chat 能力；技能规划由 task 层负责。
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .types import LLMCapability, LLMChatResult, LLMMessage, LLMStreamEvent


@dataclass
class LLMPlanResult:
    """LLM 返回的规划结果"""
    skill_id: Optional[str]          # 匹配的技能ID，不匹配则为 None
    skill_name: str                 # 技能名称
    parameters: Dict[str, Any]      # 提取的参数
    reasoning: str                   # 分析过程
    confidence: float               # 置信度 0.0 ~ 1.0
    error: Optional[str] = None    # 无法理解时的错误信息
    fallback_suggestion: Optional[str] = None  # 降级建议

    def is_valid(self) -> bool:
        """是否有效匹配"""
        return self.confidence >= 0.5 and self.error is None


class BaseLLMClient(ABC):
    """
    通用 LLM 客户端抽象基类。
    """

    @abstractmethod
    def is_available(self) -> bool:
        """
        检查 LLM 服务是否可用

        Returns:
            是否可用
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """
        获取模型名称

        Returns:
            模型名称
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """获取 provider 名称。"""
        pass

    @abstractmethod
    def capabilities(self) -> set[LLMCapability]:
        """获取 provider 支持的能力集合。"""
        pass

    async def chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        """普通非流式对话。"""
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """流式对话。"""
        raise NotImplementedError

    async def close(self) -> None:
        """释放底层资源。"""

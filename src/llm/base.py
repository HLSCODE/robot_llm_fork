"""
LLM 抽象基类。

Provider 只暴露通用异步 chat / stream_chat 能力；技能规划由 task 层负责。
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..domain.commands import PlannedCommand
from .types import (
    LLMCallProvenance,
    LLMCapability,
    LLMChatResult,
    LLMMessage,
    LLMStreamEvent,
)


@dataclass(frozen=True, slots=True)
class CommandPlanResult:
    """Validated typed command returned by a deterministic or LLM planner."""

    command: PlannedCommand | None
    reasoning: str
    confidence: float
    error: Optional[str] = None
    fallback_suggestion: Optional[str] = None
    provenance: Optional[LLMCallProvenance] = None

    def is_valid(self) -> bool:
        """是否有效匹配"""
        return (
            self.command is not None
            and self.confidence >= 0.5
            and self.error is None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a transport-safe planning result."""
        return {
            "command": self.command.to_dict() if self.command is not None else None,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "error": self.error,
            "fallback_suggestion": self.fallback_suggestion,
            "provenance": (
                self.provenance.to_dict()
                if self.provenance is not None
                else None
            ),
        }


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

    def stream_chat(
        self,
        messages: List[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """流式对话。"""
        raise NotImplementedError

    async def close(self) -> None:
        """释放底层资源。"""

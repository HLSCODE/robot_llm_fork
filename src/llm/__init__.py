"""
LLM 大模型模块
提供统一的大模型能力层，用于对话、流式对话、指令分类和动作规划。
"""
from .base import BaseLLMClient, CommandPlanResult
from .metrics import LLMCallOutcome, LLMMetrics, LLMMetricsSnapshot, LLMUsage
from .providers.minicpm_realtime import MiniCPMRealtimeClient
from .providers.openai_compatible import OpenAICompatibleClient
from .registry import LLMRegistry
from .routing import ProviderHealthSnapshot, ProviderHealthStatus
from .tasks import (
    BALANCE_READING_PROFILE,
    GENERAL_CHAT_PROFILE,
    INSTRUCTION_CLASSIFIER_PROFILE,
    REPEAT_PROFILE,
    ROBOT_PLANNER_PROFILE,
    VISION_FUSION_PROFILE,
    VOICE_FEEDBACK_PROFILE,
    InstructionClassifier,
    ProviderName,
    ReasoningEffort,
    RepeatTask,
    ResponseMode,
    CommandPlanner,
    TaskProfile,
    TaskRunner,
    VisionFusionTask,
)
from .types import (
    LLMArtifactVersion,
    LLMCallProvenance,
    LLMCapability,
    LLMChatResult,
    LLMContentPart,
    LLMMessage,
    LLMStreamEvent,
)

__all__ = [
    "BaseLLMClient",
    "CommandPlanResult",
    "LLMArtifactVersion",
    "LLMCallProvenance",
    "LLMCapability",
    "LLMCallOutcome",
    "LLMChatResult",
    "LLMContentPart",
    "LLMMessage",
    "LLMMetrics",
    "LLMMetricsSnapshot",
    "LLMStreamEvent",
    "LLMUsage",
    "LLMRegistry",
    "ProviderHealthSnapshot",
    "ProviderHealthStatus",
    "TaskRunner",
    "CommandPlanner",
    "InstructionClassifier",
    "TaskProfile",
    "ProviderName",
    "ReasoningEffort",
    "ResponseMode",
    "GENERAL_CHAT_PROFILE",
    "VOICE_FEEDBACK_PROFILE",
    "ROBOT_PLANNER_PROFILE",
    "INSTRUCTION_CLASSIFIER_PROFILE",
    "VISION_FUSION_PROFILE",
    "BALANCE_READING_PROFILE",
    "REPEAT_PROFILE",
    "OpenAICompatibleClient",
    "MiniCPMRealtimeClient",
    "VisionFusionTask",
    "RepeatTask",
]

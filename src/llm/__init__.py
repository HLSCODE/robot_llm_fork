"""
LLM 大模型模块
提供统一的大模型能力层，用于对话、流式对话、指令分类和动作规划。
"""
from .base import BaseLLMClient, LLMClient, LLMPlanResult
from .providers.openai_compatible import OpenAICompatibleClient
from .providers.minicpm_realtime import MiniCPMRealtimeClient
from .registry import LLMRegistry
from .tasks import (
    GENERAL_CHAT_PROFILE,
    INSTRUCTION_CLASSIFIER_PROFILE,
    REPEAT_PROFILE,
    ROBOT_PLANNER_PROFILE,
    VISION_FUSION_PROFILE,
    InstructionClassifier,
    RepeatTask,
    SkillPlanner,
    TaskProfile,
    TaskRunner,
    VisionFusionTask,
)
from .types import (
    LLMCapability,
    LLMChatResult,
    LLMContentPart,
    LLMMessage,
    LLMStreamEvent,
)

__all__ = [
    "BaseLLMClient",
    "LLMClient",
    "LLMPlanResult",
    "LLMCapability",
    "LLMChatResult",
    "LLMContentPart",
    "LLMMessage",
    "LLMStreamEvent",
    "LLMRegistry",
    "TaskRunner",
    "SkillPlanner",
    "InstructionClassifier",
    "TaskProfile",
    "GENERAL_CHAT_PROFILE",
    "ROBOT_PLANNER_PROFILE",
    "INSTRUCTION_CLASSIFIER_PROFILE",
    "VISION_FUSION_PROFILE",
    "REPEAT_PROFILE",
    "OpenAICompatibleClient",
    "MiniCPMRealtimeClient",
    "VisionFusionTask",
    "RepeatTask",
]

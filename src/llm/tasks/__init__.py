"""
LLM task wrappers.

This package contains model-usage scenarios built on top of provider
capabilities, such as generic chat tasks, skill planning, and classification.
"""
from .classifier import INSTRUCTION_CLASSIFIER_PROFILE, InstructionClassifier
from .planner import CommandPlanner, ROBOT_PLANNER_PROFILE
from .profiles import (
    GENERAL_CHAT_PROFILE,
    ReasoningEffort,
    TaskProfile,
    VOICE_FEEDBACK_PROFILE,
)
from .repeat import REPEAT_PROFILE, RepeatTask
from .runner import TaskRunner
from .vision import BALANCE_READING_PROFILE, VISION_FUSION_PROFILE, VisionFusionTask

__all__ = [
    "TaskProfile",
    "ReasoningEffort",
    "GENERAL_CHAT_PROFILE",
    "VOICE_FEEDBACK_PROFILE",
    "ROBOT_PLANNER_PROFILE",
    "INSTRUCTION_CLASSIFIER_PROFILE",
    "VISION_FUSION_PROFILE",
    "BALANCE_READING_PROFILE",
    "REPEAT_PROFILE",
    "TaskRunner",
    "CommandPlanner",
    "InstructionClassifier",
    "VisionFusionTask",
    "RepeatTask",
]

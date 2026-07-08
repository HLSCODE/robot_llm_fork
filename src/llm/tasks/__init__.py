"""
LLM task wrappers.

This package contains model-usage scenarios built on top of provider
capabilities, such as generic chat tasks, skill planning, and classification.
"""
from .classifier import INSTRUCTION_CLASSIFIER_PROFILE, InstructionClassifier
from .planner import ROBOT_PLANNER_PROFILE, SkillPlanner
from .profiles import (
    GENERAL_CHAT_PROFILE,
    TaskProfile,
)
from .repeat import REPEAT_PROFILE, RepeatTask
from .runner import TaskRunner
from .vision import VISION_FUSION_PROFILE, VisionFusionTask

__all__ = [
    "TaskProfile",
    "GENERAL_CHAT_PROFILE",
    "ROBOT_PLANNER_PROFILE",
    "INSTRUCTION_CLASSIFIER_PROFILE",
    "VISION_FUSION_PROFILE",
    "REPEAT_PROFILE",
    "TaskRunner",
    "SkillPlanner",
    "InstructionClassifier",
    "VisionFusionTask",
    "RepeatTask",
]

"""
AI 集成模块
连接大模型、Skill系统和现有执行层
"""
from .ai_controller import AIController
from .execution_bridge import ExecutionBridge

__all__ = [
    "AIController",
    "ExecutionBridge",
]

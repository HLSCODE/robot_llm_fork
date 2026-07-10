"""AI runtime and execution integration.

Dialogue and intent routing live in ``src.voice_interaction``. This package
keeps shared LLM/Skill dependencies and execution bridging for the GUI.
"""
from .ai_controller import AIController
from .execution_bridge import ExecutionBridge

__all__ = [
    "AIController",
    "ExecutionBridge",
]

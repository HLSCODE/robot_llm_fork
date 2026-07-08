"""
LLM provider 实现集合。
"""
from .openai_compatible import OpenAICompatibleClient
from .minicpm_realtime import MiniCPMRealtimeClient

__all__ = [
    "OpenAICompatibleClient",
    "MiniCPMRealtimeClient",
]

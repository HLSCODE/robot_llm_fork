"""AI runtime and execution integration.

Dialogue and intent routing live in ``src.voice_interaction``. This package
keeps the application-facing AI controller; Qt bridges live under ``src.gui``.
"""
from .ai_controller import AIController
__all__ = ["AIController"]

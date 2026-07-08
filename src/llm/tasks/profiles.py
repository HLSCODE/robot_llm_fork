"""
LLM task profiles.

TaskProfile describes one stable model-usage scenario: the system prompt
template plus default generation options.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from string import Template
from typing import Any, Dict, Optional, Union


ResponseFormat = Union[str, Dict[str, Any]]


@dataclass(frozen=True)
class TaskProfile:
    """A reusable prompt and option set for a fixed LLM task."""

    name: str
    system_prompt_template: str
    temperature: float = 0.3
    max_tokens: int = 512
    response_format: Optional[ResponseFormat] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def render_system_prompt(self, **context: Any) -> str:
        """Render the profile system prompt with optional context values."""
        normalized = {
            key: "" if value is None else str(value)
            for key, value in context.items()
        }
        return Template(self.system_prompt_template).safe_substitute(normalized)

    def chat_options(self, **overrides: Any) -> Dict[str, Any]:
        """Return model call options, with explicit overrides taking priority."""
        options: Dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.response_format is not None:
            options["response_format"] = self.response_format

        for key, value in overrides.items():
            if value is not None:
                options[key] = value
        return options


GENERAL_CHAT_PROFILE = TaskProfile(
    name="general_chat",
    temperature=0.7,
    max_tokens=512,
    system_prompt_template="你是一个可靠、简洁的助手。",
)

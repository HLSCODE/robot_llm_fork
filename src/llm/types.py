"""
LLM 能力层通用数据类型。
"""
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union


MessageRole = Literal["system", "user", "assistant"]
ContentType = Literal["text", "image", "audio"]
StreamEventType = Literal[
    "session_started",
    "text_delta",
    "audio_delta",
    "done",
    "error",
    "metrics",
]


class LLMCapability(str, Enum):
    """模型客户端支持的能力。"""

    CHAT = "chat"
    STREAM_CHAT = "stream_chat"
    VISION_CHAT = "vision_chat"
    AUDIO_CHAT = "audio_chat"
    TTS = "tts"
    PLANNING = "planning"


@dataclass(frozen=True, slots=True)
class LLMArtifactVersion:
    """A versioned input artifact used by an LLM task."""

    name: str
    version: str
    sha256: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class LLMCallProvenance:
    """Non-sensitive provenance for one resolved model invocation."""

    task_profile: str
    prompt_version: str
    prompt_template_sha256: str
    request_sha256: str
    provider: str
    model: str
    attempted_providers: tuple[str, ...]
    fallback_used: bool
    artifacts: tuple[LLMArtifactVersion, ...] = ()

    def with_artifact(
        self,
        *,
        name: str,
        version: str,
        sha256: str,
    ) -> "LLMCallProvenance":
        artifact = LLMArtifactVersion(
            name=name,
            version=version,
            sha256=sha256,
        )
        retained = tuple(
            existing
            for existing in self.artifacts
            if existing.name != name
        )
        return replace(self, artifacts=(*retained, artifact))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_profile": self.task_profile,
            "prompt_version": self.prompt_version,
            "prompt_template_sha256": self.prompt_template_sha256,
            "request_sha256": self.request_sha256,
            "provider": self.provider,
            "model": self.model,
            "attempted_providers": list(self.attempted_providers),
            "fallback_used": self.fallback_used,
            "artifacts": [
                artifact.to_dict()
                for artifact in self.artifacts
            ],
        }


@dataclass
class LLMContentPart:
    """多模态消息片段。"""

    type: ContentType
    text: Optional[str] = None
    data: Optional[str] = None
    mime_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class LLMMessage:
    """统一对话消息。"""

    role: MessageRole
    content: Union[str, List[LLMContentPart]]


@dataclass
class LLMChatResult:
    """非流式对话结果。"""

    text: str
    model: str
    provider: str
    raw: Any = None
    usage: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    provenance: Optional[LLMCallProvenance] = None


@dataclass
class LLMStreamEvent:
    """流式对话事件。"""

    type: StreamEventType
    text_delta: str = ""
    audio_data: Optional[str] = None
    text: str = ""
    error: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    raw: Any = None
    provenance: Optional[LLMCallProvenance] = None

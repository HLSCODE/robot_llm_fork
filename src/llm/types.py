"""
LLM 能力层通用数据类型。
"""
from dataclasses import dataclass
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

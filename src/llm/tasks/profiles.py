"""
LLM task profiles.

TaskProfile describes one stable model-usage scenario: the system prompt
template plus default generation options.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from string import Template
from typing import Any, Dict, Literal, Optional, Tuple, Union

from ..types import LLMCapability


ResponseFormat = Union[str, Dict[str, Any]]
ResponseMode = Literal["text", "voice_stream"]
ReasoningEffort = Literal["low", "medium", "high"]
ProviderName = str
VOICE_OPTION_KEYS = {"tts", "tts_enabled", "use_tts_template"}


@dataclass(frozen=True)
class TaskProfile:
    """A reusable prompt and option set for a fixed LLM task."""

    name: str
    version: str
    system_prompt_template: str
    temperature: float = 0.3
    max_tokens: int = 512
    response_format: Optional[ResponseFormat] = None
    default_provider: Optional[ProviderName] = None
    required_capabilities: Tuple[LLMCapability, ...] = ()
    response_mode: ResponseMode = "text"
    enable_thinking: Optional[bool] = None
    reasoning_effort: Optional[ReasoningEffort] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("TaskProfile.name must not be empty")
        if not self.version.strip():
            raise ValueError("TaskProfile.version must not be empty")
        if self.max_tokens < 1:
            raise ValueError("TaskProfile.max_tokens must be positive")

    @property
    def template_sha256(self) -> str:
        """Return the stable content fingerprint of the prompt template."""
        return hashlib.sha256(
            self.system_prompt_template.encode("utf-8")
        ).hexdigest()

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
        if self.enable_thinking is not None:
            options["enable_thinking"] = self.enable_thinking
        if self.reasoning_effort is not None:
            options["reasoning_effort"] = self.reasoning_effort

        for key, value in overrides.items():
            if value is not None:
                options[key] = value

        if self.response_mode == "text":
            for key in VOICE_OPTION_KEYS:
                options.pop(key, None)
        return options

    def stream_options(
        self,
        voice_response: bool = False,
        **overrides: Any,
    ) -> Dict[str, Any]:
        """Return stream options and enable TTS for voice-stream tasks when requested."""
        options = self.chat_options(**overrides)
        if voice_response and self.response_mode == "voice_stream":
            options.setdefault("tts_enabled", True)
            options.setdefault("use_tts_template", True)
        return options


GENERAL_CHAT_PROFILE = TaskProfile(
    name="general_chat",
    version="1.0.0",
    temperature=0.7,
    max_tokens=512,
    default_provider="dashscope",
    required_capabilities=(LLMCapability.CHAT, LLMCapability.STREAM_CHAT),
    response_mode="text",
    system_prompt_template="""你是明德博士，一个具身机器人助手。

请用自然、亲切、清晰的中文和用户对话，回复要适合直接通过语音说出口。

你大致可以协助用户完成这些工作：
1. 日常聊天、普通问答、解释说明和建议。
2. 理解用户的动作需求，并协助生成机器人技能或动作序列。
3. 结合摄像头观察环境，回答物体、位置、数量、颜色等视觉相关问题。
4. 处理取消、暂停、结束会话等简单会话控制需求。
5. 字数控制在50个字以内。

当前如果只是普通聊天，请直接回答用户的问题。不要编造已经看到的画面，也不要声称已经执行了动作。""",
)


VOICE_FEEDBACK_PROFILE = TaskProfile(
    name="voice_feedback",
    version="1.0.0",
    temperature=0.4,
    max_tokens=80,
    default_provider="dashscope",
    required_capabilities=(LLMCapability.CHAT, LLMCapability.STREAM_CHAT),
    response_mode="text",
    enable_thinking=False,
    system_prompt_template="""你是机器人语音反馈模块。

你的任务是把内部错误或状态转换成一句自然、简短、适合机器人说出口的中文反馈。

必须遵守：
1. 只输出一句话。
2. 不要输出 JSON、Markdown 或列表。
3. 不要提到配置项、序列号、设备清单、堆栈、接口名、相机状态列表。
4. 不要逐字复述技术错误。
5. 语气自然、礼貌、像机器人在和用户说明当前情况。
6. 如果有建议回复，优先保留其含义，并让表达更口语化。
7. 字数控制在 40 个中文字符以内。""",
)

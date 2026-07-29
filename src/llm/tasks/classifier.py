"""
通用指令分类器。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

from ..base import BaseLLMClient
from ..types import LLMCapability, LLMMessage
from .profiles import TaskProfile

logger = logging.getLogger(__name__)
ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


INSTRUCTION_CLASSIFIER_PROFILE = TaskProfile(
    name="instruction_classifier",
    temperature=0.1,
    max_tokens=200,
    response_format="json",
    default_provider="dashscope",
    required_capabilities=(LLMCapability.CHAT,),
    response_mode="text",
    enable_thinking=False,
    system_prompt_template="""你是机器人对话入口的意图识别模块。

用户输入可能来自 GUI 文本框，也可能来自唤醒后的语音 ASR。你的任务是判断用户这句话的意图，以及如果当前存在语音 session，机器人是否应该继续该 session。

请判断用户输入属于以下哪类：
- chat：正常聊天、问候、闲聊、普通问答，不需要机器人执行动作，也不需要视觉。
- command：要求机器人创建新的动作计划、控制设备、移动、转向、播放内容、切换状态、调整参数等。
- vision_question：询问机器人看到什么，或询问环境中的人、物体、障碍物、位置、数量、颜色等视觉信息。
- session_control：用户只控制对话 session，例如结束、暂停或恢复对话。
- execution_control：用户取消、暂停或恢复动作预览或正在执行的机器人任务。

is_addressed_to_robot 判断规则：
1. GUI 文本框输入和唤醒后的语音输入，默认 is_addressed_to_robot 为 true。
2. 如果用户明显是在和旁边的人说话、评论别人、闲聊其他人，is_addressed_to_robot 为 false。
3. 如果无法确定，优先判断为 true。

session 判断规则：
1. 如果用户说“没事了”“不用了”“算了”“先这样”“退下吧”“结束吧”“不聊了”“可以了”“好了不用回答了”等，通常表示结束当前 session。
2. 这类话应归类为 session_control。
3. “取消刚才那个”“别执行了”“暂停动作”“继续执行”属于 execution_control，不得归为 session_control。
4. 如果用户只是让机器人暂停说话，例如“先别说话”，session_action 为 pause_session，不结束 session。
5. 如果用户说“停下”“停止动作”，应归类为 execution_control，execution_action 为 cancel，不结束 session。
6. 如果用户说“没事了，但是你帮我看看前面”，不要结束 session，应根据后半句判断为 vision_question。
7. 如果一句话只有“没事了”“不用了”“先这样”等结束性表达，should_end_session 为 true。

输出要求：
1. 只输出 JSON。
2. 不要回答用户问题。
3. 不要执行命令。
4. 不要输出 Markdown。
5. 不要解释原因。

输出格式：
{
  "intent": "chat | command | vision_question | session_control | execution_control",
  "is_addressed_to_robot": true,
  "should_end_session": false,
  "session_action": "none | end_session | pause_session | resume_session",
  "execution_action": "none | cancel | pause | resume"
}
""",
)


class InstructionClassifier:
    """使用 LLM chat 能力判断用户文本是否为机器人指令。"""

    def __init__(
        self,
        llm: Optional[BaseLLMClient] = None,
        profile: TaskProfile = INSTRUCTION_CLASSIFIER_PROFILE,
        client_resolver: Optional[ClientResolver] = None,
    ) -> None:
        self._llm = llm
        self._profile = profile
        self._client_resolver = client_resolver

    async def classify(
        self,
        user_input: str,
        enabled: bool = True,
        system_prompt: str | None = None,
        profile: TaskProfile | None = None,
        provider: str | None = None,
        **chat_options: Any,
    ) -> Dict[str, Any]:
        if not enabled:
            return _fallback_result()

        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        if not llm.is_available():
            logger.info("指令分类 LLM 不可用，跳过分类")
            return _fallback_result()

        try:
            result = await llm.chat(
                [
                    LLMMessage(
                        role="system",
                        content=system_prompt or active_profile.render_system_prompt(),
                    ),
                    LLMMessage(role="user", content=user_input),
                ],
                **active_profile.chat_options(**chat_options),
            )
            data = json.loads(_strip_json_text(result.text))
            return _normalize_result(data)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except Exception as exc:
            logger.warning("指令分类失败 (%s)，按非指令处理", exc)
            return _fallback_result()

    def _resolve_llm(
        self,
        profile: TaskProfile,
        provider: Optional[str],
    ) -> BaseLLMClient:
        if self._client_resolver is not None:
            return self._client_resolver(profile, provider)
        if self._llm is None:
            raise ValueError("InstructionClassifier 未配置 LLM client")
        return self._llm


def _normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    intent = str(data.get("intent", "chat") or "chat").strip()
    if intent not in {
        "chat",
        "command",
        "vision_question",
        "session_control",
        "execution_control",
    }:
        intent = "chat"

    session_action = str(data.get("session_action", "none") or "none").strip()
    if session_action not in {
        "none",
        "end_session",
        "pause_session",
        "resume_session",
    }:
        session_action = "none"
    execution_action = str(
        data.get("execution_action", "none") or "none"
    ).strip()
    if execution_action not in {"none", "cancel", "pause", "resume"}:
        execution_action = "none"

    is_addressed_to_robot = bool(data.get("is_addressed_to_robot", True))
    should_end_session = bool(data.get("should_end_session", False))
    if should_end_session:
        intent = "session_control"
        session_action = "end_session"
    if intent != "session_control":
        session_action = "none"
        should_end_session = False
    if intent != "execution_control":
        execution_action = "none"
    return {
        "intent": intent,
        "is_addressed_to_robot": is_addressed_to_robot,
        "should_end_session": should_end_session,
        "session_action": session_action,
        "execution_action": execution_action,
    }


def _fallback_result() -> Dict[str, Any]:
    return {
        "intent": "chat",
        "is_addressed_to_robot": True,
        "should_end_session": False,
        "session_action": "none",
        "execution_action": "none",
    }


def _strip_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    return text

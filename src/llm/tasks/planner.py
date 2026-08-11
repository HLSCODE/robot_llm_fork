"""
机器人类型化命令规划器。

该模块只负责 prompt 构造和规划结果解析；具体模型调用由注入的 LLM
client 完成。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from ...domain.commands import command_from_dict
from ..base import BaseLLMClient, CommandPlanResult
from ..errors import LLMError
from ..fingerprints import fingerprint_json
from ..types import LLMCapability, LLMMessage
from .profiles import TaskProfile

logger = logging.getLogger(__name__)
ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


ROBOT_PLANNER_PROFILE = TaskProfile(
    name="robot_command_planner",
    version="2.1.0",
    temperature=0.3,
    max_tokens=800,
    response_format="json",
    required_capabilities=(LLMCapability.CHAT, LLMCapability.PLANNING),
    enable_thinking=False,
    system_prompt_template="""你是一个机器人动作规划助手。

项目中有以下可用命令目录：

$command_catalog

请分析用户输入并返回一种明确的类型化命令。

返回格式要求（必须严格遵循 JSON 格式）：
{
  "command": "下列四种对象之一，无法确定时为 null",
  "reasoning": "你的分析思路（1-2句话）",
  "confidence": 置信度0.0~1.0，低于0.5视为无法匹配
}

command 必须严格选择以下一种结构，不得混入其他 kind 的字段：
1. Action：{"kind":"action","action_type":"标准 ActionType","parameters":{},"action_id":"可选","action_name":"可选"}
2. Skill：{"kind":"skill","skill_id":"目录中的技能 ID","parameters":{}}
3. Workflow：{"kind":"workflow","workflow_name":"目录中的流程名称"}
4. ExecutionControl：{"kind":"execution_control","action":"cancel | pause | resume"}

重要规则：
- 只返回上述JSON格式，不要包含任何其他文字
- 如果无法确定唯一命令，将 command 设置为 null 并说明歧义，不得猜测机械臂或设备
- `action_name` 和 `action_id` 只能出现在 kind=action 中；skill/workflow/execution_control 禁止携带这两个字段
- parameters 中的字段必须与 Action/Skill 目录一致
- 只做规划，不得声称已经执行硬件动作""",
)


class CommandPlanner:
    """Use a routed LLM client to produce one typed interaction command."""

    def __init__(
        self,
        llm: Optional[BaseLLMClient] = None,
        profile: TaskProfile = ROBOT_PLANNER_PROFILE,
        client_resolver: Optional[ClientResolver] = None,
    ) -> None:
        self._llm = llm
        self._profile = profile
        self._client_resolver = client_resolver

    async def plan(
        self,
        user_text: str,
        command_catalog: List[Dict[str, Any]],
        system_prompt: str | None = None,
        profile: TaskProfile | None = None,
        provider: str | None = None,
        **chat_options: Any,
    ) -> CommandPlanResult:
        """异步规划入口。"""
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        if not llm.is_available():
            return CommandPlanResult(
                command=None,
                reasoning="",
                confidence=0.0,
                error=f"{llm.get_provider_name()} LLM 不可用，请检查配置",
            )

        rendered_system_prompt = system_prompt or active_profile.render_system_prompt(
            command_catalog=self._build_command_catalog(command_catalog)
        )

        messages = [
            LLMMessage(role="system", content=rendered_system_prompt),
            LLMMessage(role="user", content=self._build_user_prompt(user_text)),
        ]

        try:
            result = await llm.chat(
                messages,
                **active_profile.chat_options(**chat_options),
            )
            logger.debug("LLM 规划原始响应: %s", result.text)
            parsed = parse_command_plan_response(result.text)
            if result.provenance is None:
                return parsed
            provenance = result.provenance.with_artifact(
                name="command_catalog",
                version="2",
                sha256=fingerprint_json(command_catalog),
            )
            return replace(parsed, provenance=provenance)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except LLMError as exc:
            logger.error("LLM 规划调用失败: %s", exc)
            return CommandPlanResult(
                command=None,
                reasoning="",
                confidence=0.0,
                error=f"LLM 调用失败: {str(exc)}",
            )
        except Exception as exc:
            logger.error("LLM 规划发生未知错误: %s", exc, exc_info=True)
            return CommandPlanResult(
                command=None,
                reasoning="",
                confidence=0.0,
                error=f"LLM 调用失败: {str(exc)}",
            )

    def _resolve_llm(
        self,
        profile: TaskProfile,
        provider: Optional[str],
    ) -> BaseLLMClient:
        if self._client_resolver is not None:
            return self._client_resolver(profile, provider)
        if self._llm is None:
            raise ValueError("CommandPlanner 未配置 LLM client")
        return self._llm

    def _build_command_catalog(self, command_catalog: List[Dict[str, Any]]) -> str:
        if not command_catalog:
            return "（暂无可用命令）"
        return json.dumps(command_catalog, ensure_ascii=False, sort_keys=True)

    def _build_user_prompt(self, user_text: str) -> str:
        return f"""用户输入："{user_text}"

请分析用户意图并返回类型化命令（仅返回JSON）："""


def parse_command_plan_response(text: str) -> CommandPlanResult:
    """Parse one untrusted planner response into a typed command result."""
    try:
        data = json.loads(_strip_json_text(text))
        if not isinstance(data, dict):
            raise TypeError("规划结果必须是 JSON 对象")

        raw_command = data.get("command")
        command = None if raw_command is None else command_from_dict(raw_command)
        confidence = float(data.get("confidence", 0.0))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence 必须是 0.0 到 1.0 的有限数值")

        return CommandPlanResult(
            command=command,
            reasoning=str(data.get("reasoning", "")),
            confidence=confidence,
            error=data.get("error"),
            fallback_suggestion=data.get("fallback_suggestion"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.error("解析 LLM 规划响应失败: %s", exc)
        return CommandPlanResult(
            command=None,
            reasoning="",
            confidence=0.0,
            error=f"无法解析 LLM 返回结果: {str(exc)}",
        )


def _strip_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    return text

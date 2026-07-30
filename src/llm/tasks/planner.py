"""
机器人技能规划器。

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

from ..base import BaseLLMClient, LLMPlanResult
from ..errors import LLMError
from ..fingerprints import fingerprint_json
from ..types import LLMCapability, LLMMessage
from .profiles import TaskProfile

logger = logging.getLogger(__name__)
ClientResolver = Callable[[TaskProfile, Optional[str]], BaseLLMClient]


ROBOT_PLANNER_PROFILE = TaskProfile(
    name="robot_skill_planner",
    version="1.0.0",
    temperature=0.3,
    max_tokens=800,
    response_format="json",
    default_provider="dashscope",
    required_capabilities=(LLMCapability.CHAT, LLMCapability.PLANNING),
    response_mode="text",
    enable_thinking=False,
    system_prompt_template="""你是一个机器人动作规划助手。

项目中有以下技能可用（每个技能由多个原子动作步骤组成）：

$skill_desc

请分析用户的自然语言输入，返回JSON格式的技能调用参数。

返回格式要求（必须严格遵循JSON格式）：
{
  "skill_id": "匹配的技能ID，如果无法匹配任何技能则返回null",
  "skill_name": "技能名称，无法匹配则为空字符串",
  "parameters": {从用户输入中提取的参数值，如果没有参数则为空对象},
  "reasoning": "你的分析思路（1-2句话）",
  "confidence": 置信度0.0~1.0，低于0.5视为无法匹配
}

重要规则：
- 只返回上述JSON格式，不要包含任何其他文字
- 如果无法匹配任何技能，设置skill_id为null并说明原因
- parameters中的参数名必须与技能定义中的参数名一致""",
)


class SkillPlanner:
    """使用任意支持 chat 的 LLM 客户端完成机器人技能规划。"""

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
        skill_summaries: List[Dict[str, Any]],
        system_prompt: str | None = None,
        profile: TaskProfile | None = None,
        provider: str | None = None,
        **chat_options: Any,
    ) -> LLMPlanResult:
        """异步规划入口。"""
        active_profile = profile or self._profile
        llm = self._resolve_llm(active_profile, provider)
        if not llm.is_available():
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"{llm.get_provider_name()} LLM 不可用，请检查配置",
            )

        rendered_system_prompt = system_prompt or active_profile.render_system_prompt(
            skill_desc=self._build_skill_desc(skill_summaries)
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
            parsed = parse_skill_plan_response(result.text)
            if result.provenance is None:
                return parsed
            provenance = result.provenance.with_artifact(
                name="skill_catalog",
                version="1",
                sha256=fingerprint_json(skill_summaries),
            )
            return replace(parsed, provenance=provenance)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except LLMError as exc:
            logger.error("LLM 规划调用失败: %s", exc)
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"LLM 调用失败: {str(exc)}",
            )
        except Exception as exc:
            logger.error("LLM 规划发生未知错误: %s", exc, exc_info=True)
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
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
            raise ValueError("SkillPlanner 未配置 LLM client")
        return self._llm

    def _build_skill_desc(self, skill_summaries: List[Dict[str, Any]]) -> str:
        if not skill_summaries:
            return "（暂无可用技能）"

        lines = []
        for skill in skill_summaries:
            param_str = "\n    ".join(skill.get("parameters", [])) or "无"
            example_str = " / ".join(skill.get("examples", [])[:2])

            lines.append(f"""技能ID: {skill['id']}
    名称: {skill['name']}
    分类: {skill['category']}
    描述: {skill['description']}
    参数: {param_str}
    示例: {example_str}""")

        return "\n\n".join(lines)

    def _build_user_prompt(self, user_text: str) -> str:
        return f"""用户输入："{user_text}"

请分析用户意图并返回技能调用参数（仅返回JSON）："""


def parse_skill_plan_response(text: str) -> LLMPlanResult:
    """Parse one planner response into the stable planning result."""
    try:
        data = json.loads(_strip_json_text(text))
        if not isinstance(data, dict):
            raise TypeError("规划结果必须是 JSON 对象")

        skill_id = data.get("skill_id")
        if skill_id is not None:
            skill_id = str(skill_id)
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError("parameters 必须是 JSON 对象")
        confidence = float(data.get("confidence", 0.0))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence 必须是 0.0 到 1.0 的有限数值")

        return LLMPlanResult(
            skill_id=skill_id,
            skill_name=str(data.get("skill_name", "")),
            parameters=parameters,
            reasoning=str(data.get("reasoning", "")),
            confidence=confidence,
            error=data.get("error"),
            fallback_suggestion=data.get("fallback_suggestion"),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("解析 LLM 规划响应失败: %s", exc)
        return LLMPlanResult(
            skill_id=None,
            skill_name="",
            parameters={},
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

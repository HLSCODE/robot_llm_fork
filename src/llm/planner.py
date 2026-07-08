"""
机器人技能规划器。

该模块只负责 prompt 构造和规划结果解析；具体模型调用由注入的 LLM
client 完成。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from .base import BaseLLMClient, LLMPlanResult
from .errors import LLMError
from .sync_utils import run_coro_sync
from .types import LLMMessage

logger = logging.getLogger(__name__)


class SkillPlanner:
    """使用任意支持 chat 的 LLM 客户端完成机器人技能规划。"""

    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm

    async def plan(
        self,
        user_text: str,
        skill_summaries: List[Dict[str, Any]],
    ) -> LLMPlanResult:
        """异步规划入口。"""
        if not self._llm.is_available():
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"{self._llm.get_provider_name()} LLM 不可用，请检查配置",
            )

        messages = [
            LLMMessage(role="system", content=self._build_system_prompt(skill_summaries)),
            LLMMessage(role="user", content=self._build_user_prompt(user_text)),
        ]

        try:
            result = await self._llm.chat(
                messages,
                temperature=0.3,
                max_tokens=800,
                response_format="json",
            )
            logger.debug("LLM 规划原始响应: %s", result.text)
            return self._parse_response(result.text)
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

    def plan_sync(
        self,
        user_text: str,
        skill_summaries: List[Dict[str, Any]],
    ) -> LLMPlanResult:
        """同步规划入口，用于兼容 GUI 后台线程和旧调用点。"""
        return run_coro_sync(self.plan(user_text, skill_summaries))

    def _build_system_prompt(self, skill_summaries: List[Dict[str, Any]]) -> str:
        if not skill_summaries:
            skill_desc = "（暂无可用技能）"
        else:
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

            skill_desc = "\n\n".join(lines)

        return f"""你是一个机器人动作规划助手。

项目中有以下技能可用（每个技能由多个原子动作步骤组成）：

{skill_desc}

请分析用户的自然语言输入，返回JSON格式的技能调用参数。

返回格式要求（必须严格遵循JSON格式）：
{{
  "skill_id": "匹配的技能ID，如果无法匹配任何技能则返回null",
  "skill_name": "技能名称，无法匹配则为空字符串",
  "parameters": {{从用户输入中提取的参数值，如果没有参数则为空对象}},
  "reasoning": "你的分析思路（1-2句话）",
  "confidence": 置信度0.0~1.0，低于0.5视为无法匹配
}}

重要规则：
- 只返回上述JSON格式，不要包含任何其他文字
- 如果无法匹配任何技能，设置skill_id为null并说明原因
- parameters中的参数名必须与技能定义中的参数名一致"""

    def _build_user_prompt(self, user_text: str) -> str:
        return f"""用户输入："{user_text}"

请分析用户意图并返回技能调用参数（仅返回JSON）："""

    def _parse_response(self, text: str) -> LLMPlanResult:
        try:
            data = json.loads(self._strip_json_text(text))

            skill_id = data.get("skill_id")
            if skill_id is not None:
                skill_id = str(skill_id)

            return LLMPlanResult(
                skill_id=skill_id,
                skill_name=data.get("skill_name", ""),
                parameters=data.get("parameters", {}),
                reasoning=data.get("reasoning", ""),
                confidence=float(data.get("confidence", 0.0)),
                error=data.get("error"),
                fallback_suggestion=data.get("fallback_suggestion"),
            )

        except json.JSONDecodeError as exc:
            logger.error("JSON 解析失败: %s, 原始文本: %s", exc, text)
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"无法解析 LLM 返回结果: {str(exc)}",
            )
        except Exception as exc:
            logger.error("解析 LLM 响应时发生错误: %s", exc)
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"解析错误: {str(exc)}",
            )

    @staticmethod
    def _strip_json_text(text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 2:
                text = "\n".join(lines[1:-1]).strip()
        return text

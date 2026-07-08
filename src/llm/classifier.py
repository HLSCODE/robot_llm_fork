"""
通用指令分类器。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from .base import BaseLLMClient
from .types import LLMMessage

logger = logging.getLogger(__name__)


CLASSIFY_PROMPT = """\
判断用户输入是机器人动作指令还是普通对话，只输出纯 JSON，不要任何其他内容。

判断规则：
- is_Instruction=true：移动、抓取、放置、转向、停止等可由机器人执行的动作
- is_Instruction=false：问候、闲聊、信息咨询、环境询问等无法直接由机器人执行的内容

输出格式（仅此格式，无其他内容）：
{"Instruction": "<用户输入>", "is_Instruction": true 或 false}

核心规则：
- Instruction 字段只保留用户要执行的动作指令，不带解释
- 无法确定时优先判定为 false，避免误触发机器人规划

示例：
用户：帮我抓瓶子 -> {"Instruction": "抓瓶子", "is_Instruction": true}
用户：前进两米 -> {"Instruction": "前进两米", "is_Instruction": true}
用户：你好吗 -> {"Instruction": "你好吗", "is_Instruction": false}
用户：这是什么 -> {"Instruction": "这是什么", "is_Instruction": false}
"""


class InstructionClassifier:
    """使用 LLM chat 能力判断用户文本是否为机器人指令。"""

    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm

    async def classify(self, user_input: str, enabled: bool = True) -> Dict[str, Any]:
        if not enabled:
            return {"Instruction": user_input, "is_Instruction": False}

        if not self._llm.is_available():
            logger.info("指令分类 LLM 不可用，跳过分类")
            return {"Instruction": user_input, "is_Instruction": False}

        try:
            result = await self._llm.chat(
                [
                    LLMMessage(role="system", content=CLASSIFY_PROMPT),
                    LLMMessage(role="user", content=user_input),
                ],
                temperature=0.1,
                max_tokens=100,
                response_format="json",
            )
            data = json.loads(_strip_json_text(result.text))
            return {
                "Instruction": data.get("Instruction", user_input),
                "is_Instruction": bool(data.get("is_Instruction", False)),
            }
        except Exception as exc:
            logger.warning("指令分类失败 (%s)，按非指令处理", exc)
            return {"Instruction": user_input, "is_Instruction": False}


def _strip_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    return text

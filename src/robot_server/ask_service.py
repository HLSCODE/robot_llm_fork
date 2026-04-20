"""指令分类服务。

使用 OpenAI 兼容接口判断用户输入是否属于机器人可执行指令。
返回格式统一为: {"Instruction": str, "is_Instruction": bool}
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """\
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


async def classify_instruction(
    user_input: str,
    api_key: str = "",
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    enabled: bool = True,
) -> dict:
    """对用户输入进行指令分类。

    当分类功能关闭、未配置 API Key、或分类调用异常时，默认返回
    `is_Instruction=False`，避免把普通问句误判为机器人指令。
    """
    if not enabled:
        return {"Instruction": user_input, "is_Instruction": False}

    if not api_key:
        logger.info("Ask 分类未配置 API Key，跳过指令分类")
        return {"Instruction": user_input, "is_Instruction": False}

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFY_PROMPT},
                {"role": "user", "content": user_input},
            ],
            temperature=0.1,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        result = json.loads(completion.choices[0].message.content)
        return {
            "Instruction": result.get("Instruction", user_input),
            "is_Instruction": bool(result.get("is_Instruction", False)),
        }
    except Exception as exc:
        logger.warning("Ask 分类失败 (%s)，按非指令处理", exc)
        return {"Instruction": user_input, "is_Instruction": False}

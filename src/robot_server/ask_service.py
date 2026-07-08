"""指令分类服务兼容入口。

实际模型调用已收敛到 `src.llm` 能力层；本模块保留原函数签名，供
`ws_server.py` 继续调用。
"""

from __future__ import annotations

import logging

from ..llm.classifier import InstructionClassifier
from ..llm.registry import LLMRegistry

logger = logging.getLogger(__name__)


async def classify_instruction(
    user_input: str,
    api_key: str = "",
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    enabled: bool = True,
) -> dict:
    """对用户输入进行指令分类。"""
    if not enabled:
        return {"Instruction": user_input, "is_Instruction": False}

    if not api_key:
        logger.info("Ask 分类未配置 API Key，跳过指令分类")
        return {"Instruction": user_input, "is_Instruction": False}

    client = LLMRegistry.create_openai_compatible(
        provider_name="ask",
        api_key=api_key,
        model=model,
        base_url=base_url,
        default_model="gpt-4o-mini",
    )
    return await InstructionClassifier(client).classify(user_input, enabled=enabled)

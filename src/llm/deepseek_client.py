"""
DeepSeek 大模型客户端
实现基于 DeepSeek API 的 LLM 推理
（API 格式与 OpenAI 兼容）
"""
import json
import logging
from typing import List, Dict, Any, Optional

from .base import LLMClient, LLMPlanResult

logger = logging.getLogger(__name__)


class DeepSeekClient(LLMClient):
    """
    DeepSeek API 客户端
    支持 DeepSeek-R1 等模型
    API 格式与 OpenAI 兼容
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self, api_key=None, model=None, base_url=None):
        """
        初始化 DeepSeek 客户端

        Args:
            api_key: DeepSeek API Key，默认从 config.env 读取
            model: 模型名称，默认从 config.env 读取
            base_url: 自定义 API 地址，默认从 config.env 读取
        """
        # 从配置加载器读取默认值
        try:
            from ..core.config_loader import Config
            config = Config.get_instance()
            if config is not None:
                if api_key is None:
                    api_key = config.OPENAI_API_KEY
                if model is None:
                    model = config.OPENAI_MODEL
                if base_url is None:
                    base_url = config.OPENAI_BASE_URL or self.DEFAULT_BASE_URL
            else:
                print("警告：Config 实例为 None，使用默认配置")
        except Exception as e:
            print(f"加载 LLM 配置失败：{e}，使用传入参数或默认值")
        
        # 确保有默认值
        if api_key is None:
            api_key = ""
        if model is None:
            model = "deepseek-reasoner"
        if base_url is None:
            base_url = self.DEFAULT_BASE_URL
        
        self._api_key = api_key
        self._model = model
        self._client = None
        self._available = False
        self._base_url = base_url or self.DEFAULT_BASE_URL

        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                base_url=self._base_url
            )
            self._available = True
            logger.info(f"DeepSeek 客户端初始化成功，使用模型: {model}")
        except ImportError:
            logger.error("OpenAI SDK 未安装，请运行: pip install openai")
            self._available = False
        except Exception as e:
            logger.error(f"DeepSeek 客户端初始化失败: {e}")
            self._available = False

    def plan(self, user_text: str, skill_summaries: List[Dict[str, Any]]) -> LLMPlanResult:
        """
        分析用户输入，返回技能调用参数

        Args:
            user_text: 用户的自然语言输入
            skill_summaries: 技能摘要列表

        Returns:
            LLMPlanResult: 解析结果
        """
        if not self.is_available():
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error="DeepSeek API 不可用，请检查 API Key 是否正确配置"
            )

        system_prompt = self._build_system_prompt(skill_summaries)
        user_prompt = self._build_user_prompt(user_text)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=1000,
            )

            response_text = response.choices[0].message.content.strip()
            logger.debug(f"DeepSeek 原始响应: {response_text}")

            return self._parse_response(response_text)

        except Exception as e:
            logger.error(f"DeepSeek LLM 调用失败: {e}")
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"LLM 调用失败: {str(e)}"
            )

    def is_available(self) -> bool:
        """检查 LLM 服务是否可用"""
        return self._available and self._client is not None

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self._model

    def _build_system_prompt(self, skill_summaries: List[Dict[str, Any]]) -> str:
        """
        构建系统提示词

        Args:
            skill_summaries: 技能摘要列表

        Returns:
            格式化的提示词
        """
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
        """
        构建用户提示词

        Args:
            user_text: 用户输入

        Returns:
            用户提示词
        """
        return f"""用户输入："{user_text}"

请分析用户意图并返回技能调用参数（仅返回JSON）："""

    def _parse_response(self, text: str) -> LLMPlanResult:
        """
        解析 LLM 返回的文本

        Args:
            text: LLM 返回的原始文本

        Returns:
            LLMPlanResult: 解析结果
        """
        try:
            text = text.strip()

            # 处理可能的 markdown 代码块
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])

            # 尝试解析 JSON
            data = json.loads(text)

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
                fallback_suggestion=data.get("fallback_suggestion")
            )

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}, 原始文本: {text}")
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"无法解析 LLM 返回结果: {str(e)}"
            )
        except Exception as e:
            logger.error(f"解析 LLM 响应时发生错误: {e}")
            return LLMPlanResult(
                skill_id=None,
                skill_name="",
                parameters={},
                reasoning="",
                confidence=0.0,
                error=f"解析错误: {str(e)}"
            )

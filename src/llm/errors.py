"""
LLM 能力层统一异常类型。
"""


class LLMError(Exception):
    """LLM 能力层基础异常。"""


class LLMConfigError(LLMError):
    """模型配置不可用。"""


class LLMProviderError(LLMError):
    """模型 provider 调用失败。"""


class LLMTimeoutError(LLMError):
    """模型响应超时。"""


class LLMResponseParseError(LLMError):
    """模型响应解析失败。"""

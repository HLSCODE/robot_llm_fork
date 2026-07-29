# LLM 能力层重构与 MiniCPM Realtime Chat 接入方案

## 1. 背景

重构前 `src/llm/` 的职责较窄，主要服务于 AI 技能规划：

- `LLMClient.plan(user_text, skill_summaries) -> LLMPlanResult`
- OpenAI-compatible 客户端曾把模型调用、规划 prompt 构造和 JSON 解析混在一起
- `ai_controller.py` 与 `robot_server/ws_server.py` 直接按配置选择具体客户端

随着项目需要接入更多模型能力，尤其是 MiniCPM-o Realtime Chat 这类 WebSocket 协议模型，继续把 `src/llm/` 定义为“规划后端”会变得不够自然。

本方案建议将 `src/llm/` 重构为统一的“大模型能力层”，收敛项目内所有模型相关调用，并通过策略模式接入不同 provider。业务层只关心“当前使用什么模型”和“该模型提供哪些能力”，不关心底层是 HTTP、OpenAI-compatible API、WebSocket Realtime API，还是未来的本地推理服务。

## 2. 目标

1. 统一模型能力入口，收敛 OpenAI、DeepSeek、DashScope、MiniCPM 等调用。
2. 将“模型调用”和“机器人规划业务”解耦。
3. 支持异步文本对话、流式对话、规划、图文理解和后续音频能力扩展。
4. MiniCPM Realtime Chat 的上游 WebSocket 由 MiniCPM provider 内部维护。
5. 外部 WebSocket 服务仍由 `robot_server/ws_server.py` 维护，只负责客户端连接、鉴权、事件转发和业务编排。
6. `ai_chat`、`ai_status` 和聊天 action 直接切换到新能力层，不保留旧接口适配。

## 3. 非目标

1. `src/llm/` 不负责前端 WebSocket 会话管理。
2. `src/llm/` 不负责机器人动作执行、技能展开、状态广播。
3. `src/llm/` 不直接依赖 `websocket` 客户端对象。
4. `src/llm/` 不吞并 `skill_system`、`robot_server`、`ai_integration` 的业务职责。
5. 本重构后 OpenAI-compatible 服务统一由 `OpenAICompatibleClient` 承载，不保留 provider 薄包装。

## 4. 新职责边界

### 4.1 `src/llm/`

负责模型能力：

- 模型 provider 创建与选择
- 普通文本对话
- 流式文本对话
- 图文输入
- 语音/音频扩展
- MiniCPM Realtime 协议封装
- 统一返回类型、错误类型、流式事件类型
- `SkillPlanner` 这类“使用模型能力完成规划”的上层模型应用

### 4.2 `robot_server/ws_server.py`

负责服务端 WebSocket：

- 维护前端连接
- 接收前端 action
- 调用 `src/llm/` 能力
- 将 `LLMStreamEvent` 翻译成前端 `chat_data`、`ai_preview_ready` 等事件
- 调用技能系统和机器人执行器

### 4.3 `skill_system`

负责技能：

- 技能注册
- 技能查询
- 技能参数校验
- 技能展开为动作序列

### 4.4 `ai_integration`

负责 GUI 执行上下文：

- 初始化并持有 `LLMRegistry`
- 初始化并持有 `SkillEngine`
- 保存 `voice_interaction` 生成的当前动作预览
- 通过 `ExecutionBridge` 执行动作序列

不再负责自然语言输入解析、意图识别或技能规划；这些统一收敛到 `voice_interaction`。

### 4.5 `voice_interaction`

负责统一对话和意图入口：

- GUI 文本输入
- 真实语音 ASR 文本
- 后续 WebSocket 远程调用入口
- `chat / command / vision_question / session_control` 路由
- command 场景下调用 `SkillPlanner` 并生成动作预览

## 5. 总体架构

```text
GUI / Voice ASR / WebSocket Client
    |
    | text / event
    v
voice_interaction
    |
    | uses
    |------------------------------|
    v                              v
src/llm                       ai_integration
    |                         |
    | LLMRegistry             | ExecutionBridge
    v
provider strategy            current robot process
    |----------------------------------|
    | OpenAI-compatible HTTP           |
    | DeepSeek OpenAI-compatible HTTP  |
    | DashScope OpenAI-compatible HTTP |
    | MiniCPM Realtime WebSocket       |
    |----------------------------------|
```

关键原则：

```text
前端 WebSocket 由 ws_server.py 维护
模型上游 WebSocket 由 provider 内部维护
业务层只消费 task 层的 chat / stream_chat / plan 等异步能力
```

## 6. 目标目录结构

建议逐步调整为：

```text
src/llm/
  __init__.py
  base.py
  types.py
  errors.py
  registry.py
  providers/
    __init__.py
    openai_compatible.py
    minicpm_realtime.py
  tasks/
    __init__.py
    profiles.py
    runner.py
    classifier.py
    planner.py
    vision.py
    repeat.py
```

说明：

- `types.py`：统一消息、结果、流式事件、能力枚举。
- `base.py`：抽象接口或 Protocol。
- `registry.py`：根据配置创建 provider。
- `providers/`：具体 provider 策略实现。
- `openai_compatible.py`：复用 OpenAI-compatible 的通用实现，OpenAI、DeepSeek、DashScope 只传配置差异。
- `minicpm_realtime.py`：封装 MiniCPM-o Realtime Chat 协议。
- `tasks/profiles.py`：`TaskProfile` 类型和通用默认对话 profile。
- `tasks/runner.py`：普通 LLM 任务执行器，把 `TaskProfile` 应用到 `chat()` / `stream_chat()`。
- `tasks/classifier.py`：机器人动作指令分类场景及其默认 profile。
- `tasks/planner.py`：机器人技能规划场景、默认 profile 和 `LLMPlanResult` 解析。
- `tasks/vision.py`：多摄像头视觉融合观察场景及其默认 profile，支持 `observe()` / `stream_observe()`。
- `tasks/repeat.py`：文本原样返回场景及其默认 profile，支持 `repeat()` / `stream_repeat()`。

## 7. 核心类型设计

### 7.1 消息类型

```python
from dataclasses import dataclass
from typing import Any, Literal, Optional

MessageRole = Literal["system", "user", "assistant"]
ContentType = Literal["text", "image", "audio"]

@dataclass
class LLMContentPart:
    type: ContentType
    text: Optional[str] = None
    data: Optional[str] = None
    mime_type: Optional[str] = None

@dataclass
class LLMMessage:
    role: MessageRole
    content: str | list[LLMContentPart]
```

说明：

- 普通文本消息用 `content: str`。
- 多模态消息用 `content: list[LLMContentPart]`。
- MiniCPM Realtime Chat 支持字符串或多模态列表，可以直接映射。

### 7.2 对话结果

```python
@dataclass
class LLMChatResult:
    text: str
    model: str
    provider: str
    raw: Any = None
    usage: Optional[dict[str, Any]] = None
    metrics: Optional[dict[str, Any]] = None
```

### 7.3 流式事件

```python
from typing import Literal

StreamEventType = Literal[
    "session_started",
    "text_delta",
    "audio_delta",
    "done",
    "error",
    "metrics",
]

@dataclass
class LLMStreamEvent:
    type: StreamEventType
    text_delta: str = ""
    audio_data: Optional[str] = None
    text: str = ""
    error: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None
    raw: Any = None
```

### 7.4 能力声明

```python
from enum import Enum

class LLMCapability(str, Enum):
    CHAT = "chat"
    STREAM_CHAT = "stream_chat"
    VISION_CHAT = "vision_chat"
    AUDIO_CHAT = "audio_chat"
    TTS = "tts"
    PLANNING = "planning"
```

Provider 可以通过 `capabilities()` 返回自己支持的能力。

### 7.5 TaskProfile

`TaskProfile` 用于描述一个固定大模型使用场景，例如“机器人技能规划”或“动作指令分类”。它不是 provider，而是 provider 之上的任务配置：

```python
from src.llm import TaskProfile

CUSTOM_PLANNER_PROFILE = TaskProfile(
    name="lab_robot_planner",
    system_prompt_template="你是实验室机器人规划助手。\n\n可用技能：\n$skill_desc",
    temperature=0.2,
    max_tokens=800,
    response_format="json",
    default_provider="dashscope",
    enable_thinking=False,
)
```

语义边界：

- `TaskProfile` 管提示词模板、默认 provider 和模型调用参数。
- `enable_thinking` / `reasoning_effort` 属于 task 级语义配置，调用时仍可覆盖；具体如何映射到请求体由 provider 负责。
- `TaskRunner` 管普通 LLM 调用的消息构造和 profile 注入。
- `SkillPlanner`、`InstructionClassifier` 管业务输入构造和结果解析。
- `OpenAICompatibleClient`、`MiniCPMRealtimeClient` 管具体模型协议。
- 调用方可以临时传入 `system_prompt` 覆盖默认系统提示词。

## 8. 抽象接口设计

### 8.1 基础模型接口

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

class BaseLLMClient(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        pass

    @abstractmethod
    def capabilities(self) -> set[LLMCapability]:
        pass

    async def chat(
        self,
        messages: list[LLMMessage],
        **options,
    ) -> LLMChatResult:
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options,
    ) -> AsyncIterator[LLMStreamEvent]:
        raise NotImplementedError

    async def close(self) -> None:
        pass
```

### 8.2 异步 task 边界

```python
planner = SkillPlanner(llm)
result = await planner.plan(user_text, skill_summaries)
```

当前实现已删除 `sync_utils.py`、`plan_sync()`、`chat_sync()` 和 provider
`plan()`。Qt 后台工作线程使用 `asyncio.run()` 驱动完整异步交互轮次，不保留同步
兼容入口。

## 9. TaskProfile 场景封装设计

`SkillPlanner` 和 `InstructionClassifier` 可以理解为封装好的固定场景：

- `TaskRunner`：普通 LLM 调用，默认使用 `GENERAL_CHAT_PROFILE`。
- `SkillPlanner`：机器人技能规划，默认使用 `ROBOT_PLANNER_PROFILE`。
- `InstructionClassifier`：判断文本是否为机器人动作指令，默认使用 `INSTRUCTION_CLASSIFIER_PROFILE`。
- `VisionFusionTask`：融合多个摄像头画面，默认使用 `VISION_FUSION_PROFILE`。
- `RepeatTask`：严格原样返回输入文本，默认使用 `REPEAT_PROFILE`。

它们都支持三种使用方式：

```python
# 1. 使用默认场景
result = await registry.skill_planner.plan(user_text, skill_summaries)

# 2. 临时覆盖系统提示词
result = await registry.skill_planner.plan(
    user_text,
    skill_summaries,
    system_prompt="你是一个更严格的机器人规划助手，只允许返回高置信度结果。",
)

# 3. 注入完整 TaskProfile
result = await registry.skill_planner.plan(
    user_text,
    skill_summaries,
    profile=CUSTOM_PLANNER_PROFILE,
)
```

这种结构让业务层关注“使用哪个任务场景”，而不是散落维护 prompt、temperature、max_tokens 和 JSON 格式要求。

普通 LLM 调用可以直接使用 `TaskRunner`：

```python
from src.llm import TaskProfile

summary_profile = TaskProfile(
    name="summary",
    system_prompt_template="你是摘要助手，用三句话总结用户内容。",
    temperature=0.2,
    max_tokens=300,
)

result = await registry.chat(
    user_text="这里是一段很长的文本...",
    profile=summary_profile,
)
```

流式调用同样支持：

```python
async for event in registry.stream_chat(
    user_text="解释一下这段代码",
    system_prompt="你是一个耐心的代码讲解助手。",
):
    ...
```

## 10. SkillPlanner 设计

`SkillPlanner` 是“使用模型完成机器人技能规划”的模型应用，不应该和具体 provider 绑定。

```python
class SkillPlanner:
    def __init__(
        self,
        llm: BaseLLMClient,
        profile: TaskProfile = ROBOT_PLANNER_PROFILE,
    ) -> None:
        self._llm = llm
        self._profile = profile

    async def plan(
        self,
        user_text: str,
        skill_summaries: list[dict[str, Any]],
        system_prompt: str | None = None,
        profile: TaskProfile | None = None,
    ) -> LLMPlanResult:
        active_profile = profile or self._profile
        messages = [
            LLMMessage(role="system", content=system_prompt or active_profile.render_system_prompt(
                skill_desc=self._build_skill_desc(skill_summaries),
            )),
            LLMMessage(role="user", content=self._build_user_prompt(user_text)),
        ]
        result = await self._llm.chat(
            messages,
            **active_profile.chat_options(),
        )
        return self._parse_response(result.text)
```

迁移后：

- OpenAI、DeepSeek、DashScope、MiniCPM 都只需要实现 `chat()`。
- 技能规划 prompt 和 JSON 解析只存在一份。
- 后续修改规划格式不会重复改多个 provider。

## 11. Provider 策略模式

### 11.1 OpenAI-compatible Provider

OpenAI、DeepSeek、DashScope 都可复用同一个底层类：

```python
class OpenAICompatibleClient(BaseLLMClient):
    def __init__(
        self,
        provider_name: str,
        api_key: str,
        model: str,
        base_url: str = "",
    ) -> None:
        ...

    async def chat(self, messages: list[LLMMessage], **options) -> LLMChatResult:
        ...

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options,
    ) -> AsyncIterator[LLMStreamEvent]:
        ...
```

Provider 差异通过配置表达：

```python
OpenAICompatibleClient(
    provider_name="deepseek",
    api_key=config.OPENAI_API_KEY,
    model=config.OPENAI_MODEL or "deepseek-reasoner",
    base_url=config.OPENAI_BASE_URL or "https://api.deepseek.com/v1",
)
```

### 11.2 MiniCPM Realtime Provider

MiniCPM-o Realtime Chat 不是 OpenAI-compatible HTTP 接口，而是 WebSocket 协议。它应该独立实现：

```python
class MiniCPMRealtimeClient(BaseLLMClient):
    def __init__(
        self,
        gateway_host: str,
        gateway_port: int,
        ws_scheme: str = "wss",
        gateway_path_prefix: str = "",
        model: str = "minicpm-o",
        timeout_s: float = 60.0,
    ) -> None:
        ...

    async def chat(self, messages: list[LLMMessage], **options) -> LLMChatResult:
        ...

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options,
    ) -> AsyncIterator[LLMStreamEvent]:
        ...
```

注意：

- 这里维护的是 MiniCPM 上游 WebSocket。
- 不接收 `ws_server.py` 的前端 WebSocket。
- `ws_server.py` 只消费 `stream_chat()` 产出的事件。

## 12. MiniCPM Realtime Chat 协议接入

根据 MiniCPM-o Realtime API 文档，Chat 模式入口为：

```text
wss://host/v1/realtime?mode=chat
```

基本生命周期：

```text
connect
  <- session.queued / session.queue_update optional
  <- session.queue_done
  -> session.init
  <- session.created
  -> input.append
  <- response.output.delta
  <- response.done
  -> session.close
  <- session.closed or websocket close
```

### 12.1 请求构造

`chat()` 使用 non-streaming：

```json
{
  "type": "input.append",
  "input": {
    "messages": [
      { "role": "user", "content": "请只回答：测试" }
    ],
    "streaming": false,
    "generation": {
      "max_new_tokens": 512,
      "length_penalty": 1.1
    },
    "tts": {
      "enabled": false
    },
    "omni_mode": false,
    "use_tts_template": false,
    "enable_thinking": false
  }
}
```

`stream_chat()` 使用 streaming：

```json
{
  "type": "input.append",
  "input": {
    "messages": [
      { "role": "user", "content": "你好" }
    ],
    "streaming": true,
    "generation": {
      "max_new_tokens": 512,
      "length_penalty": 1.1
    },
    "tts": {
      "enabled": false
    }
  }
}
```

当 MiniCPM 请求启用 TTS 且调用方没有显式传入 `tts.ref_audio_data` / `ref_audio_data` 时，`MiniCPMRealtimeClient` 默认读取应用内参考音频 `assets/ref_audio/ref_minicpm_signature.wav`，将 16-bit PCM 采样转换为 little-endian float32 裸音频数据，再 base64 后写入 `input.tts.ref_audio_data`。

### 12.2 响应映射

MiniCPM 事件映射到统一事件：

| MiniCPM event | 条件 | LLM event |
| --- | --- | --- |
| `session.created` | 会话创建成功 | `session_started` |
| `response.output.delta` | `kind == "text"` | `text_delta` |
| `response.output.delta` | `kind == "audio"` | `audio_delta` |
| `response.done` | turn 完成 | `done` |
| `error` | 服务端错误 | `error` |
| `session.closed` | 会话关闭 | 可忽略或作为结束状态 |

### 12.3 `chat()` 处理方式

`chat()` 可以复用 `stream_chat()`：

```python
async def chat(self, messages: list[LLMMessage], **options) -> LLMChatResult:
    text_parts = []
    final_text = ""
    raw_done = None

    async for event in self.stream_chat(messages, streaming=False, **options):
        if event.type == "text_delta":
            text_parts.append(event.text_delta)
        elif event.type == "done":
            final_text = event.text or "".join(text_parts)
            raw_done = event.raw
        elif event.type == "error":
            raise LLMProviderError(event.error or "MiniCPM realtime error")

    return LLMChatResult(
        text=final_text or "".join(text_parts),
        model=self._model,
        provider="minicpm_realtime",
        raw=raw_done,
    )
```

### 12.4 连接关闭

每次 turn 可以使用短连接，流程简单、状态隔离好：

```text
one chat call -> one upstream websocket -> close
```

未来如果需要多轮低延迟会话，可以扩展 `MiniCPMRealtimeSession`：

```python
async with llm.open_realtime_session() as session:
    async for event in session.stream_chat(messages):
        ...
```

第一阶段不建议引入长连接会话池，避免生命周期和并发复杂度过早上升。

## 13. 外部 WebSocket 如何使用 `src/llm`

`ws_server.py` 继续维护前端 WebSocket：

```python
async def _handle_chat_send(self, websocket, data: dict) -> None:
    messages = parse_frontend_messages(data)
    llm = self._llm_registry.get_chat_client()

    async for event in llm.stream_chat(messages):
        await websocket.send(self._json_msg(map_llm_event_to_frontend(event)))
```

映射函数示例：

```python
def map_llm_event_to_frontend(event: LLMStreamEvent) -> dict:
    if event.type == "text_delta":
        return {
            "event": "chat_data",
            "type": "chunk",
            "text_delta": event.text_delta,
            "packet": event.raw,
        }
    if event.type == "audio_delta":
        return {
            "event": "chat_data",
            "type": "chunk",
            "audio_data": event.audio_data,
            "packet": event.raw,
        }
    if event.type == "done":
        return {
            "event": "chat_data",
            "type": "done",
            "text": event.text,
            "packet": event.raw,
        }
    if event.type == "error":
        return {
            "event": "error",
            "message": event.error or "LLM 调用失败",
        }
    return {
        "event": "chat_data",
        "type": event.type,
        "packet": event.raw,
    }
```

这样前端 WebSocket 与 MiniCPM 上游 WebSocket 完全解耦。

## 14. Registry 设计

`LLMRegistry` 负责根据配置创建 provider：

```python
class LLMRegistry:
    @classmethod
    def from_config(cls, config) -> "LLMRegistry":
        return cls(
            config=config,
            default_provider=config.LLM_DEFAULT_PROVIDER,
        )

    def get_provider(self, provider: str | None = None) -> BaseLLMClient:
        # 按 provider 名称懒加载并缓存；TaskProfile 通过
        # get_client_for_profile() 复用同一解析规则。
        ...

    async def close(self) -> None:
        # 幂等关闭全部已加载 provider。
        ...
```

后续通过 `TaskProfile.default_provider` 和调用时 `provider` 覆盖选择不同模型：

```env
LLM_DEFAULT_PROVIDER=minicpm
```

第一阶段只保留 `LLM_DEFAULT_PROVIDER` 作为全局默认 provider。

## 15. 配置建议

### 15.1 第一阶段：兼容现有配置

继续支持：

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
LLM_DEFAULT_PROVIDER=dashscope

MINICPM_GATEWAY_HOST=10.10.17.15
MINICPM_GATEWAY_PORT=8006
MINICPM_WS_SCHEME=wss
MINICPM_GATEWAY_PATH_PREFIX=
MINICPM_REALTIME_PATH=/v1/realtime
```

新增 provider 值：

```env
LLM_DEFAULT_PROVIDER=minicpm
```

语义：

- `openai` / `deepseek` / `dashscope`：OpenAI-compatible HTTP provider
- `minicpm`：MiniCPM Realtime WebSocket provider

### 15.2 第二阶段：Provider Registry + TaskProfile 默认 provider

当业务需要“某个 task 默认用 MiniCPM，某次调用临时用 DashScope”时，不再新增环境变量，而是在 profile 或调用参数中声明：

```python
TaskProfile(
    name="vision_fusion",
    default_provider="minicpm",
    response_mode="voice_stream",
)

await registry.chat(
    user_text="你好",
    provider="dashscope",
)
```

此时 `LLM_DEFAULT_PROVIDER` 是全局默认 provider。

## 16. 迁移计划

### 阶段 1：建立统一类型和接口

新增：

- `src/llm/types.py`
- `src/llm/errors.py`
- 扩展 `src/llm/base.py`

当前结果：

- `LLMPlanResult` 作为 task 规划结果保留。
- Provider 只实现 `BaseLLMClient` 异步能力，不再实现 `plan()`。

目标：

- 允许新 provider 实现 `chat()` 和 `stream_chat()`

### 阶段 2：提取 TaskProfile、TaskRunner、InstructionClassifier 和 SkillPlanner

新增：

- `src/llm/tasks/profiles.py`
- `src/llm/tasks/runner.py`
- `src/llm/tasks/classifier.py`
- `src/llm/tasks/planner.py`
- `src/llm/tasks/vision.py`
- `src/llm/tasks/repeat.py`

迁移：

- 从旧 OpenAI-compatible 客户端中提取规划系统提示词到 `ROBOT_PLANNER_PROFILE`
- 从 Ask 服务中提取动作指令分类提示词到 `INSTRUCTION_CLASSIFIER_PROFILE`
- 提取 `_build_user_prompt`
- 提取 `_parse_response`

目标：

- 固定场景 prompt 和默认模型参数只有一份
- 调用方可以通过 `system_prompt` 或 `TaskProfile` 覆盖不同场景
- 普通 LLM 调用通过 `TaskRunner` 统一接入 `TaskProfile`
- 旧客户端可以通过 `SkillPlanner` 实现 `plan()`

### 阶段 3：重构 OpenAI/DeepSeek/DashScope

新增：

- `src/llm/providers/openai_compatible.py`

调整：

- `LLMRegistry` 直接创建 `OpenAICompatibleClient(provider_name=...)`
- DeepSeek / DashScope 只保留 provider 名称、默认模型和默认 Base URL 配置
- 不再保留 `OpenAIClient` / `DeepSeekClient` / `DashScopeClient` 薄包装文件

目标：

- OpenAI-compatible 逻辑只维护一份

### 阶段 4：引入 LLMRegistry

新增：

- `src/llm/registry.py`

替换：

- `ai_controller.py` 中手写的 provider 分支
- `ws_server.py` 中手写的 provider 分支

目标：

- 模型选择逻辑集中在 `src/llm/registry.py`

### 阶段 5：接入 MiniCPM Realtime Chat

新增：

- `src/llm/providers/minicpm_realtime.py`

实现：

- `_build_realtime_url()`
- `_connect()`
- `_wait_queue_done()`
- `_send_session_init()`
- `_send_input_append()`
- `_read_stream_events()`
- `chat()`
- `stream_chat()`

目标：

- `LLM_DEFAULT_PROVIDER=minicpm` 时，规划和聊天可以走 MiniCPM Realtime Chat
- MiniCPM 上游 WebSocket 只存在于 provider 内部

### 阶段 6：迁移 `ws_server.py` 的聊天 action

旧实现中 MiniCPM 聊天代理直接连接 MiniCPM 网关并规范化响应。

迁移后：

- `chat_connect` 仍由 `ws_server.py` 维护前端会话标记
- `chat` 调用 `llm.stream_chat()`
- `chat_data` 事件由统一 `LLMStreamEvent` 映射得到
- 删除旧 MiniCPM `/ws/chat` 代理模块，不保留兼容分支
- 新 Realtime provider 仅处理 `/v1/realtime?mode=chat`

### 阶段 7：清理旧接口

在所有调用点迁移后：

- 已删除 `LLMClient.plan()` 和同步 wrapper
- 统一改用 `SkillPlanner.plan()`
- README 和 `docs/websocket-api.md` 更新配置说明

## 17. 调用示例

### 17.1 普通聊天

```python
registry = LLMRegistry.from_config(config)
llm = registry.get_chat_client()

result = await registry.chat(
    user_text="你好",
    system_prompt="你是一个有用的助手",
)

print(result.text)
```

### 17.2 流式聊天

```python
async for event in llm.stream_chat(messages):
    if event.type == "text_delta":
        print(event.text_delta, end="")
    elif event.type == "done":
        print(event.text)
```

### 17.3 技能规划

```python
registry = LLMRegistry.from_config(config)
planner = registry.skill_planner

result = await planner.plan(user_text, skill_summaries)

if result.is_valid():
    preparation = command_runtime.prepare(
        SkillMatchResult(
            skill_id=result.skill_id,
            skill_name=result.skill_name,
            confidence=result.confidence,
            extracted_params=result.parameters,
            reasoning=result.reasoning,
        ),
        source="websocket-ai",
        plan=result.__dict__,
    )
```

### 17.4 `ws_server.py` 中转发流式事件

```python
async def _handle_chat_send(self, websocket, data: dict) -> None:
    messages = parse_frontend_messages(data)
    llm = self._llm_registry.get_chat_client()

    async for event in llm.stream_chat(messages):
        frontend_event = map_llm_event_to_frontend(event)
        await websocket.send(self._json_msg(frontend_event))
```

## 18. 错误处理

建议统一错误类型：

```python
class LLMError(Exception):
    pass

class LLMConfigError(LLMError):
    pass

class LLMProviderError(LLMError):
    pass

class LLMTimeoutError(LLMError):
    pass

class LLMResponseParseError(LLMError):
    pass
```

MiniCPM provider 应处理：

- 连接失败
- 排队超时
- `session.created` 未返回
- `response.done` 超时
- 上游 `error` 事件
- WebSocket 异常关闭
- 非法 JSON 包

业务层映射：

```python
try:
    result = await planner.plan(text, skill_summaries)
except LLMConfigError:
    await websocket.send(error_event("LLM 配置不可用"))
except LLMTimeoutError:
    await websocket.send(error_event("LLM 响应超时"))
except LLMError as exc:
    await websocket.send(error_event(f"LLM 调用失败: {exc}"))
```

## 19. 超时与并发

建议默认：

- WebSocket 建连超时：`30s`
- 等待 `session.queue_done`：`60s`
- 等待首个输出：`60s`
- 单 turn 总超时：`120s`
- 最大消息大小沿用当前 `100 MB`

并发策略：

- 第一阶段每次 `chat()` 创建独立上游连接。
- 不共享 MiniCPM 上游 WebSocket。
- 不在 provider 内持有前端连接引用。
- 如果业务层取消任务，应关闭上游连接。

## 20. 测试方案

### 20.1 单元测试

覆盖：

- `SkillPlanner._parse_response()`
- markdown code block JSON 解析
- 无效 JSON 错误返回
- OpenAI message 转换
- MiniCPM message 转换
- MiniCPM event 到 `LLMStreamEvent` 的映射

### 20.2 Fake Transport 测试

MiniCPM provider 可以把底层连接抽象成内部 transport factory，便于测试：

```python
class MiniCPMRealtimeClient:
    def __init__(..., transport_factory=None):
        self._transport_factory = transport_factory or self._default_transport_factory
```

测试时注入 fake transport：

```python
fake = FakeRealtimeTransport([
    {"type": "session.queue_done"},
    {"type": "session.created", "session_id": "sess_test"},
    {"type": "response.output.delta", "kind": "text", "text": "测试"},
    {"type": "response.done", "text": "测试", "reason": "turn_end"},
])
```

注意：

- 这是 provider 内部测试用抽象。
- 不要求业务层传入 WebSocket。

### 20.3 集成测试

覆盖：

- `LLM_DEFAULT_PROVIDER=openai`
- `LLM_DEFAULT_PROVIDER=deepseek`
- `LLM_DEFAULT_PROVIDER=dashscope`
- `LLM_DEFAULT_PROVIDER=minicpm`
- `ai_status` 显示当前 provider、model、可用能力
- `ai_chat` 能正常生成规划预览
- MiniCPM `chat` 能返回 `chat_data` chunk/done

## 21. 版本切换策略

- 不保留同步 LLM 或旧 provider 规划兼容入口。
- Provider 直接实现 `BaseLLMClient`，规划统一由 `SkillPlanner` 异步执行。
- OpenAI-compatible 服务统一由 `LLMRegistry` 创建
  `OpenAICompatibleClient`。
- 调用方必须直接迁移到 async task API。

## 22. 文档与状态接口更新

`ai_status` 建议扩展：

```json
{
  "event": "ai_status",
  "llm_available": true,
  "provider": "minicpm",
  "model": "minicpm-o",
  "capabilities": ["chat", "stream_chat", "vision_chat"],
  "api_key_set": true,
  "processing": false,
  "has_preview": false
}
```

README 建议更新：

- `src/llm/` 描述为“大模型能力层”
- `LLM_DEFAULT_PROVIDER=minicpm` 的说明
- MiniCPM Realtime Chat 和旧 MiniCPM 代理的区别

`docs/websocket-api.md` 建议更新：

- MiniCPM 聊天链路不再直接描述为服务端代理旧网关
- 说明前端仍通过主控 WebSocket 的 `chat` action 使用
- 服务端内部使用 `src/llm/providers/minicpm_realtime.py`

## 23. 推荐落地顺序

推荐顺序：

1. 新增统一类型和 `BaseLLMClient`，不动业务。
2. 新增 `SkillPlanner`，让 OpenAI/DeepSeek 复用同一套规划逻辑。
3. 新增 `OpenAICompatibleClient`，减少重复代码。
4. 新增 `LLMRegistry`，替换 `ai_controller.py` 和 `ws_server.py` 中重复 provider 选择逻辑。
5. 新增 `MiniCPMRealtimeClient.chat()`。
6. 新增 `MiniCPMRealtimeClient.stream_chat()`。
7. 将 `chat` action 切换为消费 `llm.stream_chat()`。
8. 更新配置示例和 WebSocket API 文档。
9. 删除旧 MiniCPM 代理路径。

## 24. 最终形态

业务侧只需要这样使用：

```python
registry = LLMRegistry.from_config(config)

chat_model = registry.get_chat_client()
planner = registry.skill_planner

reply = await chat_model.chat(messages)
plan = await planner.plan(user_text, skill_summaries)
```

MiniCPM provider 内部自行处理：

```text
Realtime URL
WebSocket connect
session.queue_done
session.init
input.append
response.output.delta
response.done
session.close
```

`ws_server.py` 只处理：

```text
frontend websocket receive
call llm capability
map LLM event to frontend event
send frontend websocket
```

这能让 `src/llm/` 成为清晰的模型能力层：能力统一、provider 可替换、业务边界稳定，同时为 MiniCPM Realtime、多模态、语音等能力留下扩展空间。

## 25. 参考资料

- MiniCPM-o Realtime API 概览：https://minicpmo45.modelbest.cn/docs/zh/realtime-api/overview/
- MiniCPM-o Realtime Chat 模式：https://minicpmo45.modelbest.cn/docs/zh/realtime-api/chat/
- 当前 LLM 抽象：`src/llm/base.py`
- 当前 OpenAI-compatible provider：`src/llm/providers/openai_compatible.py`
- 当前服务端 LLM 初始化：`src/robot_server/ws_server.py`
- 当前 WebSocket API 文档：`docs/websocket-api.md`

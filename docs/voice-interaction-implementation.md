# 唤醒后语音对话需求实施方案

## 1. 背景

项目已经将大模型调用收敛到 `src/llm/`：

- `LLMRegistry` 负责按配置创建不同 provider。
- `TaskRunner` 负责普通对话。
- `InstructionClassifier` 负责意图识别。
- `SkillPlanner` 负责机器人技能规划。
- `VisionFusionTask` 负责多摄像头视觉融合问答。
- `RepeatTask` 负责文本原样返回。
- `MiniCPMRealtimeClient` 已支持流式文本、音频输入、TTS 音频输出事件。

本需求是在此基础上实现一套“唤醒后持续对话”的执行逻辑。语音能力第一阶段可先用手动输入代替，先跑通会话状态、意图识别和任务分发。

## 2. 目标

1. 接入唤醒词模型，用户说唤醒词后进入可对话 session。
2. 唤醒后在一定时间内持续监听用户语音，并通过 ASR 转成文本。
3. 对每句用户输入先做意图识别，区分聊天、命令、视觉问题、会话控制等。
4. 根据意图调用对应 task：
   - `chat`：普通大模型对话。
   - `command`：技能规划，生成指令序列。
   - `vision_question`：采集相机画面并做视觉融合问答。
   - `session_control`：结束、暂停、取消任务等。
5. 支持流式文本和后续语音回复。
6. 第一阶段不依赖真实语音链路，使用 GUI 手动输入模拟 ASR 文本。

## 3. 非目标

1. 第一阶段不实现真实唤醒词模型。
2. 第一阶段不实现真实 ASR。
3. 第一阶段不要求实时播放 TTS 音频，可以先处理文本流。
4. 不把语音输入、会话管理、技能执行逻辑塞进 `src/llm/`。
5. 不让 LLM task 直接执行机器人动作。

## 4. 推荐架构

新增独立编排层：

```text
src/voice_interaction/
  __init__.py
  types.py
  session.py
  controller.py
  router.py
  adapters.py
```

职责划分：

- `types.py`：定义 session 状态、输入事件、输出事件、执行结果。
- `session.py`：维护唤醒状态、超时、历史上下文、当前任务状态。
- `controller.py`：主入口，接收文本输入，驱动 classify -> route -> response。
- `router.py`：根据 intent 调用对应 task。
- `adapters.py`：预留唤醒词、ASR、TTS、音频播放适配接口。

第一阶段只需要实现 `types.py`、`session.py`、`controller.py`、`router.py`，语音适配器用 mock。

## 5. 总体流程

```text
sleeping
  |
  | wake()
  v
awake/listening
  |
  | handle_text("用户手动输入，模拟 ASR")
  v
classifying
  |
  | InstructionClassifier.classify()
  v
routing
  |-------------------------|
  | chat                    | -> TaskRunner.stream_chat()
  | command                 | -> SkillPlanner.plan() -> skill engine
  | vision_question         | -> capture images -> VisionFusionTask.stream_observe()
  | session_control         | -> end / pause / cancel
  |-------------------------|
  v
responding
  |
  | done / error / cancelled
  v
awake/listening 或 sleeping
```

## 6. 核心状态模型

建议定义：

```python
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic


class VoiceSessionState(str, Enum):
    SLEEPING = "sleeping"
    AWAKE = "awake"
    RESPONDING = "responding"
    PAUSED = "paused"


@dataclass
class VoiceSession:
    state: VoiceSessionState = VoiceSessionState.SLEEPING
    timeout_s: float = 30.0
    last_activity_at: float = field(default_factory=monotonic)
    history: list = field(default_factory=list)
    current_task_id: str | None = None

    def wake(self) -> None:
        self.state = VoiceSessionState.AWAKE
        self.touch()

    def sleep(self) -> None:
        self.state = VoiceSessionState.SLEEPING
        self.history.clear()
        self.current_task_id = None

    def touch(self) -> None:
        self.last_activity_at = monotonic()

    def is_expired(self) -> bool:
        return monotonic() - self.last_activity_at > self.timeout_s
```

状态规则：

- `SLEEPING`：只接受唤醒事件，不处理普通文本。
- `AWAKE`：可接收用户输入。
- `RESPONDING`：正在调用 LLM 或执行动作，可根据策略允许打断。
- `PAUSED`：暂停回应，但 session 可保留。
- 超过 `timeout_s` 自动回到 `SLEEPING`。
- `session_control.should_end_session=true` 立即回到 `SLEEPING`。

## 7. 输入输出事件

建议所有输出统一成事件，便于 GUI 和 WebSocket 共用：

```python
from dataclasses import dataclass
from typing import Any, Literal


VoiceEventType = Literal[
    "session_started",
    "session_ended",
    "intent",
    "text_delta",
    "audio_delta",
    "command_preview",
    "command_started",
    "command_finished",
    "vision_started",
    "error",
    "done",
]


@dataclass
class VoiceEvent:
    type: VoiceEventType
    text: str = ""
    text_delta: str = ""
    audio_data: str | None = None
    intent: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
```

GUI 中通过 Qt signal 转发 `VoiceEvent`，WebSocket 中可以直接转 JSON。

## 8. Controller 设计

核心入口：

```python
class VoiceInteractionController:
    def __init__(
        self,
        llm_registry,
        skill_engine=None,
        camera_provider=None,
        session: VoiceSession | None = None,
    ) -> None:
        self.llm_registry = llm_registry
        self.skill_engine = skill_engine
        self.camera_provider = camera_provider
        self.session = session or VoiceSession()

    def wake(self) -> VoiceEvent:
        self.session.wake()
        return VoiceEvent(type="session_started")

    async def handle_text(self, text: str):
        if self.session.state == VoiceSessionState.SLEEPING:
            yield VoiceEvent(type="error", text="机器人未唤醒")
            return

        if self.session.is_expired():
            self.session.sleep()
            yield VoiceEvent(type="session_ended", text="会话已超时")
            return

        self.session.touch()

        intent = await self.llm_registry.instruction_classifier.classify(text)
        yield VoiceEvent(type="intent", intent=intent)

        async for event in self._route(text, intent):
            yield event
```

`handle_text()` 第一阶段由 GUI 输入框调用；未来由 ASR 文本回调调用。

## 9. Intent 路由规则

`InstructionClassifier` 的目标输出：

```json
{
  "intent": "chat | command | vision_question | session_control",
  "is_addressed_to_robot": true,
  "should_end_session": false,
  "session_action": "none | end_session | cancel_task | pause"
}
```

路由逻辑：

```python
async def _route(self, text: str, intent: dict):
    if not intent.get("is_addressed_to_robot", True):
        return

    if intent["intent"] == "session_control":
        async for event in self._handle_session_control(intent):
            yield event
        return

    if intent["intent"] == "chat":
        async for event in self._handle_chat(text):
            yield event
        return

    if intent["intent"] == "command":
        async for event in self._handle_command(text):
            yield event
        return

    if intent["intent"] == "vision_question":
        async for event in self._handle_vision(text):
            yield event
        return
```

## 10. Chat 处理

普通聊天只调用通用对话 task：

```python
async def _handle_chat(self, text: str):
    async for event in self.llm_registry.task_runner.stream_chat(
        user_text=text,
        tts_enabled=True,
        use_tts_template=True,
    ):
        yield self._from_llm_event(event)
```

第一阶段可不传 TTS 参数：

```python
async for event in self.llm_registry.task_runner.stream_chat(user_text=text):
    ...
```

## 11. Command 处理

命令走两步：

1. `SkillPlanner` 将自然语言转成技能调用。
2. `skill_engine` 将技能调用转成动作序列或预览。

```python
async def _handle_command(self, text: str):
    if self.skill_engine is None:
        yield VoiceEvent(type="error", text="技能系统未初始化")
        return

    skill_summaries = self.skill_engine.get_skill_summaries()
    plan = await self.llm_registry.skill_planner.plan(text, skill_summaries)

    if not plan.is_valid():
        yield VoiceEvent(
            type="error",
            text=plan.error or "没有匹配到可执行技能",
        )
        return

    sequence = self.skill_engine.plan_skill_execution(
        skill_id=plan.skill_id,
        skill_name=plan.skill_name,
        confidence=plan.confidence,
        extracted_params=plan.parameters,
        reasoning=plan.reasoning,
    )

    yield VoiceEvent(
        type="command_preview",
        data={
            "plan": plan,
            "sequence": sequence,
        },
    )
```

执行策略建议：

- GUI 第一阶段只生成预览，不自动执行。
- 后续可加配置：`VOICE_AUTO_EXECUTE_COMMAND=true/false`。
- 执行动作时必须支持取消。

## 12. Vision 处理

视觉问题需要先采集图像，再调用视觉融合 task：

```python
async def _handle_vision(self, text: str):
    if self.camera_provider is None:
        yield VoiceEvent(type="error", text="视觉系统未初始化")
        return

    images = self.camera_provider.capture_llm_parts()
    yield VoiceEvent(type="vision_started")

    async for event in self.llm_registry.vision_fusion.stream_observe(
        images=images,
        question=text,
        tts_enabled=True,
        use_tts_template=True,
    ):
        yield self._from_llm_event(event)
```

第一阶段如果相机链路没准备好，可以用 mock：

```python
class MockCameraProvider:
    def capture_llm_parts(self):
        return []
```

或者先让 `_handle_vision()` 返回固定提示，验证路由。

## 13. Session Control 处理

```python
async def _handle_session_control(self, intent: dict):
    action = intent.get("session_action", "none")

    if action == "end_session" or intent.get("should_end_session"):
        self.session.sleep()
        yield VoiceEvent(type="session_ended")
        return

    if action == "cancel_task":
        await self._cancel_current_task()
        yield VoiceEvent(type="done", text="已取消当前任务")
        return

    if action == "pause":
        self.session.state = VoiceSessionState.PAUSED
        yield VoiceEvent(type="done", text="我先暂停")
        return
```

注意：

- “停下”通常是 `command`，不是结束 session。
- “没事了”“不用了”“先这样”通常结束 session。
- “取消刚才那个”通常取消任务，但不一定结束 session。

## 14. 语音能力适配层

第一阶段不实现真实语音，但接口先预留：

```python
class WakeWordAdapter:
    async def listen(self):
        ...


class ASRAdapter:
    async def transcribe(self, audio_chunk: bytes) -> str:
        ...


class TTSPlayer:
    async def play_delta(self, audio_data: str) -> None:
        ...
```

后续真实链路：

```text
WakeWordAdapter 检测唤醒
  -> controller.wake()
Microphone stream
  -> ASRAdapter
  -> controller.handle_text(asr_text)
LLMStreamEvent.audio_delta
  -> TTSPlayer.play_delta()
```

## 15. GUI 第一阶段接入

建议在 GUI 增加一个“语音会话调试面板”：

```text
[唤醒机器人] [结束会话]
输入框：模拟 ASR 文本
[发送]
状态：sleeping / awake / responding
意图：chat / command / vision_question / session_control
输出区域：流式文本
```

Qt 中不要在主线程跑 async。推荐：

```text
VoiceSessionWorker(QThread)
  -> asyncio.run(controller.handle_text())
  -> event_signal.emit(VoiceEvent)
主线程 UI
  -> 根据 event 更新界面
```

第一阶段 GUI 行为：

1. 点击“唤醒机器人”调用 `controller.wake()`。
2. 输入文本点击“发送”调用 `controller.handle_text(text)`。
3. 显示分类结果。
4. 对 `text_delta` 做流式追加。
5. 对 `command_preview` 显示技能预览。
6. 对 `session_ended` 切回未唤醒状态。

## 16. 配置建议

新增配置：

```env
VOICE_SESSION_TIMEOUT_S=30
VOICE_AUTO_EXECUTE_COMMAND=false
VOICE_TTS_ENABLED=false
VOICE_WAKE_WORD_ENABLED=false
VOICE_ASR_ENABLED=false
```

含义：

- `VOICE_SESSION_TIMEOUT_S`：唤醒后无交互多久自动休眠。
- `VOICE_AUTO_EXECUTE_COMMAND`：命令是否自动执行，第一阶段建议 false。
- `VOICE_TTS_ENABLED`：是否请求模型生成语音回复。
- `VOICE_WAKE_WORD_ENABLED`：是否启用真实唤醒词。
- `VOICE_ASR_ENABLED`：是否启用真实 ASR。

第一阶段配置：

```env
VOICE_SESSION_TIMEOUT_S=30
VOICE_AUTO_EXECUTE_COMMAND=false
VOICE_TTS_ENABLED=false
VOICE_WAKE_WORD_ENABLED=false
VOICE_ASR_ENABLED=false
```

## 17. 错误和取消

必须处理：

- LLM 不可用。
- intent JSON 解析失败。
- 命令规划失败。
- 技能系统未初始化。
- 相机不可用。
- 用户取消当前任务。
- session 超时。
- LLM stream 中断。

建议所有异常都转成 `VoiceEvent(type="error")`，不要让 GUI worker 崩溃。

## 18. 测试方案

### 18.1 单元测试

覆盖：

- session wake / sleep / timeout。
- intent 路由。
- `session_control` 行为。
- `command` 规划失败。
- `vision_question` 无相机时错误返回。
- `chat` 流式事件映射。

### 18.2 Fake LLM 测试

使用 fake registry：

```python
class FakeClassifier:
    async def classify(self, text):
        return {"intent": "chat", "is_addressed_to_robot": True, ...}
```

验证 controller 不依赖真实模型。

### 18.3 GUI 手动测试

用输入框模拟：

- “你好” -> chat。
- “抓瓶子” -> command。
- “你看到什么” -> vision_question。
- “没事了” -> session_control/end_session。
- “取消刚才那个” -> session_control/cancel_task。
- “停下” -> command。

## 19. 推荐实施顺序

1. 新增 `src/voice_interaction/types.py`。
2. 新增 `src/voice_interaction/session.py`。
3. 新增 `src/voice_interaction/controller.py`。
4. 新增 `src/voice_interaction/router.py` 或先把 `_route()` 放在 controller 内。
5. GUI 增加手动唤醒和文本输入调试入口。
6. 接入 `InstructionClassifier` 路由。
7. 接入 `TaskRunner` 聊天流。
8. 接入 `SkillPlanner` 命令预览。
9. 接入 `VisionFusionTask`，相机先 mock。
10. 增加 session 超时和取消逻辑。
11. 再接真实唤醒词。
12. 再接真实 ASR。
13. 最后接 TTS 音频播放。

## 20. 第一阶段验收标准

在无真实语音输入的情况下，通过 GUI 手动输入可以完成：

1. 点击唤醒后进入 awake 状态。
2. 超时后自动回到 sleeping 状态。
3. 输入“你好”能走 chat 并流式显示回复。
4. 输入“抓瓶子”能走 command 并生成技能规划或失败说明。
5. 输入“你看到什么”能走 vision_question。
6. 输入“没事了”能结束 session。
7. 输入“取消刚才那个”能触发 cancel_task。
8. LLM 或相机不可用时 GUI 不崩溃，有明确错误事件。

## 21. 后续扩展

后续接入真实语音后只替换 IO 层：

- 唤醒词模型只负责调用 `controller.wake()`。
- ASR 只负责产出文本并调用 `controller.handle_text(text)`。
- TTS 播放器只消费 `LLMStreamEvent.audio_delta`。
- 主业务逻辑仍保持在 `VoiceInteractionController`。

这样可以避免语音链路和机器人行为决策互相耦合，也方便 GUI、WebSocket、测试脚本复用同一套会话编排逻辑。

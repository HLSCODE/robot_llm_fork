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
   - `vision_question`：通过 `src/cameras/` 采集相机画面并做视觉融合问答。
   - `session_control`：结束、暂停、取消任务等。
5. 支持流式文本和语音回复；纯语音对话类 task 使用流式语音响应，结构化 task 使用文本响应。
6. GUI 底部文本框不需要唤醒；真实语音 IO 由 `VoiceSpeechRuntime` 独立启动，
   但文本与语音共享 GUI 的 controller、session 和历史。

## 3. 非目标

1. 不把语音输入、会话管理、技能执行逻辑塞进 `src/llm/`。
2. 不让 LLM task 直接执行机器人动作。
3. 不让项目代码依赖临时 `asr/` 目录；ASR、VAD、唤醒词和音频采集实现应位于 `src/voice_interaction/`。

## 4. 推荐架构

新增独立编排层：

```text
src/voice_interaction/
  __init__.py
  core/
    types.py
    session.py
    controller.py
    router.py
  adapters/
    cameras.py
  speech/
    audio.py
    asr.py
    vad.py
    wake_word.py
    utterance.py
    runtime.py
```

职责划分：

- `core/types.py`：定义 session 状态、输入事件、输出事件、执行结果。
- `core/session.py`：维护唤醒状态、超时、历史上下文、当前任务状态。
- `core/controller.py`：主入口，接收文本输入，驱动 classify -> route -> response。
- `core/router.py`：根据 intent 调用对应 task。
- `adapters/cameras.py`：提供 `CamerasModuleProvider`，通过注入的
  `DeviceRuntime` 相机获取视觉帧。
- `speech/audio.py`：sounddevice 麦克风采集和 float32/int16 音频工具。
- `speech/vad.py`：FunASR VAD 适配器。
- `speech/asr.py`：FunASR 语音识别适配器。
- `speech/wake_word.py`：sherpa-onnx、dummy、openWakeWord 唤醒词适配器。
- `speech/utterance.py`：VAD 事件与 RMS 静音兜底组合成一句完整 utterance。
- `speech/runtime.py`：真实语音运行时，将麦克风 -> 唤醒词 -> VAD -> ASR -> `VoiceInteractionController.handle_text()` 串起来。

GUI 仍可用手动文本直接对话；真实语音 IO 通过 `VoiceSpeechRuntime` 单独启动，
两者在 interaction 层共享会话策略，WebSocket 保持独立会话历史。

## 5. 总体流程

```text
sleeping
  |
  | wake()
  v
awake/listening
  |
  | handle_text("用户手动输入")
  v
classifying
  |
  | InstructionClassifier.classify()
  v
routing
  |-------------------------|
  | chat                    | -> TaskRunner.stream_chat(voice_response=True)
  | command                 | -> SkillPlanner.plan() -> skill engine
  | vision_question         | -> CamerasModuleProvider.capture_llm_parts() -> VisionFusionTask.stream_observe(voice_response=True)
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
        command_runtime: CommandRuntime,
        source: str,
        camera_provider=None,
        session: VoiceSession | None = None,
        turn_timeout_s: float = 90.0,
    ) -> None:
        self.llm_registry = llm_registry
        self.command_runtime = command_runtime
        self.source = source
        self.camera_provider = camera_provider
        self.session = session or VoiceSession()

    def wake(self) -> VoiceEvent:
        self.session.wake()
        return VoiceEvent(type="session_started")

    async def handle_text(self, text: str, *, require_awake: bool = True):
        # 非阻塞单轮互斥 + asyncio.timeout；当前 task 可由
        # cancel_active_turn() 从 Qt/服务线程安全取消。
        ...
```

GUI 文本、ASR 和 WebSocket 均调用 `handle_text()`；前两者共享 GUI controller，
WebSocket 使用独立 controller。

## 9. Intent 路由规则

`InstructionClassifier` 的目标输出：

```json
{
  "intent": "chat | command | vision_question | session_control | execution_control",
  "is_addressed_to_robot": true,
  "should_end_session": false,
  "session_action": "none | end_session | pause_session | resume_session",
  "execution_action": "none | cancel | pause | resume"
}
```

路由逻辑：

```python
async def _route(self, text: str, intent: dict):
    if not intent.get("is_addressed_to_robot", True):
        return

    handler = handlers[intent["intent"]]
    async for event in handler():
        yield event
```

## 10. 响应模式

每个 `TaskProfile` 需要声明响应模式：

- `response_mode="voice_stream"`：用户可听见的任务，例如普通聊天、复述、视觉问答。语音会话中传 `voice_response=True` 时，MiniCPM 等支持 TTS 的 provider 会返回 `audio_delta`。
- `response_mode="text"`：结构化或内部任务，例如 `InstructionClassifier`、`SkillPlanner`。这类任务始终走文本结果，并会剔除误传的 TTS 选项，避免语音模板污染 JSON 或规划格式。
- `enable_thinking=False`：推荐用于 JSON、原样返回、视觉融合观察等格式敏感任务，避免模型输出推理过程或影响固定响应格式。
- `command`、`session_control` 和 `execution_control` 仍应返回可理解的控制结果；
  固定反馈文本在启用 TTS 时可通过 `RepeatTask.stream_repeat()` 播报。

推荐默认划分：

```text
TaskRunner / GENERAL_CHAT_PROFILE       -> voice_stream
RepeatTask / REPEAT_PROFILE             -> voice_stream
VisionFusionTask / VISION_FUSION_PROFILE -> voice_stream
InstructionClassifier                    -> text
SkillPlanner                             -> text
```

## 11. Chat 处理

普通聊天只调用通用对话 task。语音会话中使用 `voice_response=True`，router 内部还应判断当前 provider 是否支持 TTS：

```python
async def _handle_chat(self, text: str):
    async for event in self.llm_registry.task_runner.stream_chat(
        user_text=text,
        voice_response=True,
    ):
        yield self._from_llm_event(event)
```

第一阶段可不传 TTS 参数：

```python
async for event in self.llm_registry.task_runner.stream_chat(user_text=text):
    ...
```

## 12. Command 处理

命令统一走三层：

1. `SkillPlanner` 只把自然语言转换为 `SkillMatchResult`。
2. 进程级 `ApplicationServices.commands` 调用唯一 `SkillEngine` 展开和校验动作，
   并注册版本化预览。
3. GUI 或 WebSocket 使用 `preview_id + version` 显式确认后，才能把一次性
   `ConfirmedCommand` 交给 `ExecutionService`。

`command_preview.data` 包含：

- 唯一 `preview_id` 和单调递增 `version`；
- `created_at`、`expires_at` 和 `state=pending`；
- `source`，防止 GUI 与 WebSocket 交叉确认对方的预览；
- `validation`、`sequence`、`skill_info` 和 `plan`；
- `risk.level/reasons/requires_acknowledgement`；
- `requires_confirmation=true`。

执行策略：

- 只有通过技能和动作序列校验的结果才能生成预览。
- 校验结果包含稳定 `code`；未知或非字符串 action type 返回
  `unsupported_action_type`，不会静默转换成 MOVE。
- Skill 参数声明使用 `str`、`int`、`float`、`bool` 强类型和可选物理单位，
  每个可输入参数必须通过 `parameter_bindings` 显式绑定到动作字段。展开前会拒绝
  未知输入、未绑定参数、无效字段、单位不一致及 Action Schema 范围错误，对应
  `invalid_skill_parameters`、`invalid_parameter_binding` 和
  `invalid_action_parameters`。
- 所有预览都带有 `requires_confirmation=true`，GUI 必须由用户点击执行或确认，
  WebSocket 必须收到独立的 `ai_confirm` 请求后才能进入统一执行运行时。
- 新规划会使旧预览进入 `superseded`；主动取消进入 `cancelled`；TTL 到期进入
  `expired`；成功确认进入 `confirmed` 且不可重复消费。
- MOVE、BASE_MOVE、MANIPULATE、CHANGE_GUN、VISION_RELOCALIZE 和
  TRAJECTORY 属于高风险动作，除普通确认外还必须提交独立风险确认。
- 项目不提供自动执行配置，也不保留绕过确认的兼容路径。
- 执行动作时必须支持取消。

## 12.1 会话控制与执行控制

分类结果不再复用一个含糊的“暂停/取消”字段：

- `session_control.session_action`：`end_session`、`pause_session`、
  `resume_session`，只影响对话状态。
- `execution_control.execution_action`：`cancel`、`pause`、`resume`，只进入
  `CommandRuntime.control_execution()`。

因此“先别说话”不会暂停机械臂，“暂停动作”也不会结束语音会话。

## 12.2 GUI 文本与语音共享策略

GUI 文本框和真实语音共享同一个 `VoiceInteractionController`、`VoiceSession`
和历史记录，并通过非阻塞单轮互斥避免两个线程同时修改会话或调用模型。
WebSocket 使用独立会话历史，但与 GUI 复用相同的 CommandRuntime 校验、风险和
执行控制策略。

## 12.3 LLM 生命周期

- `LLM_REQUEST_TIMEOUT_S` 约束 provider 网络请求。
- `INTERACTION_TURN_TIMEOUT_S` 约束分类、规划、聊天或视觉的一整轮交互。
- `cancel_active_turn()` 使用事件循环线程安全取消当前 async task。
- GUI 关闭和 WebSocket 附加服务停止时调用幂等 `LLMRegistry.close()`，关闭所有
  已懒加载 provider。

## 13. Vision 处理

视觉问题通过应用层相机会话采集图像，再调用视觉融合 task。GUI 默认注入
`CamerasModuleProvider`：

```python
def camera_capture_session():
    return application_services.camera_access.open("gui-voice-capture")


camera_provider = CamerasModuleProvider(
    session_factory=camera_capture_session,
    camera_name=Config.get_instance().VISION_CAMERA_NAME or None,
)
```

`session_factory` 必须由应用组装层注入，并通过 `CameraAccessService`
取得独占 `CameraSession`。provider 在抓帧成功、失败或超时后都会退出上下文并
释放租约，不直接创建、初始化或关闭底层相机。它调用 `get_latest_jpegs()` 转成
`LLMContentPart(type="image")`。如果
`VISION_CAMERA_NAME` 为空，则使用所有在线相机；如果配置了名称或序列号，
则只使用对应相机。

当相机未连接、未启动或没有最新帧时，不应把设备状态列表直接反馈给用户。适配器抛出 `CameraCaptureError`，其中 `user_message` 是适合语音播报的自然提示，`technical_detail` 只用于日志和调试数据。需要根据技术细节进一步润色时，路由层可使用 `VOICE_FEEDBACK_PROFILE` 生成一句更拟人化的反馈。

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
        voice_response=True,
    ):
        yield self._from_llm_event(event)
```

测试时如果相机链路没准备好，可以用 mock：

```python
class MockCameraProvider:
    def capture_llm_parts(self):
        return []
```

或者先让 `_handle_vision()` 返回固定提示，验证路由。

## 14. Session Control 处理

```python
async def _handle_session_control(self, intent: dict):
    action = intent.get("session_action", "none")

    if action == "end_session" or intent.get("should_end_session"):
        self.session.sleep()
        yield VoiceEvent(type="session_ended")
        return

    if action == "pause_session":
        self.session.pause()
        yield VoiceEvent(type="session_paused")
        return

    if action == "resume_session":
        self.session.resume()
        yield VoiceEvent(type="session_resumed")
        return
```

注意：

- “停下”“取消刚才那个”属于 `execution_control/cancel`，不是 session control。
- “没事了”“不用了”“先这样”通常结束 session。
- “先别说话”属于 `session_control/pause_session`，不会暂停设备。

## 15. 语音能力适配层

真实语音输入已经迁入 `src/voice_interaction/`，接口如下：

```python
class WakeWordAdapter:
    def reset(self) -> None:
        ...

    def accept_audio(self, audio_float32, sample_rate: int) -> dict:
        ...


class ASRAdapter:
    def transcribe(self, audio_float32, sample_rate: int) -> str:
        ...


class TTSPlayer:
    async def play_delta(self, audio_data: str) -> None:
        ...
```

后续真实链路：

```text
VoiceSpeechRuntime
  -> AudioCapture 读取麦克风
WakeWordAdapter 检测唤醒
  -> controller.wake()
VAD + UtteranceEndpoint 判断一句话结束
  -> ASRAdapter.transcribe()
  -> controller.handle_text(asr_text)
LLMStreamEvent.audio_delta
  -> TTSPlayer.play_delta()
```

创建运行时：

```python
from src.voice_interaction import build_voice_speech_runtime

runtime = build_voice_speech_runtime(controller)
async for event in runtime.run():
    handle_voice_event(event)
```

`build_voice_speech_runtime()` 读取 `Config.get_voice_interaction_config()`：

- `VOICE_INPUT_ENABLED=true` 才会创建真实语音输入运行时。
- 真实语音输入运行时会同时加载 ASR/VAD 与 `VOICE_WAKE_ENGINE` 指定的唤醒词模型。
- `VOICE_INPUT_ENABLED=false` 时，不加载 ASR/KWS 依赖；GUI 底部文本对话入口仍可直接使用。
- 默认 sherpa-onnx 模型路径指向 `models/kws/...`，不能指向临时 `asr/models`。

## 16. GUI 接入

GUI 底部文本框作为通用对话入口；开启真实语音输入时，额外显示语音 session 状态：

```text
[结束语音会话] [启动监听 / 停止监听] Session: 未唤醒 / 已唤醒 / 回复中，监听: 待启动 / 运行中
输入框：输入消息、问题或机器人指令，按 Enter 发送
[发送]
状态：就绪 / 对话处理中 / 语音监听中 / 语音识别中
意图：chat / command / vision_question / session_control
输出区域：流式文本
```

Qt 中不要在主线程跑 async。推荐：

```text
VoiceSessionWorker(QThread)
  -> asyncio.run(controller.handle_text())
  -> event_signal.emit(VoiceEvent)

VoiceSpeechRuntimeWorker(QThread)
  -> build_voice_speech_runtime(controller)
  -> runtime.run()
  -> event_signal.emit(VoiceEvent)
主线程 UI
  -> 根据 event 更新界面
```

GUI 行为：

1. 点击“唤醒机器人”调用 `controller.wake()`。
2. 输入文本点击“发送”调用 `controller.handle_text(text)`。
3. 显示分类结果。
4. 对 `text_delta` 做流式追加。
5. 对 `command_preview` 显示技能预览。
6. 对 `session_ended` 切回未唤醒状态。
7. 底部文本输入不依赖唤醒词，发送后直接进入 intent classifier 和 router。
8. `VOICE_INPUT_ENABLED=false` 时，不显示语音 session 区域，也不加载 ASR/KWS 依赖。
9. `VOICE_INPUT_ENABLED=true` 时，后台线程加载 FunASR/VAD、唤醒词模型和麦克风采集。
10. `VOICE_INPUT_ENABLED=true` 时，GUI 启动后先自动加载真实语音监听并等待唤醒词；Robot、底盘、串口等启动硬件会优先等待语音 runtime 初始化。
11. 首次运行 FunASR 可能下载模型。若超过 `VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S`，GUI 会先继续初始化 Robot，语音 runtime 仍在后台下载/加载。
12. GUI 收到 `asr_result` 后显示用户文本；收到 `done/audio_delta` 后显示/播放机器人回复。

## 17. 配置建议

新增配置：

```env
LLM_DEFAULT_PROVIDER=minicpm
CAMERA_PROVIDER=realsense
VISION_CAMERA_NAME=monitor1
VOICE_SESSION_TIMEOUT_S=30
VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S=30
VOICE_TTS_ENABLED=false
VOICE_INPUT_ENABLED=false
VOICE_AUDIO_SAMPLE_RATE=16000
VOICE_AUDIO_DEVICE=
VOICE_VAD_MODEL=fsmn-vad
VOICE_ASR_MODEL=iic/SenseVoiceSmall
VOICE_WAKE_ENGINE=sherpa
VOICE_KWS_KEYWORDS_FILE=models/kws/keywords.txt
```

含义：

- `LLM_DEFAULT_PROVIDER`：默认 LLM provider。具体 task 可通过 `TaskProfile.default_provider` 覆盖，单次调用也可传 `provider` 覆盖。
- `CAMERA_PROVIDER`：视觉问答使用的相机来源，复用 `src/cameras/` 支持的 `realsense` / `webcam`。
- `VISION_CAMERA_NAME`：视觉问答默认使用的相机名称或序列号；为空时使用所有在线相机。
- `VOICE_SESSION_TIMEOUT_S`：唤醒后无交互多久自动休眠。
- `VOICE_TTS_ENABLED`：是否在 `voice_stream` task 中请求模型生成语音回复。`classifier/planner` 等文本 task 不受该配置影响。
- `VOICE_INPUT_ENABLED`：是否启用真实语音输入；true 时同时启用唤醒词和 ASR，false 时二者都不加载。

手动调试配置：

```env
LLM_DEFAULT_PROVIDER=minicpm
CAMERA_PROVIDER=realsense
VISION_CAMERA_NAME=monitor1
VOICE_SESSION_TIMEOUT_S=30
VOICE_TTS_ENABLED=false
VOICE_INPUT_ENABLED=false
```

真实语音监听配置：

```env
VOICE_INPUT_ENABLED=true
VOICE_WAKE_ENGINE=sherpa
VOICE_KWS_KEYWORDS_FILE=models/kws/keywords.txt
```

## 18. 错误和取消

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

## 19. 测试方案

### 19.1 单元测试

覆盖：

- session wake / sleep / timeout。
- intent 路由。
- `session_control` 行为。
- `command` 规划失败。
- `vision_question` 无相机时错误返回。
- `chat` 流式事件映射。

### 19.2 Fake LLM 测试

使用 fake registry：

```python
class FakeClassifier:
    async def classify(self, text):
        return {"intent": "chat", "is_addressed_to_robot": True, ...}
```

验证 controller 不依赖真实模型。

### 19.3 GUI 手动测试

用输入框模拟：

- “你好” -> chat。
- “抓瓶子” -> command。
- “你看到什么” -> vision_question。
- “没事了” -> session_control/end_session。
- “取消刚才那个” -> execution_control/cancel。
- “停下” -> execution_control/cancel。

## 20. 推荐实施顺序

1. 新增 `src/voice_interaction/core/types.py`。
2. 新增 `src/voice_interaction/core/session.py`。
3. 新增 `src/voice_interaction/core/controller.py`。
4. 新增 `src/voice_interaction/core/router.py` 或先把 `_route()` 放在 controller 内。
5. GUI 增加手动唤醒和文本输入调试入口。
6. 接入 `InstructionClassifier` 路由。
7. 接入 `TaskRunner` 聊天流。
8. 接入 `SkillPlanner` 命令预览。
9. 接入 `VisionFusionTask`，相机先 mock。
10. 增加 session 超时和取消逻辑。
11. 按需在 GUI 或 WebSocket 服务中启动 `VoiceSpeechRuntime`。
12. 准备 `models/kws` 下的 sherpa-onnx 模型与关键词文件。
13. 最后做真实设备上的麦克风、VAD、ASR、TTS 联调。

## 21. 第一阶段验收标准

在无真实语音输入的情况下，通过 GUI 手动输入可以完成：

1. 点击唤醒后进入 awake 状态。
2. 超时后自动回到 sleeping 状态。
3. 输入“你好”能走 chat 并流式显示回复。
4. 输入“抓瓶子”能走 command 并生成技能规划或失败说明。
5. 输入“你看到什么”能走 vision_question。
6. 输入“没事了”能结束 session。
7. 输入“取消刚才那个”能触发 execution_control/cancel。
8. LLM 或相机不可用时 GUI 不崩溃，有明确错误事件。

## 22. 后续扩展

真实语音链路接入后仍只替换 IO 层：

- 唤醒词模型只负责调用 `controller.wake()`。
- ASR 只负责产出文本并调用 `controller.handle_text(text)`。
- TTS 播放器只消费 `LLMStreamEvent.audio_delta`。
- 主业务逻辑仍保持在 `VoiceInteractionController`。

这样可以避免语音链路和机器人行为决策互相耦合，也方便 GUI、WebSocket、测试脚本复用同一套会话编排逻辑。

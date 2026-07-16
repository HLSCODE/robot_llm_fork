# 统一执行入口重构需求文档

## 1. 背景

当前项目已经逐步将对话、意图识别、技能规划收敛到 `src/voice_interaction/`：

- GUI 底部输入框可以作为通用文本对话入口。
- 真实语音链路通过唤醒词、ASR、VAD 进入 `VoiceInteractionController`。
- WebSocket 远程文本调用也已经接入 `voice_interaction`。
- `command` 意图会先生成动作预览，再由用户确认执行。

但动作执行层仍然存在职责分散：

- `src/gui/execution.py` 中的 `ExecutionThread` 持有一套真实动作执行逻辑，依赖 Qt `QThread` 和 `pyqtSignal`。
- `src/robot_server/action_executor.py` 中的 `ActionExecutor` 持有另一套纯 Python 动作执行逻辑，当前被 WebSocket 服务使用。
- `src/ai_integration/execution_bridge.py` 仍通过 GUI 的 `ExecutionThread` 执行动作。
- `voice_interaction` 后续需要支持更多 `command`，但目前没有稳定、统一的执行控制接口。

这会导致同一种动作在 GUI、WebSocket、语音交互中可能走不同代码路径，后续扩展时容易出现行为不一致、修复遗漏和重复实现。

## 2. 重构目标

本次后续重构的核心目标是建立一个唯一的实际执行入口：

1. 将真实动作执行逻辑从 `gui` 和 `robot_server` 中抽离到中立模块。
2. GUI、WebSocket、`voice_interaction` 都通过同一个执行入口启动、暂停、恢复、停止动作。
3. GUI 只负责把执行事件转换为界面状态和 Qt signal。
4. WebSocket 只负责把执行事件转换为 WebSocket event。
5. `voice_interaction` 只负责理解用户意图、生成动作预览或控制命令，不直接操作硬件细节。
6. 后续新增 command 时，可以统一落到同一个 command runtime / execution runtime 上。

## 3. 非目标

1. 不在 `voice_interaction` 中直接实现机械臂、底盘、继电器、吸液枪等硬件执行逻辑。
2. 不让 WebSocket 成为执行层所有者，WebSocket 只是一种远程调用入口。
3. 不让 GUI 的 `QThread` 继续承载业务执行逻辑。
4. 不改变 `skill_system` 的职责，技能系统仍只负责技能查询、参数校验和展开动作序列。
5. 不在第一阶段重写所有动作实现；优先迁移和收敛现有 `ActionExecutor` / `ExecutionThread` 中已经存在的逻辑。

## 4. 当前问题

### 4.1 执行逻辑重复

`ExecutionThread` 和 `ActionExecutor` 都包含：

- 序列执行主循环。
- `LoopBlock` 展开和循环进度。
- stop / pause / resume 控制。
- 动作状态更新。
- 各类 `ActionType` 的执行分发。
- 机械臂、底盘、身体、夹爪、吸液枪、换枪、视觉抓取等具体动作执行。

这种重复会导致后续维护成本升高。

### 4.2 行为可能漂移

例如某个新动作只加到 `ActionExecutor`，WebSocket 可用，但 GUI 不可用；或者只加到 `ExecutionThread`，GUI 可用，但远程控制不可用。

当前已经可以看到一些差异：

- `ActionExecutor` 已经包含表情屏相关执行逻辑。
- `ExecutionThread` 仍有自己的硬件执行分发逻辑。

后续如果语音 command 增多，这种漂移会更明显。

### 4.3 `voice_interaction` 缺少稳定执行接口

当前 `voice_interaction` 的 `command` 主要生成动作预览，真正执行依赖 GUI 或 WebSocket 各自处理。

后续需要支持：

- “执行刚才的动作”
- “取消当前任务”
- “暂停一下”
- “继续”
- “停下”
- “回到安全位置”
- “切换表情”
- “初始化设备”

这些都需要一个统一的程序控制和动作执行接口。

## 5. 推荐目标架构

新增中立执行模块：

```text
src/execution/
  __init__.py
  context.py
  events.py
  executor.py
  controller.py
  command_runtime.py
```

### 5.1 `src/execution/context.py`

定义运行时依赖容器，集中持有硬件控制器：

```python
@dataclass
class ExecutionContext:
    robot_controller: RobotController | None = None
    body_controller: ModbusMotor | None = None
    neck_controller: PWMNeckController | None = None
    move_controller: RobotMoveController | None = None
```

后续如果新增相机、表情屏、扬声器、机械手等控制器，也从这里统一注入。

### 5.2 `src/execution/events.py`

定义执行事件，替代 GUI signal 和 WebSocket event 直接耦合执行逻辑：

```python
ExecutionEventType = Literal[
    "started",
    "step_started",
    "step_completed",
    "step_failed",
    "loop_progress",
    "log",
    "paused",
    "resumed",
    "stopped",
    "finished",
]

@dataclass
class ExecutionEvent:
    type: ExecutionEventType
    index: int | None = None
    item: SequenceItem | None = None
    message: str = ""
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)
```

执行层只产出统一事件，不关心事件最终发给 GUI、WebSocket 还是日志系统。

### 5.3 `src/execution/executor.py`

放置唯一真实动作执行器。

建议以当前 `src/robot_server/action_executor.py` 为基础迁移，因为它已经是纯 Python、无 Qt 依赖。

职责：

- 执行动作序列。
- 展开 `LoopBlock`。
- 维护 running / paused / stop 状态。
- 分发 `ActionType` 到具体动作执行方法。
- 通过 callback 或事件队列产出 `ExecutionEvent`。

不应包含：

- Qt signal。
- WebSocket broadcast。
- GUI 控件更新。
- LLM / voice_interaction 逻辑。

### 5.4 `src/execution/controller.py`

封装执行器生命周期，对外提供稳定控制接口：

```python
class ExecutionController:
    def execute(self, sequence: list[SequenceEntry]) -> bool: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def is_running(self) -> bool: ...
    def is_paused(self) -> bool: ...
```

`ExecutionController` 是 GUI、WebSocket、`voice_interaction` 共同依赖的执行入口。

### 5.5 `src/execution/command_runtime.py`

用于承接非技能序列类 command，例如：

- 停止当前执行。
- 暂停当前执行。
- 恢复执行。
- 取消当前预览。
- 清空当前任务。
- 初始化设备。
- 切换表情。
- 查询执行状态。

接口示例：

```python
class CommandRuntime:
    def execute_sequence(self, sequence: list[SequenceEntry]) -> bool: ...
    def cancel_current_task(self) -> None: ...
    def pause_current_task(self) -> None: ...
    def resume_current_task(self) -> None: ...
    def stop_current_task(self) -> None: ...
    def get_status(self) -> dict: ...
```

`voice_interaction` 后续可以依赖这个抽象，而不是依赖 GUI 或 WebSocket。

## 6. 重构后的职责边界

### 6.1 `src/execution/`

负责唯一实际执行入口：

- 动作序列执行。
- 执行状态。
- 暂停、恢复、停止。
- 执行事件。
- 硬件控制器调用。
- command runtime 基础控制能力。

### 6.2 `src/gui/`

负责界面展示和用户操作：

- 将按钮操作转发给 `ExecutionController`。
- 将 `ExecutionEvent` 转换成 Qt signal 或直接更新控件。
- 管理执行列表 UI 状态。
- 展示日志、错误弹窗、按钮状态。

GUI 不再拥有真实动作执行逻辑。

### 6.3 `src/robot_server/`

负责远程调用协议：

- 接收 WebSocket action。
- 将 execute / stop / pause / resume 调用转发给 `ExecutionController`。
- 将 `ExecutionEvent` 转换成 WebSocket event。
- 保持远程调用能力，不拥有动作执行实现。

`src/robot_server/action_executor.py` 最终应删除或变成对 `src/execution/` 的薄导入过渡文件。若项目不需要兼容旧路径，迁移完成后直接删除。

### 6.4 `src/voice_interaction/`

负责对话和意图入口：

- 识别用户意图。
- 根据 intent 调用聊天、视觉、技能规划、会话控制等 task。
- 对 command 生成动作预览。
- 对 session control 调用 `CommandRuntime`。

`voice_interaction` 不直接持有硬件控制器，也不直接执行 `ActionType`。

### 6.5 `src/ai_integration/`

负责 GUI 中的共享上下文和状态桥接：

- 持有 `LLMRegistry`。
- 持有 `SkillEngine`。
- 保存当前动作预览。
- 通过统一执行入口执行已确认的预览。

后续可逐步弱化 `ExecutionBridge`，让它只做 Qt signal adapter，或者完全由 GUI adapter 替代。

## 7. 目标调用链

### 7.1 GUI 手动执行动作

```text
GUI button
  -> ExecutionController.execute(sequence)
  -> SequenceExecutor
  -> ExecutionEvent
  -> GuiExecutionAdapter
  -> 更新列表、日志、按钮状态
```

### 7.2 WebSocket 远程执行

```text
WebSocket action: execute
  -> ExecutionController.execute(sequence)
  -> SequenceExecutor
  -> ExecutionEvent
  -> WebSocketExecutionAdapter
  -> step_started / step_completed / execution_finished
```

### 7.3 语音或文本 command

```text
用户输入
  -> VoiceInteractionController.handle_text()
  -> InstructionClassifier
  -> command
  -> SkillPlanner
  -> SkillEngine.parse_and_expand()
  -> command_preview event
  -> 用户确认
  -> CommandRuntime.execute_sequence(sequence)
  -> ExecutionController.execute(sequence)
```

### 7.4 会话控制 command

```text
用户说“暂停一下”
  -> VoiceInteractionController
  -> session_control / command
  -> CommandRuntime.pause_current_task()
  -> ExecutionController.pause()
```

## 8. 关键接口要求

### 8.1 执行入口必须支持异步启动

动作执行不能阻塞 GUI 主线程，也不能阻塞 WebSocket event loop。

推荐实现：

- 执行器内部继续使用后台线程。
- 对外提供非阻塞 `execute()`。
- 通过 callback 或线程安全队列返回事件。

### 8.2 执行事件必须线程安全

GUI 和 WebSocket 的事件消费方式不同：

- GUI 需要切回 Qt 主线程。
- WebSocket 需要切回 asyncio event loop。

因此执行器不能直接调用 UI 控件，也不能直接调用 `websocket.send()`。

### 8.3 stop / pause / resume 必须统一

所有入口调用同一套方法：

- GUI 暂停按钮。
- WebSocket `pause` action。
- 语音“暂停一下”。
- 文本“停下”。

这些不应该各自实现一套状态逻辑。

### 8.4 执行状态必须可查询

统一提供状态：

```python
{
    "running": bool,
    "paused": bool,
    "current_index": int | None,
    "current_action": str | None,
    "last_error": str | None,
}
```

GUI 状态栏、WebSocket `status`、语音反馈都应使用同一份状态。

## 9. 迁移方案

### 阶段 1：新增中立执行模块

1. 新增 `src/execution/`。
2. 将 `src/robot_server/action_executor.py` 的核心逻辑迁移到 `src/execution/executor.py`。
3. 新增 `ExecutionContext`、`ExecutionEvent`、`ExecutionController`。
4. 保持 WebSocket 现有行为不变，只调整 import 和实例化位置。

验收：

- WebSocket `execute` / `stop` / `pause` / `resume` 行为不变。
- `python -m compileall src` 通过。

### 阶段 2：WebSocket 改用统一执行入口

1. `ws_server.py` 不再从 `src.robot_server.action_executor` 导入执行器。
2. `ws_server.py` 创建 `ExecutionContext` 和 `ExecutionController`。
3. WebSocket 事件回调从 `ExecutionEvent` 映射。
4. 删除 `src/robot_server/action_executor.py`，或仅保留临时薄导入；若不需要兼容旧代码，迁移后删除。

验收：

- WebSocket 协议事件不破坏现有前端。
- AI 生成的动作预览确认后仍可执行。
- `session_control.cancel_task` 能停止当前执行。

### 阶段 3：GUI 改用统一执行入口

1. 将 `src/gui/execution.py` 中真实动作执行逻辑移除。
2. 保留或新增 `GuiExecutionAdapter`，把 `ExecutionEvent` 转为 Qt signal。
3. `MainWindow._start_sequence_execution()` 改为调用统一 `ExecutionController`。
4. `toggle_pause()`、`stop_execution()` 改为调用统一控制接口。

验收：

- GUI 执行动作、组合任务、循环块显示行为不变。
- GUI 暂停、继续、停止行为不变。
- 执行日志仍正常显示。
- 执行失败仍能弹窗提示。

### 阶段 4：AIController / ExecutionBridge 收敛

1. `ExecutionBridge` 不再创建 `ExecutionThread`。
2. `ExecutionBridge` 改为封装 `ExecutionController`，或删除并由 GUI adapter 替代。
3. `AIController.confirm_and_execute()` 通过统一执行入口执行预览。

验收：

- GUI 中由 `voice_interaction` 生成的 command preview 确认后仍可执行。
- 模拟模式如仍需要，应在统一执行入口中提供 simulation executor 或 adapter。

### 阶段 5：voice_interaction 接入 CommandRuntime

1. 为 `VoiceInteractionController` 注入 `CommandRuntime` 或 `InteractionBridge`。
2. `session_control.cancel_task` 使用统一取消方法。
3. 新增 command 行为时，不直接依赖 GUI 或 WebSocket。
4. 支持更多自然语言控制：
   - 暂停当前任务。
   - 继续当前任务。
   - 停止当前任务。
   - 取消当前预览。
   - 执行当前预览。
   - 查询当前状态。

验收：

- GUI 文本输入、真实语音输入、WebSocket 远程文本输入产生一致行为。
- 同一句“暂停一下”不因入口不同而走不同代码。

## 10. 建议文件变更

目标新增：

```text
src/execution/
  __init__.py
  context.py
  events.py
  executor.py
  controller.py
  command_runtime.py
```

目标调整：

```text
src/gui/execution.py
```

改为 GUI adapter，或迁移完成后删除。

```text
src/robot_server/action_executor.py
```

迁移完成后删除。

```text
src/ai_integration/execution_bridge.py
```

改为统一执行入口的 Qt bridge，或逐步删除。

```text
src/voice_interaction/core/router.py
```

后续 command / session_control 通过 `CommandRuntime` 处理执行控制。

```text
src/robot_server/ws_server.py
```

WebSocket 的 execute / ai_confirm / stop / pause / resume 统一转发到 `ExecutionController`。

## 11. 兼容性要求

对外行为应保持稳定：

- GUI 操作流程不变。
- WebSocket action 名称不变。
- `execute`、`stop`、`pause`、`resume`、`ai_confirm` 事件语义不变。
- `voice_interaction` 的 command preview 格式不变。

如果不需要保留旧代码兼容，内部旧文件可以在迁移完成后删除，但对 GUI 和 WebSocket 用户可见协议应尽量稳定。

## 12. 风险与注意事项

### 12.1 Qt 线程切换

统一执行器是纯 Python 线程时，不能直接更新 Qt 控件。

GUI adapter 必须使用 Qt signal 或 `QMetaObject.invokeMethod` 等方式回到主线程。

### 12.2 asyncio 线程切换

WebSocket 服务需要从执行线程安全地投递事件到 asyncio loop。

现有 `_broadcast_threadsafe()` 思路可以保留，但应只存在于 WebSocket adapter。

### 12.3 停止动作的实时性

有些硬件调用本身是阻塞的，例如机械臂移动、串口通信、轨迹执行。统一 stop 状态只能在动作实现检查 stop flag 时生效。

迁移时应保留现有动作中的 stop 检查，并逐步补齐长耗时动作的 stop / pause 检查。

### 12.4 动作实现中的 GUI 依赖

当前某些执行逻辑可能引用 `src.gui` 下的工具，例如定位补偿的 UDP 数据读取。

迁移时应识别这些依赖，将纯运行时能力挪到中立模块，避免 `src/execution/` 反向依赖 GUI。

### 12.5 模拟模式

GUI 当前 AI 执行链路中存在 simulation mode。统一执行入口需要明确：

- 模拟模式是 `ExecutionController` 的配置。
- 或者提供独立 `SimulationExecutor`。

不要让模拟逻辑只存在于 `AIController`。

## 13. 验收标准

### 13.1 代码结构

- `src/execution/` 成为唯一真实动作执行模块。
- GUI 和 WebSocket 不再各自维护动作执行实现。
- `voice_interaction` 不直接调用硬件控制器。
- 旧的 `ExecutionThread` 执行业务逻辑被移除或仅保留 adapter。
- 旧的 `robot_server/action_executor.py` 被删除或不再持有实际实现。

### 13.2 功能行为

- GUI 可执行普通动作序列。
- GUI 可执行包含 `LoopBlock` 的序列。
- GUI 可暂停、继续、停止。
- WebSocket 可执行、暂停、继续、停止。
- WebSocket `ai_confirm` 可执行 AI 预览序列。
- 语音/文本 `cancel_task` 可取消当前预览或停止当前执行。
- 执行日志、步骤状态、失败状态正常展示。

### 13.3 一致性

- 同一动作从 GUI、WebSocket、语音 command 触发时走同一套执行代码。
- 新增 `ActionType` 时只需要在统一执行器中注册一次。
- 新增 command 控制行为时只需要在 `CommandRuntime` 中注册一次。

### 13.4 验证命令

基础验证：

```powershell
python -m compileall src
```

建议人工验证：

1. GUI 执行单个动作。
2. GUI 执行循环块动作。
3. GUI 暂停、继续、停止。
4. WebSocket 执行同一动作序列。
5. WebSocket `ai_chat` 生成预览后 `ai_confirm`。
6. 文本输入“取消当前任务”。
7. 文本输入“暂停一下”“继续”。

## 14. 推荐实施顺序

优先顺序：

1. 先迁移 `ActionExecutor` 到 `src/execution/executor.py`。
2. 让 WebSocket 改用新路径，保持远程执行稳定。
3. 再迁移 GUI `ExecutionThread`，因为 GUI 涉及 Qt signal 和 UI 状态，风险更高。
4. 最后让 `voice_interaction` 的 session control 和 command runtime 依赖统一执行入口。

这样可以先稳定纯 Python 执行层，再处理 GUI 适配，降低一次性改动风险。

## 15. 结论

本重构是必要的。

项目已经把“大模型调用”收敛到 `src/llm/`，把“对话和意图入口”收敛到 `src/voice_interaction/`。下一步应该把“机器人动作执行”也收敛到 `src/execution/`。

最终目标是形成三层稳定边界：

```text
llm                 负责模型能力
voice_interaction   负责对话、意图、任务路由
execution           负责唯一真实执行入口
```

GUI 和 WebSocket 都只是入口和展示层，不再拥有动作执行逻辑。这样后续扩展更多语音 command、远程调用能力和机器人控制能力时，代码会更清晰，也更不容易出现多入口行为不一致。

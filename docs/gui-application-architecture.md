# GUI 应用架构

> 文档类型：Current Architecture  
> 最近更新：2026-08-03  
> 状态：Active

## 1. 目标与边界

GUI 是应用的表现层，不拥有设备、执行线程、任务持久化或业务状态机。
所有可被 WebSocket、HTTP、语音或其他入口复用的能力必须位于应用层；Qt
组件只负责采集用户输入、显示不可变快照和把跨线程事件送回 GUI 线程。

```text
MainWindow / Dialogs
        |
ActionLibraryView / WorkflowEditorView / DeviceStatusView / DeviceControlView
        | commands       ^ immutable view state / Qt signals
        v                |
TaskComposerService   DeviceViewModel   ExecutionViewModel
        |                    |                 |
CompositionService   DeviceManagement   ExecutionService
        |                    |                 |
 JSON repository       DeviceRuntime     ExecutionManager

ExecutionBridge: ExecutionManager event -> Qt signal
CompositionBridge: CompositionService event -> Qt signal
GuiNotificationCenter: operational message -> history/log/status/modal
```

## 2. 当前职责

### 2.1 MainWindow

- 组合窗口、面板、对话框和 Qt 信号。
- 将点击、拖放和表单结果转换为应用服务调用。
- 渲染 service/view-model 返回的状态。
- 通过 `GuiNotificationCenter` 发布操作状态和用户可见错误。
- 不保存设备连接或暂停状态的平行布尔值。
- 不创建设备实例，不创建序列执行 worker，不直接访问 JSON 仓储。

### 2.2 稳定视图域

- `ActionLibraryView` 负责动作分类页、已保存任务列表和 AI Assistant 的视图组合，
  仅通过 create/edit/delete/camera-test/task-add 意图信号与窗口协作。
- `WorkflowEditorView` 负责动作序列、任务组合器及其控制区；拖放事件在组件内解析，
  执行按钮状态通过单一 `render_execution_controls()` 接口更新。
- `DeviceStatusView` 负责设备状态灯、机械臂位姿和定位文本，只接收
  `DeviceViewState` 与格式化后的展示文本。
- `DeviceControlView` 负责夹爪、继电器和移液枪手动操作入口，只发出参数化意图信号，
  不持有设备实例或调用 Application Service。
- `MainWindow` 直接持有上述组件，不再提供旧按钮、列表和状态标签属性别名。

### 2.3 TaskComposerService

- 独占尚未持久化的任务/动作组合草稿。
- 提供插入、删除、移动、连续区块循环和清空操作。
- 通过 `CompositionService` 解析任务引用，并为每次执行生成全新的
  `SequenceItem` 与 UUID。
- 对外返回防御性副本，QListWidget 不作为业务状态存储。

持久化动作、任务与共享动作序列仍由 `CompositionService` 独占，两个服务
职责不重叠。

### 2.4 DeviceViewModel

- 只读取 `DeviceManagementService.status()`。
- 将运行时设备快照投影为 GUI 需要的 robot/body/pipette/relay ready 状态。
- 按钮是否可用和状态灯颜色均由该快照派生。
- 两只机械臂当前属于同一个 `ROBOT_SYSTEM` 设备能力，因此共享 ready 状态；
  后续若 provider 暴露逐臂状态，应先扩展设备领域快照，再扩展 ViewModel。

### 2.5 ExecutionViewModel 与 ExecutionBridge

`ExecutionViewModel` 读取唯一的 `ExecutionService` 状态机，并派生：

- 是否处于 active 状态；
- 是否允许 pause、resume、cancel；
- 暂停按钮文案。

pause/resume/cancel 直接进入 `ExecutionService`，窗口不维护 `is_paused`。

`ExecutionBridge` 是 Qt 边界：序列提交时注册执行 listener，将后台事件转换为
Qt signals；安全停止使用短生命周期 I/O 调度线程，避免阻塞主线程。它不再
提供第二套 pause/resume/cancel 或执行状态查询 API。

### 2.6 GuiNotificationCenter

- 使用 `GuiNotificationLevel` 和不可变 `GuiNotification` 表达通知。
- 同一发布动作统一写入有界历史、运行日志和状态栏。
- 模态展示与确认通过 `NotificationDialogPresenter` 边界实现，测试可注入 fake，
  MainWindow 不直接调用 `QMessageBox`。
- 后台执行日志通过 Qt signal 进入通知中心，通知顺序允许反映真实并发事件；
  调用方读取历史而不是依赖某条消息永久保持为 latest。

### 2.7 SchemaActionForm

`ActionConfigDialog` 从 `src/domain/action_schema.py` 获取唯一 schema，并通用支持：

- `text`、`number`、`select`、`boolean`、`object` 字段；
- action variant 动态切换；
- label、unit、placeholder、default、required、readonly；
- 数值上下限和枚举选项；
- object 字段 JSON 边界解析；
- 提交前统一 `validate_action_parameters()` 校验与归一化。

新增动作或参数时不得在 GUI 增加按 `ActionType` 的初始化/构建分派。应先修改
canonical schema；只有文件选择器、设备发现等纯交互增强才可按 schema 元数据
注册可复用 widget factory，不能复制业务字段定义或校验规则。

## 3. 状态所有权

| 状态 | 唯一所有者 | GUI 获取方式 |
|---|---|---|
| 设备实例与生命周期 | `DeviceRuntime` | `DeviceManagementService` → `DeviceViewModel` |
| 序列执行状态 | `ExecutionManager` | `ExecutionService` → `ExecutionViewModel` / `ExecutionBridge` |
| 动作、任务、共享序列 | `CompositionService` | service 快照 + `CompositionBridge` |
| 临时任务组合草稿 | `TaskComposerService` | service 快照 |
| GUI 启动阶段 | `GuiStartupLifecycle` | `GuiStartupState` |
| 动作参数定义 | canonical action schema | `SchemaActionForm` |

## 4. 扩展规则

新增同功能不同协议设备时，只增加或注册 `DeviceRuntime` adapter/provider，并运行
相同 capability contract；GUI 和应用服务不得导入厂商 SDK。

新增动作时：

1. 在 `ActionType` 和 canonical action schema 定义类型与参数。
2. 注册 action handler 及控制策略。
3. 增加 schema、handler 和协议契约测试。
4. GUI 自动生成表单，不新增平行字段映射。

新增 HTTP 等入口时，复用同一 `ApplicationServices`，不得创建第二份 runtime、
设备连接、执行 manager 或任务仓储。

## 5. 组件协作与关闭

AI Assistant 不获取 MainWindow 对象。它通过以下窄信号协作：

- `welcome_task_execution_requested`；
- `sequence_visualization_requested`；
- step、loop 和 execution terminal 展示事件。

关闭分为两个阶段：

1. `closeEvent` 请求执行取消、相机线程中断和交互 worker 停止，关闭窗口时不等待。
2. Qt 事件循环退出后，launcher 调用 `shutdown_after_event_loop()`；相机和交互线程
   使用显式超时等待，然后附加服务、定位服务和 DeviceRuntime 按宿主顺序关闭。

线程未在期限内退出会记录明确错误，不会被静默吞掉。

## 6. 当前遗留项

- `MainWindow` 仍承担动作/任务列表内容转换和序列树局部展示协调；后续只有在这些
  展示规则形成稳定复用边界时再下沉，业务草稿和执行状态仍必须由应用服务独占。
- ActionConfigDialog 内部的表单校验提示仍属于对话框局部交互；如后续需要统一
  非模态体验，应通过注入 presenter 实现，不得引入全局 UI 单例。
- 真实设备的逐项验收、RealMan 停止延迟和恢复条件仍按总计划执行。

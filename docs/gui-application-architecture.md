# GUI 应用架构

> 文档类型：Current Architecture + Approved Target
> 最近更新：2026-08-07
> 状态：Active

## 1. 目标与边界

GUI 是应用的表现层，不拥有设备、执行线程、任务持久化或业务状态机。
所有可被 WebSocket、HTTP、语音或其他入口复用的能力必须位于应用层；Qt
组件只负责采集用户输入、显示不可变快照和把跨线程事件送回 GUI 线程。

当前实现（D-031 完成后）：

```text
MainWindow / Dialogs
        |
WorkbenchView
  | Activity Bar / Side Bar / Editor / Status Bar
  |                           \ Floating Detail Panel
  |
TaskLibraryView / ActionLibraryView / AIAssistantWidget
WorkflowEditorView / DeviceHealthView / DevicePoseView / DeviceControlView
        | commands       ^ immutable view state / Qt signals
        v                |
WorkflowEditingSession DeviceViewModel  ExecutionViewModel
        |                    |                 |
CompositionService   DeviceManagement  ExecutionService
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
- 将唯一 `WorkbenchView` 设为中央控件；MainWindow 不再拥有 Splitter 尺寸、
  Side Bar 或详情浮层的当前页与展开状态。

### 2.2 稳定视图域

- `WorkbenchView` 只拥有 Activity Bar、Side Bar、Editor、Status Bar 和右下锚定详情
  浮层的表现状态。Side Bar 二次点击收起并继续使用视觉 1 px、实际命中 7 px 的
  水平 `QSplitter`；原画布/Bottom Panel 垂直 Splitter 已删除，详情不再改变画布尺寸。
- `WorkbenchLayoutState` 是 schema v2 不可变布局偏好；组合根注入
  `QSettingsWorkbenchLayoutStore`，保存 `side_page`、`side_visible`、`side_width`、
  `panel_page` 和 `panel_visible`。v2 直接替代 v1，不提供 v1 兼容读取；未知版本、字段、
  类型、范围或已删除页面会清理损坏值并恢复默认布局，“视图 → 恢复默认布局”提供
  显式恢复入口。
- Activity Bar、Status Bar、Task/Action/Workflow 面板工具栏图标统一使用 `IconName`
  和编译后的 Qt Resource 单色 SVG；图标随 Palette 变化重绘并覆盖 1x/2x/3x，
  不从文件系统动态查找资源。所有 icon-only 命令都有 Tooltip 和可访问名称。
- `TaskLibraryView` 只展示 `CompositionService` 的已保存任务投影，并发出打开或作为
  Subworkflow 插入当前文档的意图；`ActionLibraryView` 只展示按类型分类的基础动作并发出增删改、插入和相机测试意图。
- `AIAssistantWidget` 是独立资源页，继续复用唯一 LLM/CommandRuntime，不嵌入动作库，
  也不反向持有 MainWindow。
- `WorkflowEditorView` 只负责动作序列画布及其控制区；执行按钮状态通过单一
  `render_execution_controls()` 接口更新。编辑、执行、适合内容和 100% 缩放命令
  已收敛为面板顶部 icon-only 工具栏：普通编辑命令使用 32 px 命中区，停止任务、
  快速停止和设备急停保持 44 px 命中区与既有语义色，并始终常驻可见。该视图不再
  包含任务组合 Tab 或第二套暂停/停止控件。
- `DeviceHealthView` 负责设备状态灯，只接收 `DeviceViewState`；`DevicePoseView`
  负责机械臂位姿、外部定位、刷新和复制意图。两者作为独立详情页在状态栏浮层中
  按需显示。
- `DeviceControlView` 负责夹爪、继电器和移液枪手动操作入口，只发出参数化意图信号，
  不持有设备实例或调用 Application Service，并作为状态栏详情浮层页面按需显示。
- `LogWidget` 作为状态栏详情浮层页面，不再用固定高度或 Bottom Panel 常驻挤压画布；
  Workbench Status Bar 保持设备摘要和通知出口，详细信息由对应页面展示。
- 同一个状态栏按钮再次点击、浮层关闭按钮或 Escape 都会关闭浮层；窗口 resize 时
  浮层重新锚定到右下角，窄窗口下限制在可用区域内且不越界。浮层非模态，不阻塞
  画布编辑和安全命令。
- `MainWindow` 直接持有上述组件，不再提供旧按钮、列表和状态标签属性别名。

### 2.3 WorkflowEditingSession

- 独占当前 `WorkflowDocument`、存储文件名、revision、dirty 和草稿边界。
- 打开任务会替换当前文档；插入任务会创建自包含 Subworkflow 快照并递归重建身份。
- 画布在任意子流程作用域修改后发布完整根文档快照，Undo/Redo 也以整棵文档为边界。
- 保存提交完整文档与 expected revision；执行只编译当前会话快照。
- `CompositionService` 继续负责动作/任务目录和 Repository 用例，不再保存 GUI 组合草稿。

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

### 2.8 当前组合编辑架构（D-028～D-030 已完成）

```text
TaskLibraryView / ActionLibraryView / AIAssistantWidget
                    | open / insert / generate intents
                    v
             WorkflowEditingSession
      document / name / revision / dirty / draft / commands
                    |
          immutable WorkflowDocument v4 snapshot
          /                    |                  \
WorkflowEditorView     CompositionService     WorkflowCompiler
Canvas + Breadcrumb      JSON repository       ExecutionPlan
```

- 原 `TaskComposerView`、`TaskComposerListWidget`、`TaskComposerService` 和 Activity Bar
  组合入口删除；可选 WorkflowOutlineView 只能投影当前文档，不能保存第二份编辑状态。
- WorkflowDocument v4 增加递归 `SubworkflowBlock`。它保存名称、可选来源 workflow
  ID/revision 和自包含 body；默认是内嵌快照，不是随源文件变化的实时引用。
- 根任务画布显示折叠 Subworkflow 卡片；双击进入 body，面包屑负责多层作用域导航。
  内部 Action/Loop/Parallel/Subworkflow 使用同一编辑命令和全局 Undo/Redo。
- 任务库“双击”打开独立源任务；拖入画布或“插入到当前任务”创建内嵌副本。复制边界
  必须递归重建节点 UUID、容器 UUID 和 branch ID，同时保留来源元数据。
- 修改内嵌动作只影响当前父任务；修改基础动作或源任务必须走单独明确命令。若以后需要
  更新来源，只允许显式替换并确认，禁止后台隐式传播。
- WorkflowCompiler 递归透明编译 Subworkflow body，继续输出唯一 ExecutionPlan；GUI、
  Subworkflow 和 WorkflowEditingSession 均不创建执行 worker 或动作 Handler。
- 保存提交完整 WorkflowDocument 和 expected revision，执行编译当前会话快照；禁止通过
  `flattened_task()` 创建组合执行输入。

### 2.9 当前工作台交互架构（D-031 已完成）

- Task/Action 的高频命令由 `PaneHeader` 承载，Workflow 编辑/执行命令由顶部命令栏
  承载；二者统一使用 `IconToolButton`、Qt Resource SVG、Tooltip 和可访问名称，
  不再在页面底部重复占用整行按钮。
- 节点拖动超过阈值后创建轻量 ghost 缩略图并跟随指针，原节点位置保留占位，避免拖动
  过程中整个主序列跳动。ghost 只表达本次交互，不进入 WorkflowDocument。
- 画布以指针与“+”中心的二维距离解析最近合法主序列插入点；只有进入激活半径的目标
  才使用主题化发光、克制的循环脉冲和位置标签提供插入预览。没有目标进入脉冲状态时，
  释放节点只恢复原位，不按单独的纵坐标隐式重排。
- 节点重排与 Action/Task 外部拖入在提交时重新使用同一个二维目标解析器，避免陈旧
  active index 或“提示顶层、实际进入 Loop/Parallel”的分叉；未命中顶层插入点的
  Action 才继续解析复合容器落点。
- Escape、窗口失焦、鼠标抓取丢失、离开画布或完成都会进入同一个幂等清理路径，移除
  ghost、恢复原节点透明度并停止目标动画；有效放置只提交一次 `UndoCommand`，没有
  第二份临时文档或中间持久化状态。
- 顶部菜单保留在原生标题栏下方的客户区。没有为追求与程序 icon 同行而接管 Windows/
  Linux/macOS 非客户区，因为那会引入窗口拖动、缩放、最大化、DPI、系统菜单和无障碍
  行为的跨平台维护风险；这是批准的实现取舍，不是待修复缺陷。

## 3. 状态所有权

| 状态 | 唯一所有者 | GUI 获取方式 |
|---|---|---|
| 设备实例与生命周期 | `DeviceRuntime` | `DeviceManagementService` → `DeviceViewModel` |
| 序列执行状态 | `ExecutionManager` | `ExecutionService` → `ExecutionViewModel` / `ExecutionBridge` |
| 动作、任务目录与外部共享序列 | `CompositionService` | service 快照 + `CompositionBridge` |
| 当前编辑文档 | `WorkflowEditingSession` | 不可变 WorkflowDocument 快照 + 窄事件 |
| GUI 启动阶段 | `GuiStartupLifecycle` | `GuiStartupState` |
| 动作参数定义 | canonical action schema | `SchemaActionForm` |
| Workbench 布局偏好 | `QSettingsWorkbenchLayoutStore` | schema v2 `WorkbenchLayoutState`（`panel_page` / `panel_visible`） |

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
- GUI 工作台 D-031 已完成；后续视觉调整不得重新引入 Unicode/Emoji 导航图标、裸资源
  相对路径、Bottom Panel 垂直 Splitter 或第二套布局状态源。
- Action 从资源库直接拖入 Loop/Parallel 时，当前会正确提交到容器且不会显示错误的
  顶层提示；容器内部插入点的专用脉冲动画可作为后续独立视觉增强，不得另建 drop 状态源。

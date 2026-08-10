# 机器人动作编排 GUI 重构计划

> 适用场景：竖屏、窄窗口、触控操作、机器人动作编排与任务执行  
> 推荐技术栈：Python + PySide6 + QGraphicsView/QGraphicsScene  
> 文档版本：V2.4
> 文档性质：目标架构、实施记录与后续验收约束
> 最近更新：2026-08-10

> 实施进度：D-015～D-032 已完成。纯 WorkflowDocument、版本化 `.workflow`
> 持久化与草稿恢复、Validator/Preflight/Compiler、稳定节点映射、受约束
> QGraphics 画布、Loop 容器、Undo/Redo、触控导航和现有功能等价接入已经
> 落地。系统 Palette、集中设计令牌、44 px 触控目标、可访问性、Qt
> offscreen 尺寸/主题矩阵和 100/500 节点性能预算均已建立；旧列表编辑器及
> 旧路径已经直接删除，不保留兼容层。真实触控屏视觉和硬件安全仍按设备验收执行。

> 2026-08-06 M6 交互复核：节点间及循环体内“+”均已接入真实插入命令；
> Loop 改为头部、展开子动作、完成节点及双侧回路的结构化表达；画布采用常规
> 桌面编辑器手势。M6 的箭头抽屉属于已实现历史状态，将由 M7 Activity Bar
> 与固定细线 Side Bar 分隔方案直接替换，不保留兼容入口。
> 节点支持带阈值的纵向拖动排序并在松开后自动吸附；单击仅改变选择状态，
> 不再展开常驻参数摘要，参数编辑统一由双击、右键菜单或“修改”命令打开。

> 2026-08-06 工作台评审：下一阶段采用 VS Code 式信息架构，将界面收敛为
> 顶部菜单、左侧 Activity Bar、可调整资源侧栏、中央画布、可调整底部面板和
> 常驻状态栏。该方案只重组表现层，不改变应用服务、设备运行时或唯一执行链。

> M7 第一批已完成：唯一 Workbench 壳层、Activity Bar、固定细线可拖动 Side Bar、
> 非模态 Bottom Panel、Status Bar 与紧凑常驻安全命令已经切换；旧箭头抽屉和
> 设备/位姿/基础控制/日志常驻布局已删除。
>
> M7 第二批的任务组合资源页属于过渡实现；M8 已将其页面、服务和 Activity
> 入口直接删除。当前 Side Bar 只保留已保存任务、分类基础动作和 AI 助手，
> 中央区域只有唯一工作流画布与执行控制区。
>
> M7 第三批已完成：项目自制 CC0 单色 SVG 已编译进入 Qt Resource，Activity Bar
> 与 Status Bar 按 Palette 和 1x/2x/3x 渲染；主要命令区已删除 Emoji 装饰。
> Side Bar/Bottom Panel 的页面、可见性和尺寸使用 schema v1 QSettings 偏好持久化，
> 损坏值和已删除页面会自动恢复默认，“视图”菜单提供显式恢复入口。
>
> M7 视觉复核：Activity Bar 使用 350 ms 延迟的紧凑自绘 Tooltip，圆角、前景、
> 背景和弱边框均跟随浅色/深色 Palette；全局视觉从“容器描边”改为“背景层级”，
> 已删除普通按钮、Tab Pane/Tab、GroupBox、列表选中项、StyledPanel、菜单栏、
> Activity Bar 和画布外框的重复线条。仅输入焦点、可拖动分隔、工作流节点边界及
> 安全/状态语义保留必要描边。

> 2026-08-07 M8 数据模型切换已完成：`*.workflow.json`/WorkflowDocument v3 已成为
> 用户可见“任务”的唯一源文档，执行计划由 Compiler 派生；控制流与 presentation
> 分离，运行状态不落盘。17 个旧任务已由显式 CLI 转换并归档，`.task`、旧
> `.workflow`、读取时隐式迁移、双格式 Repository API 和旧目录配置已从 runtime 删除。
>
> 2026-08-07 组合编辑单一化已完成：WorkflowDocument v4 使用自包含 Subworkflow
> 保留 Loop/Parallel 控制流，`WorkflowEditingSession` 成为当前编辑文档唯一所有者。
> 原“任务组合”页面、服务、Activity 入口和控制器路径已删除，不保留兼容入口。
>
> 2026-08-07 D-031 工作台交互收口已完成：Task/Action/Workflow 命令迁移到对应
> 面板顶部的 Qt Resource 单色 SVG icon-only 工具栏，普通编辑命令使用 32 px 命中区，
> 停止任务、快速停止和设备急停继续使用 44 px 命中区与语义色；原 Bottom Panel
> 垂直 Splitter 已删除，设备、位姿、控制和日志改由 Status Bar 图标打开右下锚定的
> 非模态详情浮层。节点拖动增加 ghost 缩略图、原位占位与最近插入点脉冲反馈；布局
> 偏好直接升级为 schema v2，不兼容读取 v1。

> 2026-08-10 D-032 视觉与命令一致性收口已完成：任务页恢复“将当前流程保存为任务”
> SVG 入口；菜单动作统一注册到可编辑、冲突校验并持久化的快捷键表；基础动作分类由
> 平铺 Tab 改为单一下拉选择。浅/深主题下的 SVG、状态栏、数值增减控件、列表选择态和
> 节点尺寸共享同一 Palette/QSS/令牌：状态栏不再重复设备摘要，设备入口以同源详情为准；
> 画布节点更紧凑，选择仅使用柔和背景层级而非粗实线框。

## 1. 结论

本次 GUI 重构应以“受约束的工作流画布 + 工作台壳层”替换当前纵向堆叠式界面，并继续复用项目已经收敛的领域模型、应用服务、设备运行时和唯一执行运行时。

重构不新建第二套执行器、动作处理器或持久化仓库；任务组合能力进入唯一工作流画布，
不能继续由独立列表组合器维护，也不能以视觉升级为由回退循环、子流程、AI 生成序列、
轨迹和执行控制能力。

目标调用链固定为：

```text
WorkflowDocument v4（Action/Loop/Parallel/Subworkflow 纯编辑模型）
    ↓
WorkflowValidator（结构与 Schema 校验）
    ↓
WorkflowCompiler（编译并生成节点映射）
    ↓
ExecutionPlan（Sequence/Action/Loop/Parallel）
    ↓
ExecutionBridge（Qt 事件适配）
    ↓
ExecutionManager
    ↓
ActionHandlerRegistry
    ↓
DeviceRuntime / Application Service
```

以下内容属于架构红线：

- `ExecutionManager` 是唯一序列执行器。
- `ActionHandlerRegistry` 是唯一动作 Handler 注册表。
- `CompositionService` 是动作、任务和当前序列的唯一持久化入口。
- `ActionDefinition`、`SequenceItem`、`LoopBlock`、`ParallelBlock`、`SubworkflowBlock`、
  `SequenceEntry`、`ExecutionPlan` 和 `ActionSchema` 是规范领域模型。
- GUI 不直接访问设备、JSON 文件、Handler 或 Provider。
- 不保留新旧编辑器双写、双执行或长期兼容层；达到切换门槛后一次切换并删除旧入口。

## 2. 背景与目标

当前竖屏界面同时展示设备状态、位姿、动作库、序列、任务库、执行控制、手动控制和日志，核心编辑区偏小，信息层级不清。`MainWindow` 虽已拆出稳定视图组件，但仍需要继续缩减为窗口壳、导航和顶层生命周期协调器。

本轮目标：

- 让添加、排序、配置、循环编排、保存和执行形成连续操作流。
- 使用画布直观呈现顺序、循环范围、当前节点和失败节点。
- 将任务库放入按需展开的侧栏，将设备详情和日志放入状态栏锚定浮层；参数通过编辑命令按需打开。
- 保持 GUI 主线程无阻塞，执行和设备状态来自唯一应用状态源。
- 为高 DPI、触控、键盘和不同主题提供可验证的交互与视觉规范。
- 让画布默认占据主要工作区，把资源浏览、设备详情、位姿、日志和低频控制改为按需展示。
- 使用稳定的 Activity Bar、Side Bar、Editor、Status Bar 和非模态详情浮层，消除多层 Tab、GroupBox、大按钮矩阵及垂直 Bottom Panel 对画布空间的挤占。

非目标：

- 条件分支、数据流和实时引用型子流程仍不在本轮范围；受控并行已完成，D-028～D-030
  仅实现自包含、可递归编辑、编译时透明展开的内嵌 Subworkflow。
- 第一阶段不实现任意拓扑、任意端口连接或通用 BPMN 引擎。
- 不为未立项能力预建执行上下文、节点 Handler 或协议兼容层。

## 3. 术语

| 术语 | 定义 |
|---|---|
| Action | 一个规范化的 `ActionDefinition` |
| Sequence item | 带稳定 UUID、状态和 Action 的 `SequenceItem` |
| Loop block | 带重复次数和子项的 `LoopBlock` |
| Sequence entry | `SequenceItem | LoopBlock | ParallelBlock | SubworkflowBlock`，是持久化/组合边界元素 |
| Subworkflow | 带名称、来源元数据和递归 `WorkflowSequence` body 的内嵌快照；可独立进入作用域编辑，编译时透明展开 |
| Execution plan | Compiler 生成的不可变递归执行输入，包含 Sequence/Action/Loop/Parallel |
| Workflow document | GUI 编辑期纯 Python 文档，包含节点、顺序和布局元数据 |
| Task | 用户可见概念；持久化为唯一 `*.workflow.json`/WorkflowDocument，运行时计划由 Compiler 派生 |
| Node | Action、Loop、Parallel、Subworkflow、Start 或 End 的画布表现，不是新的业务动作类型 |
| Compile | 将合法编辑文档转换为规范 `ExecutionPlan`，不执行设备动作 |
| Preflight | 执行前检查设备在线、能力、资源和停止能力等瞬时条件 |

文档和界面不得混用“序列、工作流、组合任务”表达同一对象；用户可见名称统一为“任务”，画布内部使用 Workflow/Node。

## 4. 功能基线与首版范围

新编辑器切换前必须保持以下现有能力：

- 动作库浏览、搜索、分类和参数配置。
- 动作添加、删除、复制、排序、清空、撤销和重做。
- `LoopBlock` 创建、编辑、嵌套约束、循环次数和执行进度展示。
- 任务保存、加载、重命名、删除，以及任务/动作混合插入当前工作流。
- AI/语音生成序列的预览、人工确认、导入和执行。
- 轨迹、视觉、相机及手动控制相关入口的现有可用能力。
- 开始、暂停、恢复、停止任务、快速停止和设备紧急停止。
- 当前步骤、循环进度、终态、错误和结构化日志展示。

首版采用受约束的线性画布：

- 默认自动纵向排列，动作加入后自动建立顺序关系。
- 节点间显示“+”插入入口；支持节点拖动、显式上移/下移排序和批量选择。
- 连线用于呈现和调整顺序，不能形成分叉、汇合或环路。
- Loop 使用容器/复合节点表达，不能降级为普通动作或丢失层级。
- Start/End 是只读表现节点，不进入 `SequenceEntry`，也不产生执行事件。
- 自由布线和多端口仅在真实业务需求、ADR 和运行时设计完成后立项。

## 5. 信息架构

```text
┌──────────────────────── 原生标题栏 ────────────────────────┐
│ 程序 icon / 标题                         最小化 / 最大化 / 关闭 │
├────────────────────────────────────────────────────────────┤
│ 文件  编辑  视图  执行  设备                       客户区菜单 │
├────┬──────────────┬────────────────────────────────────────┤
│任务│ 当前资源页   │ [编辑/执行 SVG 工具栏] [适合内容/100%] │
│动作│              ├────────────────────────────────────────┤
│ AI │ 可调整宽度   │                                        │
│大纲│ 的 Side Bar  │              工作流画布                │
│可选│              │                           ┌──────────┐ │
│设置│              │                           │详情浮层  │ │
│    │              │                           └──────────┘ │
├────┴──────────────┴────────────────────────────────────────┤
│ ●设备摘要 │ 当前任务/执行状态 │ 设备 │ 位姿 │ 控制 │ 日志 │
└────────────────────────────────────────────────────────────┘
```

工作台区域职责：

- 顶部 `QMenuBar` 提供文件、编辑、视图、执行和设备命令及键盘快捷键，不重复放置全宽保存/加载按钮。菜单保留在原生标题栏下方的客户区；不为实现与程序 icon 同行而接管跨平台原生非客户区。
- 左侧 Activity Bar 固定约 48～52 px，只负责切换已保存任务、基础动作、AI 助手和
  可选工作流大纲；原任务组合资源页删除。再次点击当前图标即收起 Side Bar，不设置
  额外展开/关闭按钮。
- Side Bar 使用 `QStackedWidget` 承载独立资源页；基础动作在页内按动作类型分组，禁止为每个动作类型占用一个 Activity Bar 图标。
- Side Bar 与画布通过水平 `QSplitter` 调整宽度；视觉上永远只显示 1 px 分隔线，允许使用 6～8 px 透明命中区提高可操作性，并记忆每个资源页最近宽度。
- 原画布/Bottom Panel 垂直 `QSplitter` 已删除；设备详情、位姿、日志和基础控制由 Status Bar 的 icon-only 入口打开右下锚定非模态浮层，浮层覆盖显示而不改变或压缩画布布局。
- Status Bar 常驻显示设备健康、当前任务、执行状态及附加服务摘要；同一个入口再次点击、Escape 或浮层关闭图标均可关闭详情。resize 和窄屏时浮层必须保持在可用客户区内。
- 停止任务、快速停止和设备急停属于常驻安全命令，不能隐藏在 Side Bar、详情浮层或菜单深层。
- Task、Action 和 Workflow 的功能命令位于对应面板顶部的 icon-only 工具栏；fit/zoom 使用同一 Qt Resource SVG 体系。普通编辑工具保持 32 px 命中区，停止/快停/急停保持 44 px 命中区与明确语义色。

主窗口职责：

- `MainWindow` 只组合页面、导航、抽屉和顶层 Qt 生命周期。
- 工作流页面由独立 Controller 协调 Editor Service、Scene 和 Bridge。
- 动作库、任务库、设备状态、参数、日志和手动控制保持独立视图组件。
- 禁止在 `MainWindow`、`QGraphicsItem` 或对话框中编写业务执行与设备调用。

## 6. 核心交互

### 6.1 添加与排序

首选入口按优先级排列：

1. 点击节点间“+”并选择动作。
2. 双击动作库条目插入当前选中位置之后。
3. 从动作库拖到画布的有效插入区。
4. 端口拖到空白处弹出菜单仅作为后续可用性验证项，不是首版重点。

已保存任务的组合入口统一为：双击打开为当前编辑文档，拖到节点间“+”或使用右键
“插入到当前任务”则复制为 Subworkflow。一次插入必须递归重建 Action/Loop/Parallel/
Subworkflow 节点 UUID 和 Parallel branch ID，并作为单条 Undo 命令提交。

新节点自动布局并连接。按住节点左键并超过移动阈值后进入纵向拖动排序：画布保留原节点占位，创建轻量 ghost 缩略图跟随鼠标；最近的合法主序列插入“+”使用主题化发光、克制脉冲和位置标签预告放置结果。动作库或任务库从外部拖入时复用同一目标反馈。松开后按目标位置只提交一次撤销命令并自动吸附；取消、离开画布或完成后必须清理 ghost、目标动画和标签。未超过阈值的左键仍只负责选择，Shift+左键不启动拖动，Ctrl+左键仅用于画布平移。上移/下移命令和右键菜单保留为精确排序及触控替代入口。只有进入高级连线模式时才允许直接操作边。

循环节点采用展开式结构：循环头显示次数与动作数，循环体内保留可执行动作卡片，底部显示“循环完成”，两侧分别显示“下一次”和“达到次数”路径。循环头与子动作间的“+”必须可点击，并将动作插入循环体的准确位置，不能只是装饰图形。

### 6.2 选择与编辑

- 单击选中；Shift+单击或框选进行多选，Ctrl+左键保留给画布平移。
- 双击或按 Enter 打开完整参数编辑器。
- 单击节点不显示参数摘要；参数编辑通过双击、Enter、右键菜单或“修改”按钮按需打开完整编辑对话框。
- 删除、移动、参数更新、循环调整、粘贴均使用 `QUndoCommand`。
- 连续拖动合并为一个撤销命令，避免命令栈污染。
- Subworkflow 默认显示为紧凑折叠卡片，展示名称、直接子节点数和来源 revision；双击或
  Enter 进入其 body，顶部面包屑显示“根任务 / 子流程 / …”，点击任一级返回对应作用域。
- 子流程内 Action 继续使用同一 Schema 参数编辑器；修改默认只影响当前文档中的内嵌
  快照，不隐式修改基础动作或源任务。

### 6.3 画布导航

- 左键单击选择节点，左键双击编辑节点，Shift+左键追加或取消多选。
- 右键在命中节点或当前多选集上打开操作菜单，提供编辑、移动、创建/展开循环和删除等适用操作。
- Ctrl+左键拖动平移画布，中键拖动作为备用平移手势。
- 鼠标滚轮纵向滚动画布；Ctrl+滚轮缩放；Ctrl+0 恢复 100%。
- Ctrl+A 全选，Esc 取消选择或平移，Delete/Backspace 删除，Ctrl+Z/Y 撤销/重做。
- 触控支持双指缩放、双指平移；单指命中节点时选择节点，空白区域单指平移。
- 提供“适合内容”“100%”和小地图/当前位置反馈。
- 缩放范围和动画时长必须有限，不能造成眩晕或误操作。

### 6.4 Activity Bar 与资源侧栏

- Activity Bar 使用单色 SVG 图标、选中指示条、Tooltip 和 `accessibleName`；不能只靠图标形状表达含义。
- Task 和 Action 页将打开、插入、新建、修改、删除及相机测试等命令收敛到页标题栏的 icon-only 工具栏；所有图标使用同一 Qt Resource、主题 Palette、Tooltip 和无障碍命名，不在页底保留重复大按钮。
- 已保存任务、基础动作和 AI 助手是独立资源页；工作流大纲仅投影当前文档结构，不能
  成为第二状态源。原任务组合页及其列表式草稿删除。
- 点击未选图标时切换并展开对应资源页；点击已选图标时收起 Side Bar；“视图”菜单与快捷键提供等价入口。
- Side Bar 设置合理最小/最大宽度，窗口缩小时优先保持画布和常驻安全命令可用。
- 动作插入选择器继续采用“动作类型—分类内动作”结构，空分类不显示，禁止扁平化为单个长列表。

### 6.5 详情浮层、状态栏与安全命令

- 设备详情、机械臂位姿、运行日志和基础控制由 Status Bar 的 icon-only 按钮打开右下锚定非模态浮层；原 Bottom Panel 和垂直 Splitter 删除，详情展示不得压缩画布。
- 再次点击当前图标、Escape 或浮层关闭图标均可关闭；切换其他图标时复用同一个页面容器。浮层在 resize、DPI 变化和窄窗口中重新约束尺寸与位置，不能越出客户区。
- 位姿页面提供刷新、复制 R1、复制 R2 和定位详情；日志页面提供级别过滤、复制、清空和自动滚动控制。
- Status Bar 只展示稳定摘要和语义状态，不展示长错误；详细错误进入通知历史或详情浮层。
- 开始、暂停/恢复与停止任务靠近当前执行状态；执行区使用 icon-only 工具栏，但每个图标必须具备完整 Tooltip、accessibleName 和键盘入口。停止任务、快速停止和设备急停始终可见、位置稳定，保持 44 px 命中区及 danger/warning 语义色，不能只靠颜色或图形区分。
- 设备急停继续明确标注为软件急停，不能造成替代物理急停回路的误解。
- 参数编辑、危险操作确认等一次性交互使用对话框；实时状态、日志、位姿和设备详情禁止使用模态对话框。

### 6.6 触控要求

- 主要触控目标最小 44×44 设备独立像素。
- 端口视觉可较小，但有效命中区不得小于 24×24，推荐 32×32。
- 长按菜单需设置移动容差和取消条件。
- 所有关键任务必须存在无需鼠标悬停和键盘的完整路径。
- 危险按钮保持固定位置、明确文案和足够间距，不能只靠颜色区分。

## 7. 数据模型与状态边界

`WorkflowDocument` 必须是纯 Python 模型，不继承 `QObject`，也不持有 `QGraphicsItem`。

```python
@dataclass(frozen=True)
class CanvasPosition:
    x: float
    y: float

@dataclass(frozen=True)
class ActionNodeModel:
    node_id: str
    item: SequenceItem
    position: CanvasPosition

@dataclass(frozen=True)
class LoopNodeModel:
    node_id: str
    block: LoopBlock
    position: CanvasPosition

@dataclass(frozen=True)
class WorkflowDocument:
    workflow_id: str
    name: str
    revision: int
    nodes: tuple[ActionNodeModel | LoopNodeModel, ...]
    order: tuple[str, ...]
```

具体实现可根据现有模型可变性调整，但必须满足：

- Action 节点引用/包含规范 `SequenceItem`，不能另造 `type: str + dict[str, Any]` 动作体系。
- Loop 节点引用/包含规范 `LoopBlock`。
- 运行状态不写入持久化任务；由 Execution ViewModel 根据运行事件派生。
- 布局、缩放和折叠状态是表现元数据，不能改变执行语义。

### 7.1 M8 目标模型

旧 `nodes + order` 只能承载顺序和单层 Loop；M8 已在保持纯 Python/Qt 无关
边界的前提下升级为结构化控制流：

```text
WorkflowDocument
├─ metadata: workflow_id/name/revision
├─ root: Sequence | Action | Loop | Parallel | Condition
└─ presentation: positions/collapsed/viewport
```

- schema v3 已完整支持可嵌套 Sequence/Action/Loop/Parallel；A-014/A-015 已完成
  编译、资源冲突、失败、取消、join、事件身份和调度语义；D-027 已补齐 GUI
  Parallel 创建、分支编辑和运行状态表达，所有入口使用同一正式模型。
- `presentation` 不参与执行语义，运行状态和执行历史不得写回 WorkflowDocument。
- Action 节点保存规范化可复现快照及来源 action ID/revision，不按显示名称解析。
- 不实现任意连线图或通用 BPMN；Condition 不执行任意代码，表达式模型另行评审。

Qt 边界拆分为：

- `WorkflowEditorService`：修改、版本、撤销命令所需的纯应用行为。
- `WorkflowDocumentBridge(QObject)`：把领域变更转换为 GUI 线程 Signal。
- `WorkflowScene`：仅渲染文档与发送用户意图。
- `WorkflowController`：连接 View、Service、Compiler、CompositionService 和 ExecutionBridge。

## 8. 校验、编译与执行

### 8.1 保存时结构校验

结构校验只验证稳定事实：

- 节点 ID、顺序和引用完整。
- Start/End 表现节点唯一且顺序可达。
- 不存在分叉、汇合、环路和孤立业务节点。
- Action 符合唯一 `ActionSchema`。
- Loop 次数、子项、嵌套深度和最大展开规模合法。

设备暂时离线不能使已保存任务变为结构损坏。

### 8.2 执行前 Preflight

Preflight 单独检查瞬时条件：

- 必要设备是否在线并就绪。
- Provider 是否声明所需能力。
- 资源租约是否可获得。
- 动作控制策略与停止能力是否满足。
- 当前系统是否允许开始新的运行。

### 8.3 编译边界

`WorkflowCompiler` 只负责：

- 将文档结构转换为不可变 `ExecutionPlan`，并保留可持久化 `SequenceEntry` 投影。
- 保留 `SequenceItem.uuid`/Loop UUID。
- 生成运行步骤与 `node_id` 的稳定映射。
- 返回不可变编译快照和诊断信息。

它不得调用设备、Handler 或执行器。编译结果经现有 `ExecutionBridge` 提交给 `ExecutionManager`。

执行事件依据稳定 UUID/编译映射更新节点状态。禁止按名称、列表位置或坐标猜测当前节点。

### 8.4 节点表现注册

如需注册节点图标、颜色、参数摘要和编辑器工厂，命名为 `NodePresentationRegistry`。其动作元数据从 `ActionSchema` 派生，不得提供 `execute()`，不得与 `ActionHandlerRegistry` 重复登记业务动作。

## 9. 执行控制与安全语义

底部固定控制区应明确提供：

| 控制 | 语义 | 显示条件 |
|---|---|---|
| 开始 | 提交已通过校验和 Preflight 的编译快照 | 文档有效且运行时可接受 |
| 暂停/恢复 | 请求运行时暂停或恢复 | 由 `ExecutionViewModel` 能力派生 |
| 停止任务 | 协作取消当前任务 | 存在活动运行 |
| 快速停止 | 调用统一 Safety Service 的 quick stop | 目标设备声明并验证能力 |
| 设备紧急停止 | 调用设备 Provider 的 emergency stop | 目标设备声明并验证能力 |

软件按钮不得宣称自己是安全等级物理急停，也不得在未完成真实硬件验收前声称“切断输出”。界面应显示目标设备、能力、请求结果和恢复条件；物理急停仍是最终安全设施。

首版不提供“失败后跳过”“自动重试”“从任意节点继续”和“单步执行”。这些能力需要先在运行时明确资源、安全策略、状态机和审计语义，再单独立项。

## 10. 持久化与恢复

- `CompositionService` 保持任务、动作和当前序列的唯一所有者。
- `JsonCompositionRepository` 保持唯一文件存储实现；GUI 不直接读写 JSON。
- 新 Schema 使用显式 `schema_version`，保存采用原子替换。
- 格式升级先备份原文件，再运行一次性前向迁移；不长期保留双格式读写。
- 编辑中的未保存内容使用独立草稿和自动保存，启动时提供崩溃恢复选择。
- 剪贴板、导入和 AI 生成内容必须经过大小限制、Schema 校验和结构校验。
- 保存采用 revision/乐观并发检查，冲突必须显式提示，不能静默覆盖。
- 正式任务统一保存为 `workflows/<name>.workflow.json`；草稿保存到独立
  `drafts/<workflow-id>.draft.workflow.json`，不得与正式任务混放。
- `*.workflow.json` 通过最后一级 `.json` 自动获得通用编辑器语法高亮，并使用
  `$schema` 提供补全与字段错误提示。
- 历史 `.task` 和 `.workflow` 只由显式迁移 CLI 读取；正常 Repository 查询不得
  触发备份、迁移或任何写盘。
- 迁移必须先 dry-run，再备份、转换、重新加载并比较任务数量、稳定 ID、步骤、
  参数和语义指纹；全部成功后一次切换并删除旧运行时读取分支。

## 11. 日志与可观测性

- 日志面板显示运行、节点、设备、安全和系统事件，但不成为唯一错误反馈。
- 画布节点、顶部摘要和通知中心同步显示与当前操作相关的错误。
- 只有结构化事件已经提供 `level/device_id/node_id/run_id/request_id` 时才开放对应过滤器。
- 禁止通过解析人类可读日志字符串推导业务状态。
- 大量日志采用批量刷新和有界缓存，避免阻塞 GUI 线程。

## 12. 推荐目录

不新增 GUI 自有执行层，推荐在现有分层内组织：

```text
src/
├─ domain/
│  └─ workflow.py                  # 纯工作流编辑模型（如确有必要）
├─ application/
│  ├─ workflow_editor.py           # 编辑用例、版本与诊断
│  └─ workflow_compiler.py         # Workflow -> SequenceEntry
└─ gui/
   ├─ assets/
   │  ├─ icons/                    # 可追踪许可证的单色 SVG
   │  └─ gui.qrc                   # Qt Resource 唯一打包入口
   ├─ controllers/
   │  └─ workflow.py               # 页面协调，不执行设备动作
   ├─ bridges/
   │  └─ workflow.py               # QObject 信号与线程适配
   ├─ view_models/
   │  └─ workflow.py               # 可渲染状态
   └─ views/
      ├─ workbench/
      │  ├─ shell.py               # Activity/Side/Editor/Bottom/Status 组合
      │  ├─ activity_bar.py        # 资源页切换意图
      │  ├─ side_bar.py            # 资源页栈与宽度状态
      │  ├─ bottom_panel.py        # 详情页栈与高度状态
      │  └─ status_bar.py          # 常驻摘要和面板入口
      └─ workflow_canvas/
         ├─ page.py
         ├─ scene.py
         ├─ view.py
         ├─ items.py
         ├─ panels.py
         └─ commands.py
```

若某文件规模或职责很小，可以合并；目录不是目标，依赖方向和职责单一才是目标。禁止出现 `gui/execution/`、`gui/handlers/` 或 GUI 私有 repository。

## 13. 视觉系统

### 13.1 设计令牌

颜色、间距、圆角、字体、阴影、动画和状态色必须集中定义，不在各 Widget 中散落硬编码。优先遵循 Qt 系统 Palette，并明确测试浅色和深色主题；如产品决定仅支持单主题，应记录为产品约束而不是偶然实现。

- 使用设备独立尺寸和 DPI 缩放，不以固定 9px 字号作为正文基线。
- 应用提供 `system`（随系统）、`light`（浅色）和 `dark`（深色）三种统一主题；
  启动模式由 `GUI_THEME` 指定，运行时可从“视图 → 主题”即时切换。
- 中文正文应在目标设备上保持可读，信息密度不能依赖极小字号。
- 状态同时使用颜色、图标和文字。
- 图标来源、许可证和打包路径必须可追踪。
- 键盘焦点样式、`accessibleName`、对比度和读屏顺序纳入验收。

### 13.2 节点状态

节点至少区分：默认、选中、悬停、校验失败、等待、执行中、成功、失败、取消、禁用。执行动画应克制，并支持减少动态效果；避免每个节点使用 `QGraphicsProxyWidget`、高成本阴影或持续动画。

### 13.3 SVG 图标与导航状态

- 导航、状态和主要工具栏建立统一 SVG 资源，不再混用 Emoji、运行时绘制图标和文本装饰符号。
- SVG 使用可由 Theme Palette 驱动的单色前景；统一 16/20/24 px 视觉栅格，并覆盖默认、悬停、选中和禁用状态。
- 图标资源必须进入 wheel、Qt Resource 和许可证清单；禁止依赖当前工作目录的相对文件路径。
- icon-only 按钮必须同时设置 Tooltip、`accessibleName` 和键盘等价入口；普通编辑命令使用 32 px 命中区，高风险停止命令保持 44 px 命中区及明确语义色。
- Activity Bar 的选中状态、Side Bar 当前页/宽度及详情浮层 `panel_page`/`panel_visible` 属于 schema v2 GUI 布局偏好，必须持久化并对损坏值安全回退。schema v1 不兼容读取，不保留迁移分支。
- 顶部菜单与程序 icon 同行需要接管原生非客户区，跨平台会扩大窗口拖动、缩放、最大化、DPI、系统菜单和无障碍风险；当前批准保留原生标题栏及其下客户区菜单。除非未来有独立 ADR、跨平台测试矩阵和明确产品收益，不实施自绘标题栏。

### 13.4 性能策略

- 节点使用轻量 `QGraphicsItem`/自绘，静态内容允许缓存。
- 可见区域外减少更新；状态事件合并后在 GUI 线程渲染。
- 100 节点为正常规模，500 节点为压力规模，二者都应有可记录的交互预算。
- 布局、校验、序列化和大规模导入不得长时间占用 GUI 线程。

## 14. 实施阶段

### 阶段 0：基线与决策

- 建立现有功能、快捷键、状态和数据格式的等价清单。
- 记录“受约束画布、单一执行器、Loop 表现、直接切换”的 ADR。
- 用可丢弃原型验证 QGraphicsView 的触控、缩放和 100/500 节点性能。

### 阶段 1：模型、持久化与编译

- 定义纯 Python `WorkflowDocument`，复用规范领域模型。
- 完成结构 Validator、Preflight 边界和 Compiler。
- 通过 `CompositionService` 完成版本、迁移、原子保存和草稿恢复。

### 阶段 2：应用服务与撤销模型

- 实现 Editor Service、Controller、Bridge 和 `QUndoCommand`。
- 覆盖新增、删除、移动、参数、排序、循环、复制粘贴和批量操作。

### 阶段 3：只读画布与执行映射

- 先渲染现有任务、循环和执行状态。
- 验证编译 UUID 映射、当前节点、循环进度和终态一致性。

### 阶段 4：编辑能力

- 实现自动布局、“+”插入、拖动/显式排序、按需参数编辑、选择和缩放。
- 完成鼠标、键盘和纯触控完整路径。

### 阶段 5：功能等价与 MainWindow 收敛

- 接入任务组合、AI/语音预览、轨迹、相机、日志、设备状态和安全控制。
- 将 MainWindow 继续拆为壳、页面 Controller 和稳定视图组件。

### 阶段 6：一次切换

- 完成数据备份/迁移、回归、性能和真实设备安全验收。
- 直接启用新编辑器，删除旧列表编辑器及旧路径，不双写、不双运行。

### 阶段 7：视觉精修

- 统一设计令牌、图标、主题、空状态、动画和可访问性。

### 阶段 8：工作台骨架与安全命令迁移

- 建立 Top Menu、Activity Bar、Side Bar、Editor、Bottom Panel 和 Status Bar 的工作台壳层。
- 先迁移并固定开始、暂停/恢复、停止任务、快速停止和设备急停，确保布局切换期间安全语义与可见性不回退。
- 画布成为中央主区域；删除旧抽屉箭头、悬停扩宽命中条和纵向堆叠式主布局，不保留两套壳层。

### 阶段 9：资源页与底部面板拆分

- 已将 `ActionLibraryView` 拆为任务、动作、AI 和任务组合资源页，保持原意图信号及 Application Service 边界。
- 该阶段的任务组合资源页是已落地的中间架构，将由阶段 13 直接删除；此处仅保留历史记录。
- 已将设备状态/位姿、日志和基础控制迁入 Bottom Panel，并建立 Status Bar 摘要投影。
- 保存/加载、编辑和执行命令统一进入菜单、工具栏、快捷键或节点上下文菜单，删除重复的大按钮入口。

### 阶段 10：SVG、布局持久化与一次切换

- 已建立 Qt Resource/SVG 图标体系和许可证清单，并覆盖深色、浅色、系统主题与常用 DPI 尺寸。
- 已持久化 Side Bar/Bottom Panel 尺寸、可见性和当前页面，损坏状态安全恢复默认布局。
- 已完成尺寸矩阵、键盘、读屏、功能等价和安全回归并直接切换；真实触控屏继续随设备验收确认。

### 阶段 11：任务数据模型 v3 与格式单一化

- 已修复启动时已保存任务未刷新的回归，并完成迁移前行为基线。
- 已冻结 WorkflowDocument v3、`*.workflow.json`、presentation 和结构化控制流 Schema；
  v2 活动数据已一次迁移，运行时不保留兼容读取。
- 已通过 `robot-workflow-data` 将 `.task`/旧 `.workflow` 一次转换，并删除双格式
  Repository API、读取时隐式迁移和旧路径配置。
- GUI 资源页、保存/加载、任务组合和 WebSocket 已统一使用 Workflow Repository；
  AI/语音后续 typed command 继续复用该入口，不在 View 中直接枚举文件。

### 阶段 12：Parallel 画布表达（D-027，已完成）

- 复用既有 WorkflowDocument v3、WorkflowCompiler 与 ExecutionPlan，自绘横向分支泳道、
  分支汇合点及嵌套动作摘要；未在 GUI 增加调度器或平行业务模型。
- 新建 Parallel、分支新增/删除/左右排序、节点跨分支移动和动作库拖入分支均经过
  同一画布文档修改边界，并复用快照式 QUndoStack；强制 2～8 分支和非空分支不变量。
- ExecutionBridge 按 parallel UUID/branch ID 转发既有运行事件，画布只派生
  pending/running/completed/failed/cancelled 表现状态，运行状态不写回持久化文档。
- 保存/加载 round-trip、Undo/Redo、插入命中、分支命令、执行态、浅色/深色和窄窗口
  offscreen 回归已覆盖；大尺寸并行节点通过画布横向滚动和“适合内容”访问。

### 阶段 13：Subworkflow 与组合编辑单一化（D-028～D-030，已完成）

- WorkflowDocument/Schema 已直接升级 v4，新增递归 `SubworkflowBlock`：保存名称、可选来源
  workflow ID/revision 和自包含 body；默认不是实时引用，源任务变化不得隐式改变父任务。
- Validator 限制最大嵌套深度、展开规模和空子流程；Compiler 将 Subworkflow body 递归
  编译进唯一 ExecutionPlan，并把 subworkflow path 纳入节点/运行事件身份，不新增 Handler。
- 已提供工作流片段复制函数，递归重建所有执行节点、容器和分支身份；同一任务
  多次插入不会产生 UUID 冲突。17 个 v3 活动文档已一次迁移到 v4，runtime 不双读。
- 已新增唯一 `WorkflowEditingSession`，独占当前文档、文件名、revision、dirty、草稿和结构
  修改边界；Canvas、任务库、动作库和 AI 只发送意图、渲染不可变快照。
- 任务库双击/按钮打开文档，拖放/右键/按钮插入为 Subworkflow；画布使用折叠卡片、双击或右键进入作用域
  和逐级返回导航，内部 Action/Loop/Parallel/Subworkflow 共用参数编辑与 Undo/Redo。
- 已删除 `TaskComposerService`、`TaskComposerView`、`TaskComposerListWidget`、组合 Activity
  入口及 MainWindow 对应添加/排序/循环/展开/执行/保存路径；不保留转发或隐藏兼容页。
- 保存统一提交完整 WorkflowDocument 和 expected revision，保留 presentation；执行统一
  编译当前会话快照。禁止继续通过 `flattened_task()` 构建可执行组合。

### 阶段 14：工作台工具栏、详情浮层与交互一致性（D-031～D-032，已完成）

- Task/Action/Workflow 高频功能已迁移到对应面板顶部的 Qt Resource 单色 SVG
  icon-only 工具栏，统一 Tooltip、可访问名称、主题刷新和键盘等价入口；适合内容与
  100% 缩放使用同一图标体系。
- 普通编辑工具命中区统一为 32 px；停止任务、快速停止和设备急停保持 44 px 命中区、
  固定位置与既有语义色，不因工具栏紧凑化降低安全可操作性。
- 已删除原 Bottom Panel 垂直 Splitter。Status Bar 的设备、位姿、控制和日志图标打开
  右下锚定的可复用非模态详情浮层；同键、Escape 和关闭图标可关闭，resize 与窄屏
  均约束在客户区内，画布尺寸不随浮层开合变化。
- `WorkbenchLayoutState` 直接升级 schema v2，以 `panel_page`/`panel_visible` 记录
  浮层偏好；v1 不兼容读取，损坏或旧版本状态按统一恢复默认策略处理。
- 节点拖动使用跟随指针的 ghost、原节点占位以及二维激活半径内插入“+”的主题化
  发光、脉冲和位置标签；无脉冲目标时释放会恢复原位，不再按纵坐标隐式重排。
- 外部 Action/Task 拖入与节点重排在提交时复用同一二维 resolver；命中顶层“+”时按
  提示顶层插入，未命中时才解析 Loop/Parallel 容器，避免预览与实际提交分叉。
- Escape、窗口失焦、鼠标抓取丢失和正常完成都走幂等清理，恢复原节点并停止动画；
  有效放置只提交一次 `UndoCommand`。
- 顶部菜单继续位于原生标题栏下方；未接管跨平台非客户区，这是对窗口行为、DPI 和
  无障碍维护风险的批准取舍。
- 任务资源页提供“将当前流程保存为任务”的 SVG 图标入口；保存仍经唯一
  `WorkflowEditingSession`/CompositionService，不新增 GUI 私有文件写入路径。
- 所有顶部菜单动作由 `ShortcutRegistry` 统一登记默认键位、QSettings 持久化和冲突校验；
  “视图 → 快捷键设置”可编辑或恢复默认，禁止空键位和重复组合键。
- 基础动作侧栏以单一下拉框切换移动、执行、检测、换枪、视觉和轨迹类别；数值输入使用
  主题化 SVG 增减箭头。状态栏去除重复的左侧设备文字摘要，仅将可用数及语义色附着到
  “设备详情”图标和浮层。
- Activity/Pane/Command/Status 图标按共享尺寸令牌与 Palette 刷新；浅色与深色下的
  禁用、普通和语义操作均保持可读。列表选择与画布节点使用柔和选中底色、紧凑尺寸和
  必要边界，不再用粗实线框表达选中。

## 15. 测试与验收

### 15.1 自动化测试

- Workflow 文档序列化 round-trip、Schema 升级和损坏数据拒绝。
- v3→v4 一次迁移的语义指纹、备份、失败不切换和 runtime 拒绝旧版本。
- Subworkflow 多层 round-trip、递归 UUID 重建、同一任务重复插入、嵌套深度/节点总量拒绝。
- 组合 Loop/Parallel 时编译计划保持控制流结构，禁止退化为扁平顺序动作。
- 根任务/多层子流程面包屑、作用域切换、内部 Action 编辑和全局 Undo/Redo。
- `.task`/`.workflow` 到 `*.workflow.json` 的 dry-run、备份、语义指纹和失败不切换测试。
- 主窗口启动时已有任务立即显示，首屏加载与保存/删除事件刷新结果一致。
- Validator、Compiler 与 `ExecutionPlan` 的确定性，以及嵌套 Loop/Parallel 覆盖。
- 每种 Undo/Redo 命令，以及拖动 ghost、原位占位、二维合法插入点高亮、无目标原位
  恢复、Esc/失焦/失去鼠标抓取清理、圆形加号命中和一次命令提交。
- UUID 到执行事件/节点状态的映射。
- Qt offscreen：加载、编辑、保存、执行、暂停、取消和关闭。
- 剪贴板、导入、AI 内容的恶意/超限输入。
- 100/500 节点加载、缩放、拖动、校验和保存性能。
- Activity Bar 切换/二次点击收起、Side Bar 拖动、schema v2 布局持久化、v1 拒绝和损坏状态恢复。
- 状态摘要与详情浮层来自同一 ViewModel 快照；同键/Escape/关闭按钮关闭、页面切换、resize 和窄屏不越界；浮层开合不改变画布尺寸。
- Task/Action/Workflow 工具栏及 fit/zoom 全部使用 Qt Resource SVG，Tooltip、可访问名称、32 px 编辑命中区和 44 px 安全命中区符合约束。
- SVG 在 system/light/dark、100%～200% DPI 和 wheel 隔离安装环境可用。

D-031 落地验证：本批 5 个 GUI 变更测试文件聚焦回归
`63 passed + 26 subtests`；完整门禁 Compile/Ruff 通过，Mypy
`287 source files / 0 errors`，Pytest
`529 passed + 74 subtests`，coverage `66.09%`，LLM golden `14/14`，
performance `9/9`，wheel smoke 通过。

D-032 落地验证：主题/Workbench/Toolbar/Shortcut/Canvas 聚焦回归
`73 passed + 28 subtests`，真实 MainWindow 与菜单快捷键回归
`17 passed + 25 subtests`；全仓手写 Mypy `288 source files / 0 errors`、
Ruff（`src`/`scripts`/`tests`）和完整 Pytest `532 passed + 101 subtests` 通过。

### 15.2 目标设备矩阵

- 至少覆盖项目实际竖屏分辨率、常用桌面分辨率和 100%/125%/150%/200% DPI。
- 覆盖 Windows 主目标平台，以及 CI 中的 Linux offscreen smoke。
- 覆盖浅色/深色系统 Palette（或已批准的单主题约束）。
- 覆盖鼠标键盘和纯触控关键流程。

### 15.3 切换门槛

- 现有功能基线全部通过，Loop、Parallel、Subworkflow 和 AI 导入无回退；组合后结构
  与源结构的编译语义一致，不允许拍平控制流。
- GUI 线程无设备 I/O、模型加载或长时间计算。
- 仓库中不存在第二个执行器、动作 Handler 注册表或 GUI 私有持久化入口。
- 停止、快停和设备急停文案、能力和结果与真实运行时一致。
- 保存失败、版本冲突、设备离线和运行失败均有可恢复反馈。
- 旧编辑器删除后，全量质量门禁与 GUI extra/wheel smoke 通过。
- `TaskComposerService/View/ListWidget`、组合 Activity 入口及 `flattened_task()` 组合执行
  调用全部删除，仓库中只剩一个工作流编辑状态源。

## 16. 风险与控制

| 风险 | 等级 | 控制方式 |
|---|---|---|
| GUI 新建执行器造成双状态源 | P0 | 编译结果只提交现有 ExecutionBridge/ExecutionManager，并加架构测试 |
| Loop、Parallel、AI 或子流程组合在切换时回退 | P0 | 结构化编译指纹、功能等价清单和切换门槛阻止上线 |
| 独立组合器拍平控制流 | P1 | D-030 删除组合器；组合只允许插入结构化 Subworkflow，不再调用 flattened_task 执行 |
| 重复插入任务造成 UUID/事件映射冲突 | P1 | 领域层递归身份重建，并覆盖同源任务多次插入和嵌套回归 |
| 修改源任务意外改变父任务 | P1 | 默认保存自包含快照；更新来源只能通过显式替换并确认，不实现隐式实时引用 |
| 多层子流程挤满画布 | P2 | 默认折叠卡片、双击进入作用域、面包屑返回；大纲只读投影 |
| 软件急停造成安全误导 | P0 | 区分三类停止，能力驱动显示，真实硬件验收 |
| 自由画布增加窄屏操作成本 | P1 | 默认自动布局和“+”插入，自由布线延后 |
| Qt 模型与领域模型耦合 | P1 | 纯文档 + QObject Bridge + Scene 表现三层分离 |
| 大图、阴影和 ProxyWidget 卡顿 | P1 | 轻量自绘、缓存、性能预算和可丢弃原型 |
| 新旧格式造成数据丢失 | P1 | 原始备份、一次性前向迁移、原子保存和恢复测试 |
| 模仿 VS Code 导致图标过密或触控目标过小 | P1 | 只借鉴信息架构，保持 44 px 关键触控目标、文字 Tooltip 和可访问名称 |
| 状态/控制移入浮层后关键故障或停止入口不可见 | P0 | Status Bar 常驻故障摘要，三类停止固定显示并加入尺寸矩阵验收 |
| Side Bar/详情浮层状态损坏导致界面不可用 | P1 | schema v2 布局偏好、范围校验、旧版本拒绝和恢复默认布局命令 |
| 自绘标题栏破坏原生窗口行为 | P1 | 保留原生非客户区；菜单位于其下客户区，未完成独立 ADR 与跨平台矩阵前不接管 |
| 拖动反馈留下 ghost 或持续动画 | P1 | 取消/离开/完成统一清理，目标动画只在最近合法插入点运行，最终仅提交一次 UndoCommand |
| SVG 打包或许可证遗漏 | P1 | Qt Resource、wheel smoke、许可证清单和禁止裸相对路径门禁 |

## 17. 完成定义

本计划完成不等于“画布能显示”。只有在单一数据源、单一执行入口、现有功能等价、工作台信息架构、触控与可访问性、性能、异常恢复以及安全控制均通过验收，并删除旧编辑入口和旧纵向堆叠布局后，GUI 重构才可标记完成。

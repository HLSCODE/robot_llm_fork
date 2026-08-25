# 代码结构与模块说明

> 文档类型：当前实现架构说明  
> 适用对象：开发、集成、测试与后续维护人员  
> 最近更新：2026-08-19

## 1. 系统定位

Robot Action Orchestrator 是一个面向机器人动作编排、设备控制和 AI 辅助规划的桌面应用。
用户可以在 PySide6 工作台中维护基础动作和工作流，也可以通过 WebSocket 调用同一套应用服务。
系统将设备差异隔离在 provider / adapter / driver 层，将所有动作统一交给执行运行时处理。

主要应用场景：

- 双机械臂、底盘、升降平台、快换手、夹爪、移液枪、继电器等设备的统一控制；
- 将基础动作、循环、并行和子工作流组合为可保存、可编辑、可执行的工作流；
- 视觉抓取、视觉重定位、相机采集与示教数据采集；
- 通过自然语言匹配技能、生成动作序列，并在确认后执行；
- 通过 GUI、WebSocket，以及后续 HTTP 等入口共享同一设备和执行运行时。

## 2. 总体架构

```text
用户 / 外部客户端
     │
     ├── PySide6 GUI ───────────────────┐
     ├── WebSocket API ─────────────────┤
     └── 语音交互（可选） ───────────────┤
                                        ▼
                             ApplicationServices
                                        │
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
    CompositionService          ExecutionService / Manager   CommandRuntime / LLM
    动作与工作流管理             统一执行、暂停、停止            技能、规划、语音路由
             │                          │                          │
             ▼                          ▼                          ▼
       Persistence              Handler Registry            Vision / Voice services
       JSON 文档仓储             动作 Handler                    │
                                        │                          │
                                        └──────────┬───────────────┘
                                                   ▼
                                           DeviceRuntime
                                                   │
                            Provider → Adapter → Driver / Transport → SDK / Hardware
```

### 2.1 核心原则

- **单一宿主**：GUI、WebSocket 和未来 HTTP 服务共用一份 `ApplicationServices`、
  `ExecutionManager` 和 `DeviceRuntime`，不会重复连接硬件。
- **依赖方向固定**：表现层只调用应用服务；应用层依赖领域协议；设备厂商 SDK 只允许出现在
  `src/devices/` 的最底层实现中。
- **定义与运行时分离**：动作、工作流、技能是版本化数据；执行状态、设备连接和线程生命周期
  只存在于运行时，不写回工作流定义。
- **配置集中解析**：TOML、`.env`、系统环境变量和命令行只在启动阶段解析为不可变
  `ApplicationSettings`，业务模块不直接读取环境变量。

## 3. 仓库目录

| 路径 | 内容 | 使用方式 |
|---|---|---|
| `config/` | 入口 TOML、`fragments/` 和本机 `config.toml` | 非敏感部署配置；入口可 include 模块子配置，本机文件不提交 |
| `.env.example` | 密钥、Token 等环境变量模板 | 复制为 `.env` 后填写敏感信息 |
| `data/` | 用户动作、工作流、技能、轨迹、草稿、调试产物 | 运行数据，默认不提交 |
| `docs/` | 架构、接口、配置、Provider、测试和重构文档 | 设计与维护依据 |
| `scripts/` | 质量检查、构建、迁移、可选能力验证脚本 | 本地与 CI 维护入口 |
| `src/` | 应用源码 | 按下文模块边界组织 |
| `tests/` | 单元、契约、集成、GUI 离屏和回归测试 | 验证功能与边界 |
| `third_party/wheels/` | 本地供应商 wheel，例如天机机械臂 SDK | 由 `pyproject.toml` 的本地 source 引用 |
| `pyproject.toml` | Python 元数据、可选依赖、CLI、Ruff/Mypy/Pytest 配置 | 依赖与工程工具的唯一声明源 |

## 4. `src/` 模块职责

| 模块 | 核心职责 | 典型使用场景 |
|---|---|---|
| `bootstrap/` | 进程入口、配置加载、服务组合、GUI 与附加服务生命周期 | `robot-llm` 启动、配置检查、数据迁移 CLI |
| `configuration/` | TOML/环境/命令行解析、Settings、路径与启动校验 | 选择模拟模式、配置相机与模型路由 |
| `application/` | 可复用应用用例与跨模块协调 | 工作流编辑、预检、相机访问、遥操作、数据采集 |
| `domain/` | 稳定业务模型、动作参数 schema、工作流树、执行计划 | 定义动作类型、Loop/Parallel/Subworkflow 结构 |
| `execution/` | 统一执行运行时、Handler registry、控制和工作流编译 | 执行、暂停、恢复、停止一个工作流 |
| `devices/` | 设备运行时、能力协议、Provider、Adapter、Driver、Transport | 以同一接口替换 RealMan、天机或模拟设备 |
| `persistence/` | JSON 文档、动作库、工作流、轨迹与工位数据持久化 | 原子保存动作/工作流、加载轨迹 |
| `gui/` | PySide6 工作台、画布、表单、主题、快捷键、窗口生命周期 | 桌面编排、设备状态、AI 助手、通知 |
| `robot_server/` | WebSocket 协议、控制器、安全、流量限制和指标 | 外部客户端控制、订阅事件和相机帧 |
| `llm/` | Provider 注册、模型路由、流式响应、任务规划与回归 | OpenAI-compatible / MiniCPM 聊天与规划 |
| `skill_system/` | 技能模型、目录扫描、参数绑定与匹配 | 将“抓取瓶子”等意图映射为动作序列 |
| `vision/` | 视觉服务、抓取管线、重定位、工件与调试产物 | 检测抓取点、示教/运行重定位 |
| `voice_interaction/` | 音频输入、ASR、VAD、唤醒词、会话与语音输出 | 唤醒后进行语音聊天或机器人控制 |
| `localization/` | 外部定位输入 provider，当前为 UDP | 获取外部定位基准用于位姿补偿 |
| `geometry/` | 无设备副作用的坐标与位姿补偿计算 | UDP / 视觉补偿后的目标点计算 |
| `data_collection/` | 示教数据 schema、录制、写入和校验 | 采集机器人与相机数据集 |
| `observability/` | 日志格式、上下文与审计辅助 | 关联一次执行的 `run_id`、记录设备失败 |
| `builtin_catalogs/` | 随应用发布的动作、技能和 JSON Schema 资源 | 首次安装默认数据、编辑器 schema 支持 |

## 5. 核心模块与关键文件

### 5.1 启动与应用组合：`bootstrap/`

| 文件 | 功能 |
|---|---|
| `launcher.py` | `robot-llm` 主入口；解析参数，加载 Settings，启动 GUI 与附加服务，并负责退出顺序 |
| `auxiliary_services.py` | 管理 WebSocket 等附加服务，避免其阻塞 Qt 主线程 |
| `catalog_cli.py` | 动作库/技能库校验与迁移命令 |
| `workflow_cli.py` | 旧任务向 `*.workflow.json` 的显式迁移工具 |

启动流程：

```text
CLI → Settings → 配置校验 → ApplicationServices factory
    → GUI Startup Card → MainWindow + WebSocket → 事件循环
    → 安全停止 worker → 关闭附加服务 → 关闭设备运行时
```

### 5.2 配置：`configuration/`

| 文件 | 功能 |
|---|---|
| `settings.py` | 冻结的类型化设置模型；包含 Runtime、Data、Robot、Vision、LLM、Voice 等分组 |
| `config_loader.py` | 组合 TOML、`.env`、系统环境变量和 CLI 覆盖，生成 `ApplicationSettings` |
| `toml_source.py` | 严格 TOML 读取与未知字段检查 |
| `environment.py` | 受支持环境变量的映射和优先级 |
| `data_paths.py` | 从 `[data]` 推导动作、工作流、技能、轨迹和视觉产物路径 |
| `config_validation.py` | 启动前业务规则校验，例如端口、相机角色、硬件参数和安全配置 |

配置来源优先级由低到高为：类型默认值 → TOML → `.env` / 系统环境变量 → CLI。
详细字段见 [配置说明](configuration.md)。

### 5.3 应用用例：`application/`

`application/` 是表现层与运行时之间的应用服务层；GUI、WebSocket、语音和未来 HTTP
入口都应调用这里，而不是直接触碰仓储或设备。

| 文件 | 功能 |
|---|---|
| `factory.py`、`services.py` | 创建并暴露 `ApplicationServices`，作为唯一组合根结果 |
| `composition.py` | 动作库、工作流、草稿和 revision 的业务用例 |
| `workflow_editing.py` | 当前编辑会话、Undo/Redo、子工作流作用域与保存边界 |
| `workflow_compiler.py` | 将递归 `WorkflowDocument` 编译为唯一 `ExecutionPlan` |
| `workflow_validation.py`、`workflow_preflight.py` | 保存与执行前的结构、参数、设备可用性校验 |
| `command_runtime.py`、`command_catalog.py` | AI/技能命令的编排与可执行命令目录 |
| `camera_access.py` | 相机 lease、状态和受控帧访问 |
| `teleoperation.py`、`safety.py` | 遥操作控制租约和安全约束 |
| `balance.py`、`data_collection.py`、`external_localization.py` | 天平、数据采集和外部定位等专项应用用例 |

### 5.4 领域模型：`domain/`

| 文件 | 功能 |
|---|---|
| `models.py` | `Action`、`ActionType` 及动作公共模型 |
| `action_schema.py` | 动作参数的唯一 schema；GUI 表单、校验与 WebSocket 使用同一来源 |
| `workflow.py` | `WorkflowDocument` 与 Sequence、Action、Loop、Parallel、Subworkflow 等树结构 |
| `commands.py` | 面向编辑和执行的领域命令 |
| `execution_plan.py`、`execution_context.py` | 编译后的执行计划和一次运行的上下文 |
| `arm_names.py` | 左/右机械臂等稳定名称规范 |

### 5.5 统一执行运行时：`execution/`

| 文件/目录 | 功能 |
|---|---|
| `manager.py` | 单一执行状态机，管理启动、暂停、恢复、取消、事件和运行 ID |
| `engine.py` | 按 `ExecutionPlan` 调度步骤、循环、并行与终止语义 |
| `handler_registry.py`、`handler_api.py` | 将 `ActionType` 映射为 Handler，并统一设备错误规范化 |
| `handlers/` | `motion.py`、`manipulation.py`、`tooling.py`、`trajectory.py`、`vision.py`、`core.py` 等具体动作处理器 |
| `control.py`、`action_control.py` | 暂停、停止、取消等协作控制原语 |
| `workflows/` | 圆周注液、粉末分配等可复用复合执行算法 |

执行链路：

```text
WorkflowDocument → WorkflowCompiler → ExecutionPlan → ExecutionManager
→ HandlerRegistry → Action Handler → DeviceRuntime capability → Adapter / Driver
```

Handler 不直接导入厂商 SDK；设备失败会统一转换为带 `device_id`、操作名称、错误类别和
`run_id` 的诊断异常。

### 5.6 设备层：`devices/`

设备层按“稳定能力协议 → 运行时注册 → 厂商实现”分离：

```text
Application / Handler
       │ uses capability protocol
       ▼
DeviceRuntime ── Registration / Provider ── Adapter ── Driver / SDK / Transport
```

| 目录 | 功能 |
|---|---|
| `runtime/` | `DeviceRuntime`、设备 ID、注册、生命周期、能力协议、模拟实现与统一错误模型 |
| `robots/` | 机械臂注册与 provider；`realman/`、`tianji/` 分别封装厂商 SDK |
| `cameras/` | RealSense/OpenCV provider、相机 manager 与目录注册 |
| `motion/` | 底盘 TCP、升降平台 body axis、颈部 PWM 等运动设备 |
| `tools/` | 移液枪、快换手、继电器、粉末分配器及其驱动 |
| `sensors/` | 天平等传感器 provider |
| `transports/` | 可复用串口、Modbus RTU、TCP 传输、重试和测试替身 |
| `displays/` | T5L DGUSII 等显示设备与界面协议 |

新增同功能不同厂商设备时，应实现已有 capability contract 并在 `runtime/factory.py` 注册；
不修改 GUI、执行 Handler 或 Application Service。

### 5.7 持久化与用户数据：`persistence/` 与 `data/`

| 文件 | 功能 |
|---|---|
| `storage.py` | 动作库、工作流和草稿的 Repository，使用 revision 和原子写入 |
| `json_documents.py` | schema 文档读取、严格校验和原子 JSON 发布 |
| `trajectory_storage.py` | 按机械臂目录分配和读取轨迹文件 |
| `vision_station_storage.py` | 视觉工位/示教数据持久化 |

当前数据格式：

```text
data/
├── actions/library.json                 # 动作库 schema v2
├── workflows/<name>.workflow.json       # 正式任务 / 工作流 schema v2
├── drafts/<workflow-id>.draft.workflow.json
├── skills/<domain>/<id>.skill.json       # 单技能单文件 schema v2
├── trajectories/<left|right>/
├── vision/debug/<operation>/<run-id>/
└── schemas/                              # 编辑器可读取的 JSON Schema
```

工作流的 `root` 保存结构化控制流，`presentation` 保存画布位置等展示信息；运行状态
不会写进工作流文件。旧 `.task` / `.workflow` 数据只可经显式 CLI 迁移，运行时不会双读。

### 5.8 桌面 GUI：`gui/`

| 目录/文件 | 功能 |
|---|---|
| `controllers/main_window.py` | GUI 组合、启动 worker 生命周期、应用服务调用与主窗口协调 |
| `views/workbench/shell.py` | VS Code 风格工作台：活动栏、资源侧栏、编辑区、状态栏和详情浮层 |
| `views/workflow.py` | 工作流编辑器与顶部命令栏 |
| `views/workflow_canvas/` | 基于 Graphics View 的节点、连线、循环/并行容器、选择、拖放和插入预览 |
| `views/action_list.py`、`action_picker.py` | 基础动作库、类型筛选、新增/编辑/插入交互 |
| `views/dialogs.py` | schema 驱动的动作配置弹框、必填校验、实时位姿获取等通用表单能力 |
| `views/ai_assistant.py` | AI 聊天、技能预览、确认执行与语音交互视图 |
| `bridges/` | 将执行、组合和通知事件安全转为 Qt signal |
| `theme.py`、`icons.py`、`resources.qrc` | 深/浅/系统主题、SVG 图标、资源缓存和主题过渡 |
| `app_dialogs.py`、`about.py`、`window_chrome.py` | 跨平台弹框、关于窗口、自绘窗口外观与生命周期保护 |
| `shortcuts.py`、`menus.py`、`toolbars.py` | 统一命令、快捷键、菜单与图标按钮 |

GUI 只管理表现状态。设备连接、执行状态、文件写入和任务业务规则仍归属
`DeviceRuntime`、`ExecutionManager` 与 `CompositionService`。

### 5.9 WebSocket 服务：`robot_server/`

| 文件/目录 | 功能 |
|---|---|
| `ws_server.py` | 后台 asyncio WebSocket 宿主，与 GUI 同进程启停 |
| `protocol/messages.py` | 请求/响应/事件 payload schema |
| `protocol/routing.py` | action 名到 controller 的路由 |
| `controllers/` | 设备、执行、组合、AI 交互、遥操作等 API 控制器 |
| `security/` | 访问控制、传输安全和请求限额 |
| `metrics/` | WebSocket 连接、请求和事件指标 |

完整消息协议见 [WebSocket 接口手册](websocket-api.md)。HTTP 服务若新增，应复用
`ApplicationServices` 和这些应用用例，而不是复制 controller 业务逻辑。

### 5.10 AI、技能与语音

| 模块 | 功能 |
|---|---|
| `llm/registry.py` | provider 生命周期与注册表 |
| `llm/providers/` | OpenAI-compatible 和 MiniCPM Realtime 适配器 |
| `llm/tasks/` | 规划、分类、重复、视觉等任务的 Prompt/Profile/runner |
| `llm/routing.py` | 按 `[model_routing.<task>]` 选择推理与语音输出链路 |
| `skill_system/` | 从 `data/skills/<domain>/*.skill.json` 确定性加载、校验、匹配技能 |
| `voice_interaction/speech/` | 音频采集、ASR、VAD、唤醒词和输出门控 |
| `voice_interaction/core/` | 会话状态、命令路由、唤醒反馈与控制器 |
| `voice_interaction/adapters/` | 相机等外部能力的窄适配边界 |

模型部署配置属于 `[model_routing]`，`TaskProfile` 只描述 Prompt 和能力需求。这样同一个
任务可以使用“文字推理 + 独立 TTS”，或直接使用原生音频模型，而不把 provider 名称硬编码
进业务代码。

### 5.11 视觉、定位、几何和数据采集

| 模块 | 功能 |
|---|---|
| `vision/service.py` | 统一视觉服务、运行目录、结果和调试产物管理 |
| `vision/pipelines/` | 抓取、瓶体、垂直、坐标转换、采集等视觉算法管线 |
| `vision/relocalization/` | 示教工位、Marker 检测、手眼标定、运行时重定位补偿 |
| `vision/artifacts.py` | 可追踪的视觉输出和调试文件 |
| `localization/` | UDP 外部定位输入与 provider 模型 |
| `geometry/pose_compensation.py` | 不依赖硬件的位姿补偿纯计算 |
| `data_collection/` | 采集 episode、写入器、schema 和离线校验 |

相机只在 `[[vision.cameras]]` 目录中声明；`roles` 与 `arms` 决定视觉抓取、重定位、
天平等应用如何选取相机。详情见 [视觉架构](vision-architecture.md) 和
[配置说明](configuration.md)。

## 6. 常见业务链路

### 6.1 GUI 保存并执行工作流

```text
ActionLibrary / TaskLibrary / Canvas
  → WorkflowEditingSession 修改 WorkflowDocument
  → CompositionService 原子保存 *.workflow.json
  → WorkflowPreflight 校验参数、结构与设备
  → WorkflowCompiler 生成 ExecutionPlan
  → ExecutionManager 调度 Handler
  → DeviceRuntime 调用对应设备能力
  → ExecutionBridge 将进度/错误回送 GUI
```

### 6.2 WebSocket 执行同一任务

```text
WebSocket request → protocol routing → ExecutionController
→ ApplicationServices.execution_service → ExecutionManager
→ event subscription → WebSocket event
```

WebSocket 不维护第二份动作、任务或设备状态；它与 GUI 看到的是同一个 revision 和执行状态。

### 6.3 AI 辅助生成动作

```text
用户文字/语音 → CommandRuntime → SkillEngine 匹配候选技能
→ LLM task router 生成/校验动作序列 → GUI 或 API 预览
→ 用户确认 → ExecutionManager
```

### 6.4 视觉重定位

```text
视觉动作 Handler → VisionService → RelocalizationService
→ 按 arm + role 解析相机 profile 与标定 → 相机帧 / Marker 检测
→ Geometry 补偿目标位姿 → Motion capability 执行移动
```

若配置没有匹配机械臂和 `relocalization` role 的相机，系统会明确拒绝执行，不会回退到另一侧相机。

## 7. 扩展指南

### 新增动作

1. 在 `domain/models.py` 增加 `ActionType`。
2. 在 `domain/action_schema.py` 增加参数、默认值、必填规则和可选 UI 元数据。
3. 在 `execution/handlers/` 实现 Handler 并注册到 `handler_registry.py`。
4. 必要时在 `devices/runtime/contracts.py` 增加稳定能力协议。
5. 覆盖 schema、Handler、编译、GUI 表单和 WebSocket 回归测试。

不要在 GUI 新增平行参数定义；表单应自动消费 canonical schema。

### 新增设备或替换厂商

1. 选择或扩展稳定 capability protocol。
2. 在 `devices/<category>/<vendor>/` 实现 provider、adapter 和 driver。
3. 在 `devices/runtime/factory.py` 注册设备能力，并在 Settings 中增加显式 provider 选择。
4. 用 fake provider 做协议与异常归一化测试；再做真实硬件验收。

禁止让 GUI、应用服务或执行 Handler 直接导入厂商 SDK。

### 新增入口（例如 HTTP）

1. 在入口层解析协议 payload 和认证。
2. 调用已有 `ApplicationServices` 用例。
3. 将进度订阅映射为该协议的事件流。
4. 不创建新的 `DeviceRuntime`、`ExecutionManager` 或数据 Repository。

### 新增技能或模型路由

- 技能：在 `data/skills/<domain>/` 新建一个 `*.skill.json`，通过 schema 校验后重启或重载。
- 模型：在 `config.toml` 的 `[llm]`、`[model_routing.<task>]` 配置 provider、fallback 与输出模式；
  密钥只写 `.env`。

## 8. 维护边界与检查清单

| 变更类型 | 必须同步更新 |
|---|---|
| 配置字段 | Settings、TOML 示例、环境映射、校验测试、专题文档 |
| 用户数据 schema | JSON Schema、Repository、迁移 CLI、测试、数据治理文档 |
| 动作 | ActionType、canonical schema、Handler、编译/校验/协议测试 |
| 硬件能力 | Contract、provider/adapter/driver、factory、模拟与真实设备验收 |
| GUI 交互 | View/Controller、主题与无障碍、离屏 GUI 回归、GUI 架构文档 |
| WebSocket API | payload schema、controller、权限/限流、接口文档与契约测试 |

建议在提交前运行：

```powershell
uv sync --frozen --all-extras --group dev
uv run --frozen --group dev python scripts/run_quality_checks.py
```

质量门禁、测试范围和性能回归阈值见 [工程质量门禁](quality-gates.md)。

## 9. 相关文档索引

- [README](../README.md)：安装、配置和常用启动命令；
- [版本更新日志](../CHANGELOG.md)：按版本和发布日期维护的对外变更记录；
- [重构分支详细报告](refactor-changelog.md)：本分支的技术变更、不兼容迁移与待验收项；
- [配置说明](configuration.md)：配置来源、相机目录、模型路由与字段规则；
- [依赖、配置与用户数据治理](data-config-governance.md)：数据格式、迁移与持久化路径；
- [GUI 应用架构](gui-application-architecture.md)：桌面工作台状态所有权与交互边界；
- [执行运行时重构计划](execution-runtime-refactor-plan.md)：执行层演进记录；
- [机器人 Provider 架构](robot-provider-architecture.md)：多厂商机器人接入方式；
- [视觉架构](vision-architecture.md)：视觉服务、相机和算法边界；
- [语音交互实现](voice-interaction-implementation.md)：ASR、唤醒词与会话链路；
- [WebSocket 接口手册](websocket-api.md)：远程控制协议；
- [工程质量门禁](quality-gates.md)：静态检查、回归、打包与性能门禁。

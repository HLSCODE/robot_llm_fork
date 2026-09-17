# 代码结构与模块说明

> 文档类型：当前实现架构说明  
> 适用对象：开发、集成、测试与后续维护人员  
> 最近更新：2026-09-17
> 核对依据：当前源码、`pyproject.toml`、配置示例及版本库中的资源；临时目录不作为架构组成部分

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

### 3.1 源码、配置与随仓库发布的资源

| 路径 | 内容 | 使用方式 |
|---|---|---|
| `config/` | 入口 TOML、`fragments/` 和本机 `config.toml` | 非敏感部署配置；入口可 include 模块子配置，本机文件不提交 |
| `.env.example` | 密钥、Token 等环境变量模板 | 复制为 `.env` 后填写敏感信息 |
| `data/` | 按 Robot Profile 隔离的可执行数据，以及共享技能、视觉和采集数据 | 用户数据默认忽略；`data/regression/` 中指定的回归样例和性能预算提交版本库 |
| `docs/` | 架构、接口、配置、Provider、测试和重构文档 | 设计与维护依据 |
| `scripts/` | 质量检查、构建、迁移、可选能力验证脚本 | 本地与 CI 维护入口 |
| `src/` | 应用源码 | 按下文模块边界组织 |
| `tests/` | 单元、契约、集成、GUI 离屏和回归测试 | 验证功能与边界 |
| `third_party/wheels/` | 本地供应商 wheel，例如天机机械臂 SDK | 由 `pyproject.toml` 的本地 source 引用 |
| `pyproject.toml` | Python 元数据、可选依赖、CLI、Ruff/Mypy/Pytest 配置 | 依赖与工程工具的唯一声明源 |
| `uv.lock`、`.python-version` | 锁定依赖与 Python 环境版本 | CI 和本地使用锁文件复现依赖 |
| `assets/ref_audio/` | 随仓库提供的参考音频 | 与 GUI 的图标资源分开管理 |
| `models/kws/` | 唤醒词说明、词表及词表模板 | README、`keywords*.txt` 提交；模型权重不提交 |
| `.github/workflows/quality.yml` | Windows/Linux 自动化质量检查 | 与 `scripts/` 中的检查入口配合 |
| `README.md`、`CHANGELOG.md` | 使用入口、版本与日期管理 | 架构细节在 `docs/` 中维护 |
| `test_client.html`、`test_panel.html` | 浏览器端联调页面 | 调试服务接口，不属于桌面应用启动链路 |
| `test_devices.py` | 加粉装置 RS-485 硬件联调脚本 | 会操作真实设备，不等同于 `tests/` 自动化测试 |
| `udp-receiver.py` | 独立 UDP 接收调试脚本 | 排查外部数据输入，不由主程序自动启动 |
| `Robot Action Orchestrator_SRS.docx` | 需求说明资料 | 历史需求参考，当前实现以源码为准 |
| `CLAUDE.md`、`.agents/`、`.claude/`、`.trae/` | 开发辅助说明、工具配置及辅助资源 | 不是运行时模块；历史说明可能与当前目录不同 |

配置模板的实际组织方式：

```text
config/
├── config.example.toml
└── fragments/
    ├── application.example.toml
    ├── services.example.toml
    ├── ai.example.toml
    ├── robot.example.toml
    ├── robots/
    │   ├── realman.example.toml
    │   └── tianji.example.toml
    ├── mobile-base.example.toml
    ├── devices.example.toml
    └── voice.example.toml
```

`robot-config-init` 增量复制这些模板及 `.env.example`，生成同位置的实际配置，已存在的文件跳过。
模板提交版本库，实际 `config.toml`、子配置和 `.env` 不提交。

### 3.2 本机生成目录与资源归属

- `.venv/`、`.uv-cache/`、`__pycache__/`、各检查工具缓存、`robot_llm.egg-info/` 和覆盖率报告是环境或构建产物。
- `logs/` 保存应用日志及 `logs/crash/` 下的故障证据；原生 dump 可能含密钥和业务数据，不应提交。
- `models/` 的模型权重由模型初始化流程准备；不能因 `models/kws/` 有可提交词表而提交整个模型目录。
- 根目录偶尔出现的 `log/`、`tmp/`、`schemas/` 或临时 SDK 源码目录不代表新的业务模块。
  JSON Schema 的发布源是 `src/builtin_catalogs/schemas/`，用户侧副本位于数据根目录的 `schemas/`。
- GUI 图片与 SVG 位于 `src/gui/assets/`，通过 Qt 资源文件打包；不要与根目录 `assets/` 混淆。

### 3.3 命令与依赖入口

以下命令由 `pyproject.toml` 注册，可通过 `uv run <命令>` 调用：

| 命令 | 实现入口 | 用途 |
|---|---|---|
| `robot-llm` | `bootstrap.launcher:main` | 主应用与配置检查 |
| `robot-init` | `bootstrap.initialization_cli:main` | 交互式或命令行分步初始化；`migrate-data` 显式迁移数据 |
| `robot-config-init` | `configuration.config_initializer:main` | 仅初始化配置模板 |
| `robot-models-init` | `bootstrap.initialization_cli:models_main` | 单独准备语音/唤醒模型 |
| `robot-library-data` | `bootstrap.catalog_cli:main` | 动作库和技能数据工具 |
| `robot-workflow-data` | `bootstrap.workflow_cli:main` | 工作流数据工具 |
| `robot-data-validate` | `data_collection.validation:main` | 校验采集数据 |
| `robot-llm-regression` | `llm.regression:main` | LLM 规划回归 |
| `t5l-dgus` | `devices.displays.t5l_dgusii.cli:main` | 显示设备调试 |

上表入口均位于 `src.` 包下。Python 最低版本为 3.12；依赖按 `gui`、`server`、`ai`、
`data`、`vision`、`hardware`、`voice`、`kws` 等 extra 安装，`openwakeword` 当前是空的兼容分组。
天机 SDK 通过平台标记选择 Windows x86_64 或 Linux x86_64 wheel；更换 wheel 时还需同步锁文件，
不能只覆盖文件后继续使用旧哈希。详见 [供应商 wheel 说明](../third_party/wheels/README.md)。

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
| `observability/` | 日志、执行上下文、Python/原生崩溃诊断 | 关联 `run_id`，记录异常堆栈、进程与线程证据 |
| `builtin_catalogs/` | 随应用发布的动作、技能和 JSON Schema 资源 | 显式内置能力、编辑器 schema 支持；不自动填充用户数据 |

## 5. 核心模块与关键文件

### 5.1 启动与应用组合：`bootstrap/`

| 文件 | 功能 |
|---|---|
| `launcher.py` | `robot-llm` 主入口；解析参数，加载 Settings，启动 GUI 与附加服务，并负责退出顺序 |
| `auxiliary_services.py` | 管理 WebSocket 等附加服务，避免其阻塞 Qt 主线程 |
| `catalog_cli.py` | 动作库/技能库校验与迁移命令 |
| `workflow_cli.py` | 旧任务向 `*.workflow.json` 的显式迁移工具 |
| `initialization.py` | 初始化计划、步骤执行、子进程管理和取消；包括配置、数据迁移、依赖、模型与校验 |
| `initialization_cli.py`、`initialization_tui.py` | 命令行入口与 Textual 全屏交互；选择步骤、查看可折叠详情与执行结果 |

启动流程：

```text
CLI → Settings / 启动选项 → 配置校验 → Qt 宿主与启动界面
    → 后台初始化应用服务 / 设备 → 主工作台与附加服务
    → 安全停止 worker → 关闭附加服务 → 关闭设备运行时
```

### 5.2 配置：`configuration/`

| 文件 | 功能 |
|---|---|
| `settings.py` | 冻结的类型化设置模型；包含 Runtime、Data、Robot、Vision、LLM、Voice 等分组 |
| `config_loader.py` | 组合 TOML、`.env` 和系统环境变量，生成 `ApplicationSettings`；CLI 启动选项由 launcher 处理 |
| `toml_source.py` | 入口 include、子配置合并、schema 和未知字段检查 |
| `environment.py` | 受支持环境变量的映射和优先级 |
| `data_paths.py` | 从 `[data]` 和 Robot Profile 推导动作、工作流、草稿、技能与轨迹路径 |
| `config_validation.py` | 启动前业务规则校验，例如端口、相机角色、硬件参数和安全配置 |
| `config_initializer.py` | 增量复制入口、子配置及环境变量模板，不覆盖已有文件 |
| `robot_profile.py` | Profile ID 的规范化和 provider/model 派生规则 |

设置优先级由低到高为：类型默认值 → 按 include 顺序合并子配置 → 入口 TOML → `.env` → 已有系统环境变量。
CLI 仅对支持的启动选项进行覆盖，不是任意 Settings 字段的通用覆盖器。

当前入口示例使用 `schema_version = 6`。可把所有字段放在入口，也可使用相对于入口目录的
`include` 拆分；子配置不能再声明 `include` 或 `schema_version`，不支持递归 include。
文件名不决定配置归属，例如 `robot.toml` 可包含其他合法配置节。

`[robot]` 保存通用选择和运动设置，`[robot_providers.realman]` / `[robot_providers.tianji]`
分别描述厂商差异；切换 `robot.provider` 不会转换已有动作。LLM 则由 `[llm]`、
`[llm_providers.<id>]` 和 `[model_routing.<task>]` 分别管理策略、实例和任务路由。
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
| `services.py` 中的 `TrajectoryTeachingService` | 统一轨迹录制、结束、保存与取消用例，调用厂商录制能力和轨迹仓储 |
| `builtin_data.py` | 创建空用户数据容器并发布 JSON Schema，不把内置示例自动填进动作库 |
| `robot_profile_migration.py`、`action_catalog_normalization.py` | 显式数据迁移和动作参数规范化；不是启动时自动修改旧数据的入口 |

### 5.4 领域模型：`domain/`

| 文件 | 功能 |
|---|---|
| `models.py` | `ActionDefinition`、`ActionType`、`SequenceItem` 及动作公共模型，包括 `robot_profile_id` |
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

机械臂 provider 由 `robots/registry.py` 解析，`runtime/factory.py` 负责组装运行时。
`runtime/arm_models.py` 描述机械臂能力和目标语义；不能把七关节角与六维笛卡尔位姿视作同一种参数。

`robots/realman/recording.py` 与 `robots/tianji/recording.py` 分别适配示教和录制流程。
GUI 不直接调用厂商的拖动示教、采集或保存接口；回放仍走统一 trajectory Handler。
新增同能力厂商实现时，优先复用现有 contract，避免把厂商分支扩散到表现层。

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
├── profiles/<robot-profile-id>/
│   ├── actions/library.json
│   ├── workflows/<name>.workflow.json
│   ├── drafts/<workflow-id>.draft.workflow.json
│   └── trajectories/<left|right>/       # 录制目录/文件，具体格式由 provider 决定
├── skills/<domain>/<id>.skill.json      # 共享技能目录
├── vision_stations/profiles.json        # 默认视觉示教工位文档
├── vision/debug/                        # 视觉运行与调试产物
├── demos/                               # 默认示教数据集目录，不是动作回放轨迹目录
├── regression/                          # 已纳入版本库的回归样例与性能预算
└── schemas/                             # 从内置资源发布的 JSON Schema
```

工作流的 `root` 保存结构化控制流，`presentation` 保存画布位置等展示信息；运行状态
不会写进工作流文件。旧 `.task` / `.workflow` 数据只可经显式 CLI 迁移，运行时不会双读。

Profile 默认由 provider 和 model 派生，例如 `realman-rm75-dual`、`tianji-tianji-dual`，
也可通过 `robot.profile_id` 显式指定。目录隔离和文档中的 `robot_profile_id` 同时参与约束；
不同 Profile 的动作、工作流不能直接混用，`unscoped` 不是绕过校验的通配符。

上图是默认布局，不是所有路径都跟随 `robot_data_dir`：动作等路径由 `ApplicationDataPaths`
解析；视觉调试、工位和采集目录有各自的配置字段。显式相对数据路径覆盖按项目根目录解析，
不能理解为相对于 Profile 目录。技能目录共享也不代表其中引用的机器人动作可跨 Profile 执行。

正常启动负责加载和校验，不自动迁移旧文档。缺失数据初始化为空容器，不复制示例动作/技能。
需要升级旧数据时执行 `uv run robot-init migrate-data`；该步骤协调 Profile 数据、旧工作流和
视觉工位迁移。迁移前应备份原数据，具体格式规则见 [数据治理](data-config-governance.md)。

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
| `controllers/startup.py`、`views/startup.py` | 启动进度、后台初始化与启动结果展示 |
| `controllers/trajectory_dialog.py`、`recording_operation.py` | 轨迹录制对话框状态机和后台操作 worker 生命周期 |
| `view_models/`、`workbench_layout.py` | 展示数据模型、工作台布局状态 |
| `drag_preview.py`、`drag_preview_style.py` | 拖拽缩略图及主题适配 |
| `application_lifecycle.py`、`screen_geometry.py`、`diagnostics.py` | Qt 关闭顺序、屏幕几何安全读取、窗口/屏幕诊断 |

GUI 只管理表现状态。设备连接、执行状态、文件写入和任务业务规则仍归属
`DeviceRuntime`、`ExecutionManager` 与 `CompositionService`。

屏幕位置/尺寸通过 `screen_geometry.py` 集中查询并返回矩形值，不让业务弹框长期持有屏幕对象。
后台工作完成后通过 Qt signal 回到 GUI 线程更新界面；关闭窗口时仍需等待、取消或回收其 worker，
不能把 Python 异常保护当作原生对象生命周期管理的替代品。

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

模型地址、类型、凭据变量引用属于 `[llm_providers.<id>]`；默认与容错策略属于 `[llm]`；
任务选择属于 `[model_routing.<task>]`。`TaskProfile` 只描述 Prompt 和能力需求。这样同一个
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

启动/重新检测使用 `CameraAccessService.probe_all()` 的健康检查路径：后台逐台检测，
记录状态和原因，结束后释放测试管线；业务任务再通过 lease 按需开启相机，短期复用并在空闲后关闭。
健康检查不是多画面预览或深度对齐流程。视觉执行预检应按动作实际使用的角色/机械臂检查相机，
而不是因为无关的可选相机离线就阻止执行。

### 5.12 日志与故障诊断：`observability/`

| 入口 | 用途 |
|---|---|
| `logging_config.py` | 终端摘要与文件日志，异常堆栈和执行上下文记录 |
| `crash_diagnostics.py` | faulthandler、结构化诊断事件和进程/线程信息 |
| 根目录 `scripts/watch_native_crash.ps1` | 使用 ProcDump 捕获 Windows 原生崩溃 dump |
| 根目录 `scripts/watch_qt_screens.ps1` | 屏幕生命周期原生跟踪，支持探针、模拟、硬件或附加进程 |
| 根目录 `scripts/debugger/`、`scripts/probes/` | 调试器命令与独立 Qt 屏幕探针 |

普通 Python 错误先查应用日志；访问冲突等原生崩溃需结合 `logs/crash/` 的诊断文件与 dump。
诊断脚本不属于正常启动的必经步骤；硬件模式会涉及真实设备，不能当成无副作用测试。
具体命令和证据说明见 [崩溃诊断](crash-diagnostics.md)。

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

### 6.5 轨迹录制与回放

```text
GUI 录制对话框 → 后台录制操作 → TrajectoryTeachingService
→ provider recorder 开始 / 结束录制 → TrajectoryStorage 保存录制结果
→ 命名并创建轨迹动作 → CompositionService 保存动作库
→ 工作流执行 → trajectory Handler → 对应 provider 回放
```

SDK 落盘成功、轨迹动作创建成功、回放成功是三个不同阶段。录制目录可能有原始采样、
左右臂轨迹等多个文件，应由 provider 的录制结果确定可回放文件，而不是让 GUI 猜文件名。
上图对应创建轨迹动作的录制入口；仅保存录制文件的入口不一定包含命名和创建动作步骤。

### 6.6 初始化与日常启动的区别

`robot-init` 的计划按配置、数据迁移、依赖、ASR/VAD 模型、KWS 模型、校验的固定顺序执行
所选步骤；Textual 负责选择和进度呈现，Runner 负责实际操作。已有配置跳过复制，模型准备
检查本地文件，数据迁移为显式步骤。日常 `robot-llm` 启动不承担依赖安装、模型准备或旧数据迁移。

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
3. 在相应 provider 注册表及 `devices/runtime/factory.py` 的装配边界接入，并在 Settings 中增加显式选择。
4. 用 fake provider 做协议与异常归一化测试；再做真实硬件验收。

禁止让 GUI、应用服务或执行 Handler 直接导入厂商 SDK。

### 新增入口（例如 HTTP）

1. 在入口层解析协议 payload 和认证。
2. 调用已有 `ApplicationServices` 用例。
3. 将进度订阅映射为该协议的事件流。
4. 不创建新的 `DeviceRuntime`、`ExecutionManager` 或数据 Repository。

### 新增技能或模型路由

- 技能：在 `data/skills/<domain>/` 新建一个 `*.skill.json`，通过 schema 校验后重启或重载。
- 模型：在入口或已 include 的子配置中，用 `[llm_providers.<id>]` 定义实例，
  用 `[llm]`、`[model_routing.<task>]` 选择默认 provider、fallback 与输出模式；
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
uv run --no-sync python scripts/run_quality_checks.py
```

这里先同步锁定的完整开发环境，再用 `--no-sync` 运行，避免第二条命令改变已安装的 extra。
日常安装按使用场景选择 extra，不必为了使用主程序安装全部能力。
`scripts/validate_optional_extra.py` 检查各 extra 的独立可用性；
`validate_package.py` 检查发布包；`run_performance_benchmarks.py` 执行性能回归。
Linux GUI 测试还需要 Qt 对应系统库，Python extra 不能替代这些系统依赖。

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
- [天机机器人接入](tianji-robot-provider.md)：SDK、运动目标、录制与回放的厂商边界；
- [视觉架构](vision-architecture.md)：视觉服务、相机和算法边界；
- [语音交互实现](voice-interaction-implementation.md)：ASR、唤醒词与会话链路；
- [WebSocket 接口手册](websocket-api.md)：远程控制协议；
- [遥操作](teleop.md)、[数据采集](data-collection.md)：控制租约、采集会话与数据校验；
- [LLM Provider 治理](llm-provider-governance.md)：实例、路由与能力配置；
- [日志说明](logging.md)、[崩溃诊断](crash-diagnostics.md)：日志位置、线程与原生故障证据；
- [GUI 资源](gui-assets.md)、[模拟测试](gui-simulation-testing.md)：资源维护与无硬件 GUI 验证；
- [性能基准](performance-benchmarks.md)：性能检查入口与预算；
- [工程质量门禁](quality-gates.md)：静态检查、回归、打包与性能门禁。

# 重构分支详细报告

> 范围：`location-tag` → `refactor`  
> 汇总日期：2026-08-19  
> 提交数量：119  
> 说明：本文是面向发布、集成和维护人员的重构摘要；逐项任务状态以
> [项目重构总计划](project-refactor-master-plan.md) 为准。按版本和日期维护的发布记录见
> 根目录 [CHANGELOG.md](../CHANGELOG.md)。

## 总览

本分支完成了从“功能分散、入口和硬件控制并行实现”到“单一应用宿主、统一执行运行时、
统一设备运行时、版本化用户数据和工作台 GUI”的系统性收敛。

```text
GUI / WebSocket / Voice / AI
              │
      ApplicationServices
       ├── CompositionService      动作与工作流
       ├── ExecutionManager        唯一执行状态机
       ├── CommandRuntime / LLM    技能与自然语言命令
       └── DeviceRuntime           设备实例与生命周期
              │
   Provider → Adapter → Driver → SDK / Hardware
```

以下变更均已切换到新实现，不保留长期双栈兼容入口。

## 主要变更

### 1. 应用架构与执行运行时

- GUI、WebSocket、AI 和语音入口统一复用一份 `ApplicationServices`、
  `ExecutionManager` 与 `DeviceRuntime`；WebSocket 变为随 GUI 启停的附加服务。
- 删除旧 GUI/Server 各自执行器和并行设备创建路径，工作流只通过唯一
  `ExecutionManager` 执行。
- 动作执行迁移为 `ActionHandlerRegistry` + 独立 Handler：移动、执行器、换枪、轨迹、
  视觉与通用动作均使用同一注册与错误处理机制。
- 引入 `ExecutionPlan`，支持递归 Sequence、Loop、Parallel、Subworkflow 编译；
  并行分支会在执行前校验设备资源冲突，失败时统一取消并汇合终态。
- 统一暂停、恢复、取消、快速停止和设备急停链路；执行事件、WebSocket 事件和日志使用
  `run_id` 关联。
- 新增 Workflow Validator、Preflight 与 Compiler，分别负责结构/参数校验、执行前
  无副作用检查和执行计划编译。

### 2. 设备运行时与硬件接入

- 所有设备实现物理收敛到 `src/devices/`，删除旧 `device_runtime`、`arm_sdk`、
  `base_move` 等平铺目录和导入转发。
- 建立厂商无关能力协议、`DeviceRuntime`、Provider Registry、Adapter/Driver 分层，
  GUI 和执行 Handler 不再直接导入厂商 SDK。
- RealMan 机械臂切换到 Provider / Adapter / Driver 架构，并增加动作失败诊断：执行模式、
  目标位姿、当前位姿和供应商返回码可进入统一错误链路。
- 新增天机双机械臂 Provider，使用本地 wheel 作为可选硬件依赖，并保持与 RealMan 相同的
  机械臂能力契约。
- 底盘、升降平台、颈部 PWM、继电器、快换手、移液枪、粉末分配器、天平、相机和显示设备
  均纳入统一设备注册、资源租约、超时和关闭生命周期。
- 串口/Modbus/TCP 传输抽取到共享 Transport 层；移动底盘 TCP 增加连接、收发超时配置。
- 遥操作、轨迹录制、数据采集和序列执行统一共享设备资源租约，避免并发抢占设备。

### 3. 工作流、动作与用户数据

- 任务正式格式统一为 `data/workflows/<name>.workflow.json`；旧 `.task` 与旧
  `.workflow` 仅支持通过 `robot-workflow-data` 显式迁移，不再在运行时双读或隐式迁移。
- 工作流从扁平动作列表升级为结构化文档，保存 Sequence、Loop、Parallel、Subworkflow
  与画布展示元数据；执行状态不再写入任务文件。
- 原任务组合页和第二套组合状态删除。流程 + 动作 + 流程统一通过内嵌 Subworkflow 在同一
  画布编辑，支持进入/退出子流程、递归编辑与整棵文档 Undo/Redo。
- 动作库迁移为 `data/actions/library.json`，强制稳定 ID、唯一名称、类型和参数校验；
  动作参数统一由 `domain/action_schema.py` 驱动。
- 技能库迁移为 `data/skills/<domain>/<id>.skill.json` 的单技能单文件目录，确定性扫描、
  完整校验后原子替换 Registry。
- 新增工作流草稿、revision 冲突检查、原子写入和 JSON Schema；轨迹录制统一保存到
  `data/trajectories/<arm>/`，视觉调试产物统一默认保存到 `data/vision/debug/`。

### 4. GUI 工作台与编辑体验

- Qt 绑定从 PyQt6 直接切换为 PySide6，移除 PyQt6/SIP 依赖及许可证风险。
- 旧纵向堆叠 GUI 重构为工作台：Activity Bar、资源侧栏、主编辑画布、状态栏和右下非模态
  详情浮层；任务、基础动作和 AI 助手成为独立资源页。
- 工作流画布基于 Graphics View 重写，支持节点选择、多选、双击编辑、右键菜单、拖拽排序、
  动作/任务外部拖入、循环、并行、子流程、Undo/Redo、缩放、平移与适应内容。
- 拖拽统一使用轻量 ghost 缩略图、原位占位、二维插入点解析和脉冲提示；Esc、失焦、失去
  鼠标抓取和取消共享幂等清理路径，避免残留预览或误排序。
- 工作流执行期间当前节点、循环、并行分支显示状态；安全控制保持常驻，不因面板收起而隐藏。
- 引入统一 SVG 图标、图标缓存、Tooltip、主题令牌和快捷键注册表；图标、菜单、工具栏、
  下拉框、数值控件、表单校验和通知不再依赖平台原生样式差异。
- 新增深色、浅色和跟随系统主题；主题切换支持缓存样式、请求合并和局部扩散动画，降低低配
  设备上的阻塞感。
- 引入跨平台 `AppDialog`、自绘菜单、窗口外观与启动卡片，修复 Windows/Linux 原生菜单、
  标题栏、弹框、字体和窗口销毁差异。
- AI 助手、设备状态、位姿、日志和错误摘要迁移为按需面板/浮层；状态栏只保留计数与入口，
  不再常驻挤压画布。

### 5. 配置系统与相机目录

- `config.env` 迁移为严格 TOML：`config/config.toml` 是非敏感配置，`.env` 只保存密钥与
  临时部署覆盖；Settings 在启动时一次解析为不可变对象。
- 配置 schema 升级并清理废弃字段；未知字段、错误类型和不安全配置会在启动前失败，避免
  运行时静默回退。
- 新增 `[data]` 配置，集中指定动作、工作流、草稿、技能、轨迹及运行产物的根目录和覆盖路径。
- LLM 任务路由迁移为 `[model_routing.<task>]`：推理 provider、fallback、输出模式、
  独立 TTS provider 和语音 fallback 可配置；`TaskProfile` 不再硬编码厂商。
- 相机配置迁移为 `[[vision.cameras]]` 目录，使用稳定名称、显示标签、provider、设备 ID、
  role、机械臂范围及标定数据作为唯一事实来源。
- 相机 role 明确区分 `vision_capture`、`robot_grasp`、`balance`、`relocalization`；指定
  机械臂时不再跨臂回退，缺失相机或标定会明确拒绝执行。
- OpenCV 相机不能配置为需要深度帧的 `robot_grasp`；视觉抓取、重定位、天平和 GUI 下拉选择
  均改从相机目录解析。

### 6. AI、技能与语音

- LLM Provider Registry 统一接管 OpenAI-compatible、DashScope、DeepSeek、MiniCPM
  Realtime 等 provider 的创建和关闭。
- 自然语言入口从“自动执行”改为“命令/技能匹配 → 预览 → 用户确认 → 执行”的受控流程。
- 新增强类型 CommandCatalog 和 CommandRuntime，覆盖执行控制、夹爪、相对移动、底盘移动、
  动作、技能和工作流命令；歧义或越界输入不会由 LLM 猜测执行。
- 语音输出支持仅文字、原生音频、文字后 TTS 三种路由；推理模型与语音模型可以独立替换。
- 唤醒欢迎行为名称统一为“唤醒欢迎工作流”，不再沿用旧 task 配置命名。
- 语音会话、ASR、VAD、KWS、音频输出和相机访问改为明确生命周期，关闭时减少 async generator
  与 Qt worker 的资源竞争。

### 7. 视觉、定位、数据采集与可观测性

- 视觉能力收敛到 `VisionService`，抓取、瓶体、垂直、坐标、采集和重定位管线拆分为单责模块。
- 视觉重定位恢复机械臂选择、动作模式动态字段、工位和相机选择；示教/运行模式使用同一
  canonical schema 与相机目录校验。
- 外部 UDP 定位迁移为独立 Provider 和 Application Service；几何位姿补偿保持为纯计算模块。
- 数据采集升级为版本化 schema、事务写入、容量预检、损坏恢复与完整性验证，并支持双臂
  遥测和相机元数据。
- 日志与审计增加结构化字段、运行上下文、WebSocket request/run 关联、遥操作审计、LLM/
  视觉/设备指标和可配置日志保留。

### 8. 质量、测试与发布

- 建立统一质量门禁：编译、Ruff、Mypy、Pytest、LLM golden regression、性能回归、wheel
  构建和隔离安装 smoke。
- Mypy 从历史基线 570 errors / 72 files 收敛为所有手写 `src/` 与 `scripts/` 文件 0 errors；
  仅排除 Qt 自动生成的 `src/gui/resources_rc.py`。
- 增加设备 Provider、执行策略、工作流结构/并行、GUI 离屏、主题、画布拖放、相机配置、
  视觉算法、LLM 路由和可选依赖的专项回归。
- GUI 启动、初始化卡片、线程关闭和无屏幕场景增加生命周期保护，避免销毁仍在运行的 Qt 对象。
- README、配置、数据治理、GUI、视觉、机器人 Provider、质量门禁和代码结构文档同步更新。

## 不兼容变更与迁移要求

| 原行为/格式 | 当前行为 | 处理方式 |
|---|---|---|
| PyQt6 | PySide6 | 重新同步依赖；生产代码不保留 PyQt6 适配层 |
| `config.env` | `config/config.toml` + `.env` | 从示例创建 TOML；密钥迁入 `.env` |
| 多个旧相机字段 | `[[vision.cameras]]` | 按相机 profile 配置 role、arms 与标定 |
| `.task` / 旧 `.workflow` | `*.workflow.json` | 执行 `robot-workflow-data --apply` 显式迁移 |
| 单个动作/技能集合文件 | `actions/library.json` 与 `skills/<domain>/*.skill.json` | 执行 `robot-library-data migrate` |
| 旧任务组合器 | 内嵌 Subworkflow + 唯一画布 | 从任务库打开或插入子流程进行编辑 |
| 旧设备直连模块 | `DeviceRuntime` Provider/Adapter/Driver | 新设备按 capability contract 接入 |
| TaskProfile 内 provider | `[model_routing.<task>]` | 在 TOML 中配置推理与语音输出路由 |

迁移工具默认 dry-run；正式写入前会进行完整解析、指纹/语义校验、临时目录生成和原子发布。
运行时不会读取旧格式，避免长期维护双格式分支。

## 仍需真实环境验收

软件结构与模拟/离屏门禁已完成，但以下事项必须在受控真实环境中验收，不能由单元测试替代：

- RealMan 与天机机械臂的连接、左右臂 IP/臂名映射、关节/笛卡尔运动、停止、快速停止和
  设备急停响应时间；
- 底盘、升降平台、夹爪、移液枪、快换手、继电器、加粉装置和显示屏的真实协议、限位和安全态；
- RealSense/OpenCV 的设备枚举、深度帧、手眼标定、视觉抓取和视觉重定位精度；
- 真实串口、TCP 网络抖动、断线重连、SDK 原生阻塞调用的超时与退出表现；
- 麦克风、扬声器、ASR/KWS、MiniCPM Realtime 及外部 LLM provider 的现场可用性；
- 真实工位下的遥操作、资源租约、数据采集偏差和安全恢复流程。

对于不提供原生取消能力的厂商 SDK / 相机 SDK，当前退出策略为“有界宽限期后安全 join”，
不会强制终止仍在使用原生资源的线程；若需要严格硬超时，应由供应商提供取消 API，或将驱动
隔离到独立可终止进程。

## 推荐阅读顺序

1. [README](../README.md)：安装、配置和启动；
2. [代码结构与模块说明](codebase-architecture.md)：目录、边界和典型链路；
3. [配置说明](configuration.md) 与 [数据治理](data-config-governance.md)：部署和数据迁移；
4. [GUI 应用架构](gui-application-architecture.md)：桌面工作台与状态所有权；
5. [机器人 Provider 架构](robot-provider-architecture.md)、[视觉架构](vision-architecture.md)、
   [LLM Provider 治理](llm-provider-governance.md)：各专项扩展；
6. [项目重构总计划](project-refactor-master-plan.md)：剩余真实验收与长期 backlog。

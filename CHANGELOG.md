# Changelog

本文件记录面向使用者、集成方和发布人员的重要版本变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。每次发布前将
`Unreleased` 中已确认的内容归入带发布日期的版本，并创建新的 `Unreleased` 区段。


## [Unreleased] - TBD

### Added

- 新增基于 Textual 全屏模式的极简 `robot-init` 逐步向导，支持全键盘选择、执行 Spinner、默认折叠的步骤详情以及失败详情自动展开。
- 新增 `robot-models-init`，可独立准备 ASR/VAD 与 KWS 模型，并支持无交互初始化。
- 初始化语音模型前检查本地缓存，完整存在时跳过下载；ASR 准备在可取消子进程中执行，`Ctrl+C` 可安全停止初始化。

- 配置 schema v5 支持入口 TOML 通过 `include` 加载模块子配置，同时保留完整单文件模式。
- 配置覆盖顺序固定为子配置声明顺序、入口配置、环境变量，并为路径越界、重复引用和非法子配置提供明确校验。
- 新增 `robot-config-init`，从入口、子配置和 `.env` 示例增量创建本机配置，已有文件始终跳过。
- 默认配置解析不再只依赖进程工作目录，并在启动日志中列出实际加载的入口与全部子配置。
- LLM Provider 从全局 `[llm]` 策略中抽离为 `[llm_providers.<id>]` 实例目录，模型路由按实例 ID 引用，并按 `kind` 复用 Provider 适配器。

## v2.0.0 - 2026-08-19

### Added

- 单一应用宿主：GUI、WebSocket、AI 与语音入口复用同一套应用服务、设备运行时和执行运行时。
- 结构化工作流编辑与执行：支持 Sequence、Loop、Parallel、Subworkflow、草稿、revision、
  Undo/Redo、执行前校验和统一执行计划。
- VS Code 风格 PySide6 工作台：资源侧栏、工作流画布、SVG 工具栏、状态栏详情浮层、
  集中快捷键和深色/浅色/跟随系统主题。
- 多厂商机械臂 Provider 架构：已接入 RealMan 与天机机械臂，并可按能力协议扩展后续供应商。
- 统一 `DeviceRuntime`、设备能力协议、Provider/Adapter/Driver 分层，以及串口、Modbus、TCP
  Transport 复用层。
- `[[vision.cameras]]` 相机目录、视觉抓取、视觉重定位、相机角色与标定校验。
- 强类型 LLM 路由：可分别配置推理模型、原生音频或独立 TTS 模型及 fallback。
- 显式数据迁移 CLI、动作/工作流/技能 JSON Schema、轨迹存储、视觉调试产物目录和配置校验。
- 统一质量门禁：Ruff、Mypy、Pytest、LLM golden regression、性能回归和 wheel smoke。

### Changed

- GUI 从 PyQt6 切换为 PySide6，消除 PyQt6 许可证风险。
- 任务正式格式统一为 `data/workflows/<name>.workflow.json`，动作库统一为
  `data/actions/library.json`，技能库统一为 `data/skills/<domain>/<id>.skill.json`。
- 配置从 `config.env` 切换为 `config/config.toml`；`.env` 仅保存 API Key、Token 等敏感信息和
  临时环境覆盖。
- 相机、视觉抓取、重定位、天平等功能改用集中相机目录解析，不再使用分散的旧相机配置字段。
- AI 指令从自动执行改为“匹配/规划 → 预览 → 用户确认 → 执行”的受控流程。
- 任务组合功能收敛为画布中的内嵌 Subworkflow，删除独立任务组合页和第二套编辑状态。
- 轨迹录制与视觉调试产物默认保存到 `data/`，不再写入 GUI 源码目录或要求每次手工选择路径。

### Fixed

- 统一设备错误、执行事件、日志与 WebSocket 审计中的设备 ID、操作名、运行 ID 和供应商错误码。
- 修复工作流画布的嵌套选择、Loop/Parallel 编辑、拖拽预览、插入提示、子流程导航和主题刷新问题。
- 修复 Windows/Linux 下原生菜单、标题栏、弹框、字体、主题和窗口销毁差异，改用一致的 Qt 客户区组件。
- 修复视觉重定位按机械臂选择相机、相机标定缺失、相机角色不匹配和视觉调试路径不一致的问题。
- 修复启动卡片、附加服务、异步 provider、GUI worker 和设备线程在关闭阶段的生命周期竞态。
- 修复 AI/语音模型路由、规划响应参数校验和执行前设备不可用时的错误反馈。

### Removed

- 移除旧的 GUI/Server 平行执行器、旧设备直连模块、旧任务组合器、旧 PyQt6 依赖和历史导入转发。
- 移除运行时对旧 `.task`、旧 `.workflow`、旧动作/技能集合格式和旧 `config.env` 的兼容读取。
- 移除 MiniCPM 指令分类的历史配置与代码，统一使用模型路由和命令运行时。

### Migration notes

- 旧任务执行 `robot-workflow-data --apply` 迁移；旧动作/技能执行
  `robot-library-data migrate` 迁移。工具默认先 dry-run，迁移后会进行解析和语义校验。
- 从 `config/config.example.toml` 创建新的 `config/config.toml`，将 API Key 和 Token 放入 `.env`。
- 将旧视觉相机字段迁移为 `[[vision.cameras]]` profile；真实重定位必须填写对应机械臂的
  `relocalization` role 与完整标定数据。

### Known limitations

- 真实硬件连接、运动范围、快停/急停延迟、视觉标定精度、串口/TCP 现场稳定性和语音设备仍需
  在受控现场完成验收。
- 不支持原生取消的厂商 SDK / 相机 SDK 不能通过强制杀线程实现硬超时；当前采用安全 join，
  严格硬超时需要供应商取消 API 或独立驱动进程。

# Robot LLM 项目重构总计划

> 文档状态：Active  
> 创建日期：2026-07-27  
> 最近更新：2026-07-29
> 当前里程碑：M1/M2 — 核心 Runtime 已落地，入口迁移与硬件验收进行中  
> 维护方式：本文件作为项目级重构总入口；专项设计和实施细节通过关联文档维护

## 1. 文档定位

本文档覆盖当前项目评审中已经识别出的全部主要重构域，用于统一管理优先级、跨模块依赖、里程碑、验收和技术决策。

本文档是“项目级总计划”，不替代专项设计文档。复杂领域应在实施前建立或更新专项计划，并从本文件链接：

| Track | 专项文档 |
|---|---|
| A. 执行运行时与安全 | [统一执行运行时重构实施计划](execution-runtime-refactor-plan.md) |
| B. 机械臂供应商适配 | [机械臂供应商适配架构](robot-provider-architecture.md) |
| C. WebSocket API | [WebSocket API 文档](websocket-api.md) |
| E. LLM 能力层 | [LLM 重构集成说明](llm-refactor-integration.md) |
| E. 语音交互 | [语音交互实现说明](voice-interaction-implementation.md) |
| F. 遥操作 | [遥操作说明](teleop.md) |
| F. 数据采集 | [数据采集说明](data-collection.md) |
| F. 智能加粉 | [智能闭环加粉 Agent](powder_dispense_agent.md) |

本计划覆盖的是当前仓库可确认的问题。后续发现的新问题应进入对应 Track 的 backlog，不能因为没有列在首版文档中而被忽略。

## 2. 项目现状摘要

项目目前已经具备完整的机器人应用雏形：

- PyQt6 本地 GUI。
- WebSocket 远程控制服务。
- 双机械臂、底盘、身体升降、夹爪、吸液枪、换枪、加粉装置和表情屏。
- RealSense/OpenCV 相机、视觉抓取和视觉重定位。
- LLM provider、自然语言意图、技能规划、语音唤醒、ASR/VAD/KWS/TTS。
- 遥操作与 RLBench 数据采集。
- 动作库、任务库、技能库和本地配置。

当前主要问题不是“功能不存在”，而是功能快速增长后缺少统一的运行时所有权和工程边界。首轮收敛已经将主要入口切换为：

```text
GUI / WebSocket / Voice / AI
              |
      ApplicationServices
       /       |         |          \
Execution  Device   Teleoperation   Safety
 Service   Service     Service       Service
    |         |           |            |
ExecutionManager    ResourceArbiter     |
    |                         |         |
ActionEngine -------- DeviceRuntime ----+
                              |
                  contracts / adapters / fakes
                              |
                 concrete SDK / protocol drivers
```

已完成的直接切换：

- 删除 `src/gui/execution.py` 和 `src/robot_server/action_executor.py`，不保留导入转发或双后端开关。
- GUI、AI 和 WebSocket 序列执行共用一个进程级 `ExecutionManager`。
- launcher 只创建一份 `ApplicationServices`，设备实例只由 `DeviceRuntime` 创建和关闭。
- 机械臂、身体、底盘、继电器、快换手、移液枪、加粉、相机和表情屏已注册为设备能力。
- 遥操作持有会话级资源租约，与序列执行互斥。
- 相机测试、语音视觉、视觉抓取和重定位不再自行创建或关闭相机。
- 相机 manager 的旧全局单例已删除，实例只由 `DeviceRuntime` 持有。
- 已删除无引用的 legacy GUI、旧底盘控制器和独立 ADP 控制脚本。
- 已建立厂商无关的机械臂模型与能力接口，RealMan 仅在 adapter/driver 边界内出现。
- GUI、执行引擎、视觉、重定位和数据采集均已切换到统一机械臂能力。
- 轨迹示教和遥操作持有完整会话周期的机械臂资源租约。
- 受控取消、软件快停和软件急停已由 `SafetyService` 统一编排；GUI 与 WebSocket
  共用逐设备结果，当前 RealMan 停止链路待真实硬件验收。
- 已删除无引用的 RealMan 直连动作脚本，不保留兼容转发。
- `ActionHandlerRegistry` 已成为唯一动作类型分发入口，全部现有 `ActionType`
  在执行引擎构造时完成注册完整性校验；`WAIT`、`INSPECT` 已迁出引擎。
- 所有动作已接入统一硬 deadline、暂停和协作取消上下文；阻塞 SDK 调用返回前
  不释放执行资源，设备级即时停止能力仍需逐路径补齐并完成硬件验收。

仍需继续收敛的重点：

- `src/robot_server/ws_server.py`
- `src/gui/main_window.py`
- `src/gui/dialogs.py`
- `src/execution/engine.py`
- `src/core/config_loader.py`
- `src/arm_sdk/controller.py` 的厂商驱动内部职责拆分和错误模型。
- 第二种机械臂供应商 adapter 的接入与真实硬件契约验收。

## 3. 总体目标

### 3.1 架构目标

1. GUI、WebSocket、语音和 AI 只是入口与展示层。
2. 应用服务负责用例编排，不直接依赖具体 UI 或传输协议。
3. 执行运行时是唯一动作序列所有者。
4. 设备运行时是唯一硬件生命周期和资源状态所有者。
5. LLM 和技能系统只产生经过验证的计划，不直接驱动硬件。
6. 遥操作和数据采集服从统一资源与安全仲裁。
7. 配置、任务、技能和标定数据具有明确 schema、版本和交付策略。
8. 所有关键状态转换、协议和领域算法具备自动化测试。
9. 项目可以在无硬件环境下通过 simulation/fake 完成主要流程回归。
10. 真实硬件行为有审计、错误分类和安全停止能力。

### 3.2 质量目标

- 新增一个 ActionType 不再修改 GUI 和 Server 两套执行器。
- 新增一个 WebSocket action 不需要继续扩大单一 Server 类。
- 设备初始化、关闭、重连和健康状态只有一个可信来源。
- 执行、AI、相机和语音任务均支持明确取消和最终结果。
- 错误不会被静默吞掉或错误映射成成功。
- 默认配置错误可以在启动阶段发现。
- 代码合并前自动执行测试、lint 和基础类型检查。
- 当前架构、目标架构和用户操作文档分开维护。

### 3.3 非目标

- 不一次性重写所有硬件 SDK。
- 不要求第一阶段引入微服务。
- 不要求第一阶段把本地文件存储改成数据库。
- 不为追求目录整洁而改变已经稳定的设备协议。
- 不在没有硬件验证的情况下重写机械臂底层运动算法。
- 不把所有实时遥操作消息持久化为普通动作序列。
- 不默认实施《项目综述》中尚未落地的四 Agent 目标方案；该方案需要单独立项。

## 4. 目标架构

```text
                         Presentation / Transport
            +---------------+---------------+---------------+
            |               |               |               |
           GUI          WebSocket         Voice          CLI/Tools
            |               |               |               |
            +---------------+-------+-------+---------------+
                                    |
                           Application Services
            +-----------------------+-----------------------+
            |                       |                       |
        TaskService          CommandRuntime          DeviceService
            |                       |                       |
            +--------------- ExecutionService --------------+
                                    |
                            ExecutionManager
                  state / run_id / result / event / cancel
                                    |
                         ActionHandlerRegistry
                                    |
            +-----------------------+-----------------------+
            |                       |                       |
          Motion                Manipulation          Domain Flows
      arm/base/body        gripper/pipette/tool     vision/powder/etc.
            |                       |                       |
            +------------ Resource & Safety Layer ----------+
                                    |
                      DeviceRuntime / Capabilities
                                    |
             arm_sdk / device_control_sdk / cameras / display

     LLMRegistry -> VoiceInteraction -> SkillEngine
                                   |
                         validated ExecutionPlan
                                   |
                           approval / policy
                                   |
                           ExecutionService

     Teleoperation ----------------> Resource & Safety Layer
     Data Collection -------------> Teleop/Device events
```

### 4.1 分层规则

| 层 | 可以依赖 | 禁止依赖 |
|---|---|---|
| Presentation/Transport | Application service、DTO | 具体硬件 SDK、动作执行细节 |
| Application Service | 领域接口、runtime 接口、repository | Qt 控件、websocket 实例 |
| Execution/Domain | handler、typed model、device capability | GUI、WebSocket、LLM provider |
| Runtime/Infrastructure | 设备 SDK、文件、网络、串口 | GUI 业务状态 |
| LLM/Interaction | task profile、skill interface | 直接硬件控制器 |

## 5. 优先级定义

| 优先级 | 定义 | 处理规则 |
|---|---|---|
| P0 | 可能造成硬件冲突、错误成功状态、未授权控制、数据损坏或阻断核心重构 | 进入其他大规模功能开发前处理 |
| P1 | 核心架构收敛和主要可靠性能力 | 当前主线里程碑完成 |
| P2 | 可维护性、扩展性、数据治理和工程效率 | P1 稳定后分批实施 |
| P3 | 长期演进、体验、性能和文档清理 | 按收益持续推进 |

## 6. Track A：执行运行时与任务控制

专项计划：[统一执行运行时重构实施计划](execution-runtime-refactor-plan.md)

### 6.1 目标

- 建立进程内唯一 `ExecutionManager`。
- 引入正式状态机、`run_id`、事件和最终结果。
- 使用 `ActionHandlerRegistry` 消除两套执行实现。
- GUI、AI、WebSocket 和 voice command 共享执行入口。
- simulation 与真实执行共享状态和事件协议。
- 区分 pause、cancel、quick-stop 和 emergency-stop。

### 6.2 项目级任务

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| A-001 | P0 | DONE | 修复 AI 将“线程启动成功”当作“最终执行成功” |
| A-002 | P0 | DONE | 阻止 GUI 手工和 GUI AI 两个执行线程并发 |
| A-003 | P0 | DONE | 修复 stop 后立即 disconnect 设备的竞态 |
| A-004 | P0 | DROPPED | 不保留旧执行链路；改为新 runtime 单元和边界测试 |
| A-005 | P1 | DONE | 实现 execution models/events/handle |
| A-006 | P1 | DOING | 实现唯一 ExecutionManager；待硬件验收和更多竞态测试 |
| A-007 | P1 | DOING | ActionHandlerRegistry 已成为唯一分发入口，WAIT/INSPECT 已拆分；待迁移运动、操作、视觉和领域 flow handler |
| A-008 | P1 | DOING | WebSocket 已迁移，待协议 contract test |
| A-009 | P1 | DOING | GUI 手工和 AI 已迁移，待 GUI smoke test |
| A-010 | P1 | DOING | GUI/网络入口语音命令已进入统一服务，待 CommandRuntime 完整状态模型 |
| A-011 | P1 | DONE | simulation 与真实模式共用状态机和执行入口 |
| A-012 | P3 | DONE | 删除 legacy executor，未引入 backend 开关 |
| A-013 | P1 | DONE | GUI 成为唯一桌面应用宿主，附加网络服务与 GUI 共用 ApplicationServices，退出顺序由组合根统一管理 |

### 6.3 完成标准

- `src/execution/` 是唯一动作序列执行实现。
- 每个 run 有唯一终态。
- GUI、AI、WS、voice command 不直接执行 ActionType。
- 新增动作只注册一次。
- 暂停、取消、失败和成功在所有入口语义一致。

## 7. Track B：设备运行时、硬件 SDK 与安全

### 7.1 当前问题

- GUI 与 WebSocket 已共用同一个 `DeviceRuntime`，但真实设备仍需逐项验证统一关闭顺序。
- 部分业务路径仍需确认没有绕过 Application Service 访问 runtime-owned 设备。
- `src/devices/` 与 `src/device_control_sdk/` 使用不同抽象风格。
- 串口设备有的每次动作创建，有的长期持有，所有权不清晰。
- 设备连接状态可能只表示“控制器对象存在”，不表示设备真实在线。
- 错误返回混合使用 bool、字符串、异常和打印日志。
- 部分阻塞 SDK 无法及时响应取消。
- 颈部控制器已初始化但没有形成完整动作链路。

### 7.2 目标

- 引入 `DeviceRuntime` 作为设备实例、健康状态和关闭顺序的唯一所有者。
- 引入 capability 模型，区分设备存在、连接、就绪、故障和模拟。
- 引入 `ResourceArbiter`，统一序列、遥操作、测试和直接控制的资源租约。
- 定义安全停止接口和设备恢复策略。
- 新设备优先采用 `device_control_sdk` 的 Transport/Protocol/Device 分层。
- 厂商协议只能出现在 driver/adapter 层，上层统一使用项目级能力接口。
- 核心机械臂能力与可选能力分离，供应商不必伪造不支持的功能。

### 7.3 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| B-001 | P0 | DONE | 已定义 cancel、quick-stop、emergency-stop 语义、结果状态和逐设备能力矩阵；真实硬件验收由 ER-006 跟踪 |
| B-002 | P0 | DOING | 序列、遥操作和直接控制增加资源互斥 |
| B-003 | P0 | DONE | 设备关闭前取消执行并等待资源释放 |
| B-004 | P1 | DONE | 建立 DeviceRuntime 和 DeviceSnapshot |
| B-005 | P1 | DOING | 主要入口已统一初始化/重连/关闭，待真实设备逐项验收 |
| B-006 | P1 | DONE | 定义设备 capability/state/error |
| B-007 | P1 | DOING | 已建立统一动作 deadline 与 cooperative cancel 契约；待补齐各阻塞 SDK 的设备级停止能力和硬件验证 |
| B-008 | P2 | TODO | 统一串口 transport 生命周期 |
| B-009 | P2 | DONE | relay/tool-changer/pipette 及机械臂 adapter 已落地，视觉不再依赖厂商对象 |
| B-010 | P2 | TODO | 评估并接入 PWM neck 动作能力 |
| B-011 | P2 | TODO | 统一硬件错误码和用户消息 |
| B-012 | P3 | TODO | 清理未使用的设备封装和实验脚本 |
| B-013 | P0 | DONE | 定义厂商无关的机械臂运动、状态、夹爪及可选能力协议 |
| B-014 | P1 | DONE | RealMan adapter 接入统一运行时，业务层移除 `rm_*` 和原生控制器访问 |
| B-015 | P1 | TODO | 接入第二种机械臂 adapter，并通过同一套硬件契约测试 |
| B-016 | P1 | TODO | 将工具架点位和厂商/型号差异改为 provider 配置 |
| B-017 | P1 | TODO | 定义继电器、快换手、移液枪等非连续运动输出的安全态和停机策略 |

### 7.4 完成标准

- 设备状态有唯一可信来源。
- 同一设备不能被两个业务流同时控制。
- shutdown 有明确顺序、超时和结果。
- 硬件错误能够关联设备、操作、run_id 和原始错误。
- simulation device 与真实 device 暴露相同 capability 接口。

## 8. Track C：WebSocket API、安全与服务拆分

### 8.1 当前问题

- 已改为默认监听 `127.0.0.1`，但显式开放远程监听时仍缺少认证、权限、Origin 限制和服务端 TLS。
- 多客户端共享同一序列、预览和设备状态，没有控制权租约。
- 执行事件缺少 `request_id`、`run_id` 和稳定错误码。
- 大部分事件广播给所有客户端，发起者和观察者边界不清晰。
- `RobotWebSocketServer` 同时处理执行、任务、AI、相机、聊天、设备、遥操作和数据采集。
- handler 内部混合协议解析、业务逻辑、线程创建和硬件调用。
- 聊天协议已经明确存在不支持并发请求的限制。

### 8.2 目标

- 建立认证、授权和控制权租约。
- 定义 API version、请求关联、错误码和幂等策略。
- 将协议 DTO/validation 与 application service 分离。
- 按 execution、task、device、camera、chat、teleop、data collection 拆分 handler。
- 明确单播、广播和订阅事件。
- 增加限流、消息大小、背压和慢客户端处理策略。

### 8.3 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| C-001 | P0 | TODO | 增加写操作认证 |
| C-002 | P0 | TODO | 建立客户端控制权租约 |
| C-003 | P0 | TODO | 为执行和设备写操作增加审计 |
| C-004 | P1 | TODO | 引入 request_id/run_id |
| C-005 | P1 | TODO | 统一错误响应和错误码 |
| C-006 | P1 | TODO | 定义 API version 和破坏性变更策略 |
| C-007 | P1 | TODO | 限制消息大小、频率和并发 |
| C-008 | P1 | TODO | 明确事件单播/广播/订阅语义 |
| C-009 | P2 | TODO | 拆分领域 handler 和 service |
| C-010 | P2 | TODO | 使用 typed request/response DTO |
| C-011 | P2 | TODO | 增加 Origin/TLS/反向代理部署方案 |
| C-012 | P2 | TODO | 增加协议 contract tests |
| C-013 | P3 | TODO | 增加 API 指标和慢客户端监控 |
| C-014 | P1 | DONE | WebSocket 改为 GUI 同进程的可选附加服务，共用唯一 ApplicationServices 和受管理 asyncio 生命周期 |
| C-015 | P1 | DONE | 动作库、任务库和当前编排序列已收敛到线程安全 CompositionService；JSON 原子替换并向 GUI/网络入口发布 revision 事件 |

### 8.4 完成标准

- 未授权客户端不能执行、初始化、断开或遥操作设备。
- 每个写操作能够关联用户/客户端、request_id 和结果。
- 多客户端不能无提示抢占控制权。
- Server 类只负责连接生命周期和顶层路由。
- 当前协议行为和版本由自动化 contract test 保证。

## 9. Track D：GUI 应用架构

### 9.1 当前问题

- `MainWindow` 同时管理 UI、设备、编排、执行、任务和相机测试。
- `dialogs.py` 集中维护大量动作类型表单和参数构建。
- `widgets` 与 GUI 业务状态耦合，部分 AI 组件反向持有 MainWindow。
- 手工执行和 AI 执行有两套状态。
- 部分按钮直接调用硬件控制器。
- 窗口关闭可能无超时等待执行线程。
- GUI 启动过程使用嵌套 `QEventLoop` 等待语音初始化，启动状态复杂。

### 9.2 目标

- MainWindow 只负责窗口组合、导航和顶层 Qt 生命周期。
- 引入应用服务或 view-model/state model 管理任务、设备和执行状态。
- Qt worker 只承担线程适配，不承载领域执行。
- 动作表单由统一 action schema 驱动。
- AI、语音、手工控制共享同一状态源。
- 启动和关闭流程可观测、有超时、可取消。

### 9.3 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| D-001 | P0 | DONE | GUI 手工/AI 共用进程级执行互斥 |
| D-002 | P1 | DONE | ExecutionBridge 已成为纯 Qt 事件 adapter |
| D-003 | P1 | DONE | ExecutionBridge 不再拥有序列 worker；安全停止仅用短生命周期 I/O 调度线程避免阻塞 Qt 主线程 |
| D-004 | P1 | DONE | MainWindow 不再关闭设备；应用宿主先停附加服务，再统一关闭 DeviceRuntime |
| D-005 | P2 | TODO | 提取 TaskComposerService |
| D-006 | P2 | TODO | 提取 DeviceViewModel/Service |
| D-007 | P2 | TODO | 提取 ExecutionViewModel |
| D-008 | P2 | TODO | action schema 驱动通用表单 |
| D-009 | P2 | DONE | 生命周期、手动控制、轨迹示教和位姿读取已进入 Application Service |
| D-010 | P2 | TODO | 重构启动初始化状态机 |
| D-011 | P3 | TODO | 统一 UI 状态、错误和通知组件 |
| D-012 | P3 | TODO | 增加 GUI 自动化 smoke tests |

### 9.4 完成标准

- MainWindow 不拥有硬件和业务执行实现。
- GUI 只有一个执行状态源。
- 新动作不需要在多个 GUI 映射表重复登记。
- UI 主线程不执行阻塞操作。
- 启动、执行和关闭均有明确状态和错误反馈。

## 10. Track E：LLM、语音交互与技能系统

### 10.1 当前优势

- `src/llm/` 已有 provider 抽象、registry、task runner 和 capability。
- `src/voice_interaction/` 已拆分 session、router、speech runtime 和 camera adapter。
- `src/skill_system/` 已具备技能注册、规划展开和基础校验。

### 10.2 当前问题

- GUI 创建了语音和文本两个 `VoiceInteractionController`，会话上下文边界不清晰。
- auto execute 配置存在，但当前执行行为没有完整接入。
- session pause 和 execution pause 容易混淆。
- 预览没有统一 `preview_id`、版本和过期策略。
- SkillEngine 对未知 action type 静默回退为 MOVE。
- 技能参数只做少量手工映射，缺少强类型、单位、范围和安全规则。
- provider/task 的取消、超时和资源关闭策略不完全统一。
- Prompt、模型配置和规划结果缺少版本化回归基线。

### 10.3 目标

- 统一 GUI 文本和语音会话策略，明确是否共享历史。
- 所有模型任务支持取消、超时、结构化错误和指标。
- 技能规划产出 typed `ExecutionPlan`。
- 规划、审批和执行严格分层。
- 高风险动作支持明确审批策略。
- 技能、prompt、provider 和模型版本可追踪。

### 10.4 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| E-001 | P0 | TODO | 禁止未经校验的 auto execute |
| E-002 | P1 | TODO | Voice CommandRuntime 接入 |
| E-003 | P1 | TODO | 区分 session control 和 execution control |
| E-004 | P1 | TODO | 预览增加 preview_id、版本和过期状态 |
| E-005 | P1 | TODO | 未知 action type 改为显式失败 |
| E-006 | P1 | TODO | 技能参数接入统一 action schema |
| E-007 | P1 | TODO | 定义高风险动作审批策略 |
| E-008 | P2 | TODO | 明确 GUI 文本/语音会话共享策略 |
| E-009 | P2 | TODO | 统一 LLM task 的 timeout/cancel/close |
| E-010 | P2 | TODO | provider health 和降级策略 |
| E-011 | P2 | TODO | prompt、模型和技能版本记录 |
| E-012 | P2 | TODO | 建立分类、规划和技能回归数据集 |
| E-013 | P3 | TODO | LLM 延迟、token、失败率和成本指标 |

### 10.5 完成标准

- LLM 只能产生计划，不能绕过审批/策略直接调用硬件。
- 相同文本在 GUI、语音和 WebSocket 中产生一致 command 行为。
- 未知动作和非法参数不会进入执行层。
- provider 失败有明确降级或用户可理解错误。
- 规划回归可以通过固定样例自动验证。

## 11. Track F：视觉、相机、遥操作、数据采集与领域流程

### 11.1 视觉与相机

当前问题：

- 相机生命周期和全局单例问题已收敛到 `DeviceRuntime`；实时预览、
  语音视觉、抓取、重定位和测试仍缺少细粒度 session/资源租约。
- 标定、工位和模型文件缺少统一版本元数据。
- `core.move_compensation` 反向依赖 `gui.udp_receive`。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-V-001 | P1 | TODO | 相机接入 ResourceArbiter |
| F-V-002 | P1 | DOING | 相机生命周期已归 DeviceRuntime，待细粒度 session/租约 |
| F-V-003 | P1 | TODO | 移除 core 对 gui.udp_receive 的依赖 |
| F-V-004 | P2 | TODO | 建立 VisionService 和 typed result |
| F-V-005 | P2 | TODO | 模型、标定、工位配置版本化 |
| F-V-006 | P2 | TODO | 统一调试图片和临时文件生命周期 |
| F-V-007 | P2 | TODO | 视觉 pipeline 的 simulation/fixture |
| F-V-008 | P3 | TODO | 性能、帧率、延迟和模型指标 |

### 11.2 遥操作

当前问题：

- 遥操作直接调用 RobotController。
- 与序列执行的互斥只分散在 WebSocket handler 中。
- 高频消息缺少明确背压、超时和控制租约。
- 网络断开后的安全行为需要统一定义。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-T-001 | P0 | DONE | 遥操作会话租约与序列执行互斥 |
| F-T-002 | P0 | DOING | 断线释放租约和 RealMan 软件快停/急停链路已实现；心跳超时、其他设备停止和硬件验收待完成 |
| F-T-003 | P1 | DOING | 已建立 TeleoperationService，会话状态和所有者仍需增强 |
| F-T-004 | P1 | TODO | 增加控制租约和心跳 |
| F-T-005 | P1 | TODO | 增加消息频率、背压和丢帧策略 |
| F-T-006 | P2 | TODO | 遥操作事件和错误统一审计 |
| F-T-007 | P3 | TODO | 延迟、抖动和吞吐基准 |

### 11.3 数据采集

当前问题：

- 采集状态集中在 WebSocket Server。
- 采集会话、episode、机械臂状态和遥操作关系耦合。
- 缺少磁盘容量、原子落盘、失败恢复和 schema 版本治理。
- `rlbench` 缺失时使用简化结构，交付差异需要明确。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-D-001 | P1 | TODO | 提取 DataCollectionService |
| F-D-002 | P1 | TODO | 建立 session/episode 状态机 |
| F-D-003 | P1 | TODO | 采集与遥操作共享控制会话 |
| F-D-004 | P2 | TODO | 数据 schema 和版本元数据 |
| F-D-005 | P2 | TODO | 原子落盘、恢复和磁盘容量检查 |
| F-D-006 | P2 | TODO | 明确完整 RLBench 与简化格式差异 |
| F-D-007 | P2 | TODO | 数据完整性验证工具 |

### 11.4 智能加粉领域

当前代码只有一个规则型 `PowderDispenseAgent`；`docs/项目综述.md` 描述的是未来四 Agent + LLM 动态决策架构，两者不能视为同一实现。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-P-001 | P0 | TODO | 明确“最大轮次仍返回成功”的业务语义 |
| F-P-002 | P1 | TODO | 加粉流程接入统一取消、安全返回和执行结果 |
| F-P-003 | P1 | TODO | 记录每轮读数、动作、阈值和结果 |
| F-P-004 | P2 | TODO | 为规则策略建立离线回归测试 |
| F-P-005 | P2 | TODO | 区分当前规则 Agent 与未来四 Agent 方案文档 |
| F-P-006 | P3 | TODO | 评审是否立项 LLM 多 Agent 粉末流程 |

## 12. Track G：配置、数据、依赖、测试与交付工程

### 12.1 配置

当前问题：

- `Config` 超过 1000 行，所有配置集中在全局单例。
- 数值转换和默认值分散，部分错误会导致整个配置加载失败。
- 配置项、模板和实际消费位置容易漂移。
- 配置包含硬件、模型、服务、语音和标定等不同生命周期数据。

目标：

- 分为 `ServerSettings`、`RobotSettings`、`DeviceSettings`、`VisionSettings`、`LLMSettings`、`VoiceSettings`。
- 启动时集中校验，输出可理解错误。
- 业务模块接收配置快照，不依赖全局可变单例。
- 敏感配置与普通配置分离。

### 12.2 数据与持久化

当前问题：

- `data/` 被整体 `.gitignore`，默认动作、技能和任务无法可靠交付。
- JSON/`.task` 文件无 schema version、锁和原子写。
- 标定、调试图片、任务和用户数据没有明确目录策略。

目标：

- 区分版本控制中的 built-in/example data 与运行时 user data。
- 引入 schema version、迁移、备份和原子写。
- 明确数据根目录，支持部署时配置。

### 12.3 依赖与打包

当前问题：

- `requirements.txt` 与 `pyproject.toml` 不完全一致。
- `pyrealsense2` 在 requirements 中重复声明。
- GUI、Server、视觉、语音和硬件依赖未充分拆分。
- `dev` 依赖为空。
- `run.py` 不是标准安装后的项目命令。

目标：

- 以 `pyproject.toml` 为唯一依赖源。
- 按 `server/gui/vision/voice/hardware/dev` 拆分 extra。
- 提供标准 CLI entry point。
- 锁文件与支持平台策略清晰。

### 12.4 测试与 CI

当前问题：

- 没有 pytest 测试套件。
- 没有 CI、lint、类型检查和覆盖率配置。
- `test_devices.py` 等主要是硬件联调脚本。
- 大量硬件代码难以在无设备环境导入和测试。

目标：

- 建立 unit、contract、simulation integration、hardware acceptance 四层测试。
- fake transport/device/camera/LLM 成为正式测试基础设施。
- CI 至少执行 compile、pytest、ruff 和核心类型检查。
- 硬件验收独立运行，不阻塞普通 CI。

### 12.5 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| G-001 | P0 | TODO | 引入 pytest 和最小 CI |
| G-002 | P0 | DOING | 已建立 fake device/runtime，transport/LLM fake 待补充 |
| G-003 | P0 | TODO | 确定默认技能和动作数据交付方式 |
| G-004 | P1 | DOING | 已覆盖 runtime、资源、执行状态和依赖边界，协议测试待补充 |
| G-005 | P1 | TODO | 增加 ruff 和基础类型检查 |
| G-006 | P1 | TODO | 统一依赖声明并清理重复依赖 |
| G-007 | P1 | TODO | 配置启动校验和敏感信息策略 |
| G-008 | P1 | TODO | 持久化原子写、版本和迁移 |
| G-009 | P2 | TODO | 配置按领域拆分 |
| G-010 | P2 | TODO | optional dependency 分组 |
| G-011 | P2 | TODO | 标准 CLI 和打包验证 |
| G-012 | P2 | TODO | 覆盖率目标和质量门禁 |
| G-013 | P2 | TODO | Windows/Linux 测试矩阵 |
| G-014 | P2 | TODO | 日志轮转、结构化日志和 run_id |
| G-015 | P3 | TODO | 性能基准和回归监控 |

## 13. 跨 Track 关键决策

以下决策需要在 M0/M1 期间确认：

| ADR | 状态 | 决策主题 | 默认建议 |
|---|---|---|---|
| ADR-M-001 | Accepted | 单进程还是拆服务 | 保持单进程模块化，暂不微服务化 |
| ADR-M-002 | Accepted | 唯一执行所有者 | 使用进程级 ExecutionManager |
| ADR-M-003 | Accepted | 设备所有权 | 使用 DeviceRuntime |
| ADR-M-004 | Accepted | 资源冲突 | 使用显式资源租约，冲突立即拒绝 |
| ADR-M-005 | Accepted | 内部迁移策略 | 直接切换，不保留 legacy/v2 双实现、转发模块或兼容开关 |
| ADR-M-006 | Accepted | simulation | 替换设备实现，不替换状态机 |
| ADR-M-007 | Proposed | 配置模型 | typed settings + 启动校验 |
| ADR-M-008 | Proposed | 数据目录 | built-in data 与 user data 分离 |
| ADR-M-009 | Proposed | GUI 状态管理 | application service + Qt adapter/view-model |
| ADR-M-010 | Proposed | 四 Agent 粉末方案 | 作为独立立项，不纳入基础重构默认范围 |

ADR 状态：`Proposed`、`Accepted`、`Superseded`、`Rejected`。

## 14. 里程碑与依赖关系

```text
M0 安全与基线
  ├─ A: 结果语义、临时互斥、disconnect 竞态
  ├─ B: 停止能力矩阵、资源冲突规则
  ├─ C: 认证和控制权最低方案
  └─ G: pytest、fake、CI
       |
       v
M1 核心 Runtime
  ├─ ExecutionManager
  ├─ DeviceRuntime
  ├─ ResourceArbiter
  └─ typed state/event/result
       |
       v
M2 入口迁移
  ├─ WebSocket adapter
  ├─ GUI adapter
  ├─ AI/Voice CommandRuntime
  └─ Teleop resource integration
       |
       v
M3 领域与数据收敛
  ├─ action schema
  ├─ vision/camera sessions
  ├─ data collection service
  ├─ powder workflow
  └─ storage/config versioning
       |
       v
M4 工程治理与清理
  ├─ 拆分 god classes
  ├─ 删除 legacy
  ├─ packaging/platform matrix
  ├─ observability/performance
  └─ documentation alignment
```

### 14.1 M0：安全与工程基线

完成条件：

- P0 执行结果问题修复。
- GUI 手工/AI 不可并发。
- stop/disconnect 不再关闭正在使用的资源。
- 遥操作和序列冲突有最低保护。
- WebSocket 写操作有最低认证方案。
- pytest、fake infrastructure 和 CI 可运行。
- 默认数据交付方案确定。

### 14.2 M1：核心 Runtime

完成条件：

- ExecutionManager、DeviceRuntime、ResourceArbiter 可独立测试。
- 状态、事件、结果、错误和 capability 模型稳定。
- simulation 覆盖核心 handler。
- 未接入入口时即可通过完整核心测试。

### 14.3 M2：入口迁移

完成条件：

- Server、GUI、AI、voice command 使用统一执行。
- teleop 使用统一资源仲裁。
- 新协议和事件契约测试通过。
- 仓库中不存在 legacy backend 或切换开关。

### 14.4 M3：领域与数据收敛

完成条件：

- action/skill/schema 一致。
- 相机资源、视觉结果和标定数据版本化。
- 数据采集独立于 WebSocket Server。
- 配置、任务和技能数据具备版本与迁移。

### 14.5 M4：工程治理与清理

完成条件：

- legacy 执行器删除。
- MainWindow 和 RobotWebSocketServer 完成职责拆分。
- 依赖、打包、测试矩阵和日志治理完成。
- 文档与实现一致。

## 15. 质量门禁

### Gate 0：任何代码变更

- `python -m compileall src`
- 新增代码无明显循环依赖。
- 不提交密钥、真实设备凭据和用户运行数据。
- 相关文档同步更新。

### Gate 1：核心 Runtime 合并

- unit tests 通过。
- 状态机合法/非法转换全覆盖。
- 取消、异常和并发提交测试通过。
- 不依赖 Qt、WebSocket 或真实硬件。

### Gate 2：入口迁移

- WebSocket contract tests 通过。
- GUI simulation smoke tests 通过。
- 当前协议 contract tests 通过。
- 多客户端控制冲突测试通过。

### Gate 3：真实硬件发布

- simulation 全流程通过。
- 硬件验收清单完成。
- stop/quick-stop/emergency-stop 分别验证。
- 配置、固件、模型、标定版本记录。
- 回滚方案演练通过。

## 16. 直接切换、迁移与回滚

### 16.1 直接切换原则

- 不保留 legacy/v2 两套实现。
- 不创建旧模块导入转发、旧配置别名或后端选择开关。
- 被替换的内部入口在同一变更中迁移全部引用并删除。
- 外部 API、任务或配置如发生破坏性调整，直接更新 schema/version、调用方和文档。
- 回滚依赖版本控制和已验证发布版本，不依赖运行时双栈。

### 16.2 迁移原则

1. 先定义最终接口、依赖边界和验收测试。
2. 在一个可验证变更中迁移对应入口。
3. 引用清零后立即删除旧实现。
4. 更新 schema、文档和启动组装。
5. 通过 simulation 后进入真实硬件验收。

### 16.3 回滚要求

- 每个里程碑使用可独立回滚的提交/PR。
- 数据格式变更必须先备份，并提供向前迁移脚本。
- 硬件发布前保留已验证版本。
- 回滚不能同时启动新旧两套控制进程。

## 17. 文档治理

### 17.1 文档类型

| 类型 | 用途 | 示例 |
|---|---|---|
| Master Plan | 项目级优先级与里程碑 | 本文件 |
| Track Plan | 专项目标、阶段和验收 | execution runtime plan |
| Current Architecture | 描述当前真实实现 | 后续新增 |
| ADR | 记录关键技术决策 | 第 13 节或独立 ADR |
| API/Operation Guide | 面向调用者和运维 | websocket-api、teleop |
| Future Proposal | 尚未实施的目标方案 | 四 Agent 粉末方案 |

### 17.2 规则

- 文档必须标注是“当前实现”还是“目标方案”。
- 代码目录或公开接口变化时同步更新文档。
- 过期文档应删除或标记 superseded，不保留多个互相冲突的入口。
- README 只保留稳定的运行和导航信息，详细设计放在 docs。

## 18. 风险登记

| 风险 | 等级 | 影响 | 缓解 |
|---|---|---|---|
| 新旧执行器同时控制硬件 | P0 | 设备冲突、人身/设备风险 | 单一 composition root、进程锁、资源租约 |
| 软件 stop 被误认为急停 | P0 | 停止不及时 | 能力分层、UI/API 明确、硬件验证 |
| WebSocket 未授权控制 | P0 | 任意网络客户端操作设备 | 认证、权限、控制租约、TLS |
| 迁移时行为漂移 | P1 | GUI/WS 功能回归 | 最终接口测试、simulation、硬件验收 |
| 阻塞 SDK 无法取消 | P1 | shutdown 卡住 | timeout、quick-stop、隔离 worker |
| 配置/任务格式损坏 | P1 | 无法启动或数据丢失 | schema version、原子写、备份 |
| simulation 与真实行为偏差 | P1 | 测试假阳性 | 共享状态机/校验，硬件验收 |
| GUI 线程错误 | P1 | 崩溃或界面异常 | Qt adapter、主线程更新规则 |
| 相机/串口资源泄漏 | P1 | 后续任务不可用 | DeviceRuntime、session、finally/shutdown |
| 文档目标与当前实现混淆 | P2 | 错误决策 | 文档分类、状态和 superseded 标记 |
| 重构范围过大 | P2 | 长期分支、难以合并 | 按能力域拆分，每个域直接切换并删除旧实现 |

## 19. 项目级完成定义

全部满足后，本轮项目重构才可标记完成：

### 架构

- [ ] GUI、WebSocket、voice 和 AI 不直接操作具体动作实现。
- [ ] ExecutionManager 是唯一序列执行所有者。
- [ ] DeviceRuntime 是唯一硬件生命周期所有者。
- [ ] teleop、sequence、测试和直接控制服从资源仲裁。
- [ ] runtime/core 不反向依赖 GUI 或 WebSocket。

### 安全与协议

- [x] cancel、quick-stop、emergency-stop 语义和能力明确。
- [ ] WebSocket 写操作有认证、权限和审计。
- [ ] 多客户端有控制权和冲突策略。
- [ ] 请求、执行、设备错误可通过 ID 关联。

### 领域能力

- [ ] action 和 skill 使用统一 schema。
- [ ] 未知动作和非法参数在执行前拒绝。
- [ ] 相机、视觉、标定和工位数据有清晰生命周期。
- [ ] 数据采集和加粉流程有结构化状态与结果。

### 工程质量

- [ ] pytest、CI、lint、类型检查和覆盖率门禁生效。
- [ ] 无硬件 simulation 可回归主要流程。
- [ ] 关键真实硬件验收有记录。
- [ ] 配置、依赖、打包和平台支持策略清晰。
- [ ] 任务、技能和默认数据可重复交付。
- [ ] legacy 执行实现和过期文档已清理。

## 20. 进度维护规则

状态枚举：`TODO`、`DOING`、`BLOCKED`、`DONE`、`DROPPED`。

每次合并重构相关变更时：

1. 更新文档顶部日期和当前里程碑。
2. 更新对应 Track 工作项状态。
3. 更新第 21 节实施记录。
4. 如改变架构边界，更新第 13 节 ADR。
5. 如产生新的专项计划，在第 1 节增加链接。
6. 如发现新问题，新增 backlog ID，不用无编号备注替代。

`DONE` 必须同时满足：

- 代码完成。
- 自动化测试完成。
- 相关文档完成。
- 对应验收标准完成。

## 21. 实施记录

| 日期 | 里程碑 | Track | 工作项 | 状态变化 | 说明 | 提交/PR |
|---|---|---|---|---|---|---|
| 2026-07-27 | M0 | Program | 创建项目级总计划 | TODO → DONE | 汇总当前全部主要重构域 | - |
| 2026-07-27 | M0 | A | 创建执行运行时专项计划 | TODO → DONE | 建立执行重构阶段、验收和回滚计划 | - |
| 2026-07-27 | M1/M2 | A/B/D/F | 统一执行与设备运行时首轮落地 | TODO → DOING | ApplicationServices、ExecutionManager、DeviceRuntime、ResourceArbiter、TeleoperationService；GUI/AI/WS 直接切换并删除旧执行器 | - |
| 2026-07-27 | M2 | B/D/F | 机械臂供应商边界收敛 | TODO → DONE | 建立核心/可选能力、RealMan adapter、统一状态与错误模型；迁移 GUI/执行/视觉/数据采集并删除直连脚本 | - |
| 2026-07-28 | M2 | A/B/D/F | 统一安全停止软件链路 | B-001 TODO → DONE；ER-006 保持 DOING | 建立停止能力矩阵、逐设备结果与 SafetyService；RealMan 快停/急停接入 GUI/WS，真实硬件验收待完成 | - |
| 2026-07-28 | M2 | A/C/D | GUI 与附加服务统一宿主 | A-013/C-014 TODO → DONE | 删除 GUI/Server 二选一组合路径；WebSocket 进入受管理 asyncio 线程并共享唯一 ApplicationServices，默认仅监听本机 | - |
| 2026-07-28 | M2 | C/D | 编排状态与持久化收敛 | C-015 TODO → DONE | GUI/WebSocket 不再直接访问 JSON 存储；动作、任务和当前序列由线程安全 CompositionService 独占，写入采用原子替换并发布跨线程变更事件 | - |
| 2026-07-29 | M2 | A/B | 动作 handler 与执行控制首批收敛 | B-007 TODO → DOING；A-007 保持 DOING | 建立唯一 ActionHandlerRegistry、注册完整性校验和统一动作 deadline/cancel 上下文；首批拆出 WAIT/INSPECT，阻塞调用不使用脱离资源租约的后台超时线程 | - |

## 22. 建议的首批实施顺序

1. **A-007/B-007**：继续拆分运动、操作、视觉和领域 handler，并为每条阻塞硬件路径声明及验证设备级停止能力。
2. **B-002/ER-006**：补齐所有直接控制资源租约，并完成 RealMan 及其他运动设备停止能力的真实硬件验收。
3. **B-015/B-016**：用第二种机械臂验证 provider 契约，并配置化型号相关工具点位。
4. **C-001/C-002/C-003**：实现认证、客户端控制租约和审计。
5. **G-001/G-004/G-005**：补 CI、协议测试、lint 和类型检查。
6. 完成 simulation smoke test 后执行逐设备真实硬件验收。

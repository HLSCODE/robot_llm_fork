# Robot LLM 项目重构总计划

> 文档状态：Active  
> 创建日期：2026-07-27  
> 最近更新：2026-08-07
>
> 当前里程碑：M8 — 用户数据模型与自然语言命令收敛（进行中）
> 计划进度：144/154（142 DONE + 2 DROPPED，93.5%）
> 维护方式：本文件作为项目级重构总入口；专项设计和实施细节通过关联文档维护

## 1. 文档定位

本文档覆盖当前项目评审中已经识别出的全部主要重构域，用于统一管理优先级、跨模块依赖、里程碑、验收和技术决策。

本文档是“项目级总计划”，不替代专项设计文档。复杂领域应在实施前建立或更新专项计划，并从本文件链接：

| Track | 专项文档 |
|---|---|
| A. 执行运行时与安全 | [统一执行运行时重构实施计划](execution-runtime-refactor-plan.md) |
| B. 机械臂供应商适配 | [机械臂供应商适配架构](robot-provider-architecture.md) |
| C. WebSocket API | [WebSocket API 文档](websocket-api.md) |
| E. LLM 能力层 | [LLM Provider 治理与规划回归](llm-provider-governance.md)、[LLM 重构集成说明](llm-refactor-integration.md) |
| E. 语音交互 | [语音交互实现说明](voice-interaction-implementation.md) |
| F. 遥操作 | [遥操作说明](teleop.md) |
| F. 视觉与相机 | [视觉服务与数据治理架构](vision-architecture.md) |
| F. 数据采集 | [数据采集说明](data-collection.md) |
| F. 智能加粉 | [智能闭环加粉 Agent](powder_dispense_agent.md) |
| D. GUI 应用架构 | [GUI 应用架构](gui-application-architecture.md)、[GUI 工作流画布重构计划](gui-refactor-plan.md) |
| G. 工程与数据治理 | [工程质量门禁](quality-gates.md)、[依赖、配置与用户数据治理](data-config-governance.md) |

本计划覆盖的是当前仓库可确认的问题。后续发现的新问题应进入对应 Track 的 backlog，不能因为没有列在首版文档中而被忽略。

## 2. 项目现状摘要

项目目前已经具备完整的机器人应用雏形：

- PySide6 本地 GUI。
- WebSocket 远程控制服务。
- 双机械臂、底盘、身体升降、夹爪、吸液枪、换枪、加粉装置和表情屏。
- RealSense/OpenCV 相机、视觉抓取和视觉重定位。
- LLM provider、自然语言意图、技能规划、语音唤醒、ASR/VAD/KWS/TTS。
- 遥操作与版本化示教数据采集。
- 动作库、任务库、技能库和本地配置。

当前主要问题不是“功能不存在”，而是功能快速增长后缺少统一的运行时所有权和工程边界。首轮收敛已经将主要入口切换为：

```text
GUI / WebSocket / Voice / AI
              |
      ApplicationServices
       /       |          |              \
Execution  Device   CameraAccess   DataCollection/Session
 Service   Service     Service            Services
    |         |          |                   |
ExecutionManager    ResourceArbiter --------+
    |                         |
ActionEngine -------- DeviceRuntime ----- SafetyService
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
- 相机测试、语音视觉、WebSocket 预览和数据采集通过 `CameraAccessService`
  持有显式会话租约；最后一个预览订阅者离开或会话结束时自动释放。
- `DataCollectionService` 已成为 recorder、相机会话、session/episode 状态和
  共享遥操作控制会话的唯一应用层所有者；WebSocket 只做协议映射，安全停止、
  控制租约释放和设备关闭共用同一清理入口。
- 数据采集已升级到 schema v2：portable/native 显式格式、事务写入、容量预检、
  残留恢复和完整性验证工具均已落地；同时记录 depth scale、相机内参与畸变、
  设备/主机时间、可选外参，以及单臂/双臂有界偏差的真实设备遥测。
- 序列提交根据动作控制策略计算实际设备租约，不再锁定全部已注册设备；
  纯软件动作可与相机预览并行，视觉动作会与其他相机会话显式互斥。
- 已删除无引用的 legacy GUI、旧底盘控制器和独立 ADP 控制脚本。
- 已建立厂商无关的机械臂模型与能力接口，RealMan 仅在 adapter/driver 边界内出现。
- 已建立机械臂 Provider 注册表、强类型 RealMan 配置和可复用核心契约测试；
  未知 Provider 在运行时组装阶段直接失败，不会隐式回退。
- 工具架工作流已从 `RobotController` 收敛到 RealMan adapter；使用的机械臂、
  槽位位姿和停留时间均由 Provider 配置决定，旧枪头配置键已直接删除。
- GUI、执行引擎、视觉、重定位和数据采集均已切换到统一机械臂能力。
- 轨迹示教和遥操作持有完整会话周期的机械臂资源租约。
- 受控取消、软件快停和软件急停已由 `SafetyService` 统一编排；GUI 与 WebSocket
  共用逐设备结果，当前 RealMan 停止链路待真实硬件验收。
- 继电器、快换手和移液枪的安全态已注册到 `DeviceRuntime`；受控停止、快停和
  急停统一执行“继电器全断、快换手锁止、移液枪回初始化位”，并逐设备报告结果。
- 外部 UDP 定位由组合根注入 `UdpExternalLocalizationProvider`，Provider 独占
  socket/线程，应用级 `ExternalLocalizationService` 只持有读取策略和关闭入口；
  应用服务、执行引擎与 GUI 只通过显式依赖读取定位，不再依赖 GUI 全局单例。
- WebSocket 写操作已使用共享密钥认证和单控制客户端租约；租约超时、控制者
  断线或发送失败只释放其所属遥操作/采集会话，观察者断线不再停止全局遥操作。
- WebSocket 权限拒绝和写操作分发使用不包含凭据/payload 的结构化安全审计；
  请求直接响应、执行事件和业务终态已通过 `request_id/run_id` 完整关联，
  执行接受与最终成功/失败/取消分别记录审计。
- 已删除无引用的 RealMan 直连动作脚本，不保留兼容转发。
- `ActionHandlerRegistry` 已成为唯一动作类型分发入口，全部现有 `ActionType`
  在执行引擎构造时完成注册完整性校验；全部具体动作 handler 已迁出
  `ActionEngine`，引擎只保留序列编排、状态推进和事件转换。
- handler 已统一返回不可与 `bool` 混用的 `ActionHandlerResult`；稳定错误码、
  用户消息、操作标识和设备 ID 已贯通运行时快照及执行事件。
- 所有动作已接入统一硬 deadline、暂停和协作取消上下文；注册表同时绑定动作
  控制策略，按实际参数分支声明阻塞调用、涉及设备、取消模式和设备停止目标。
  策略与设备注册能力不一致时会在初始化设备前拒绝执行，并通过运行时事件及
  WebSocket 输出；handler 路由与策略路由不一致时应用启动失败，避免新增动作
  漏配安全策略。RealMan 最大停止延迟仍待真实硬件验收。
- 身体轴、继电器、快换手、移液枪、PWM 颈部和表情屏生产链路已统一通过
  `devices.transports.SerialTransport` 访问串口；端口参数、打开重试、RTS/DTR、
  超时、互斥收发和幂等关闭由组合根与共享 Transport 管理。
- PWM 颈部已作为 `MANIPULATE/颈部` 正式接入统一动作 schema、handler、控制策略
  和设备资源租约；水平、垂直、双轴及复位均经 `DeviceRuntime` 调用，参数在设备
  初始化前校验，驱动不再静默钳制越界 PWM。
- 工具架不再在机械臂 Provider 内直接持有移液枪串口逻辑；放枪动作按控制策略
  同时租用机械臂和移液枪，并从 `DeviceRuntime` 注入弹枪能力。
- 身体轴驱动中的独立 Tk 调试 GUI、旧移液枪串口脚本和无调用方的
  `tapping_device` 包装已删除；伪 `Stateful*`/`*Snapshot = object` 导出一并移除，
  不保留兼容入口。
- 硬件失败已统一为 `DeviceErrorCategory` 和 `DeviceOperationError`；设备、操作、
  稳定分类及可用的供应商原始码贯通 step/terminal event、执行快照、设备状态与
  WebSocket，用户消息不包含底层端口、堆栈或 SDK 诊断。
- 全部硬件运行时、通信、驱动和产品实现已物理收敛到 `src/devices/`；旧的
  `device_runtime`、`arm_sdk`、`base_move`、`cameras`、`device_control_sdk`、
  `pwm_sdk` 和 `expression_display` 顶级目录已直接删除，不保留导入转发。
- RealMan 已拆分为通用 Provider 定义、独立注册表以及厂商目录内的
  Provider/Adapter/Driver；厂商 SDK 仅允许出现在 RealMan Driver，新增机械臂
  可在 `src/devices/robots/<provider>/` 下平行实现。
- GUI 已按 `controllers/bridges/view_models/views` 组织，WebSocket 已按
  `controllers/protocol/security/metrics` 组织；旧平铺模块直接删除，不保留导入转发。
- `ApplicationServices` 不再公开 `DeviceRuntime`；GUI、WebSocket、Widget 和 Voice
  只能通过设备管理、相机访问、遥操作、查询与安全等 Application Service 获取状态
  或执行用例。
- 原 `src/core/` 已拆空并删除：启动生命周期进入 `bootstrap`，配置进入
  `configuration`，领域模型进入 `domain`，持久化进入 `persistence`，纯几何进入
  `geometry`，日志进入 `observability`；稳定层依赖方向由 AST 测试约束。
- 未引用的底盘客户端、RealSense 调试 Widget、源码内轨迹/图片产物已删除；瓶子抓取
  从历史 action 迁入 vision，调试图片只写入 VisionArtifactStore 管理的运行目录。

本轮已完成的重点：

- 视觉天平已进入 `CameraAccessService`、`LLMRegistry` 和 `DeviceRuntime` 的统一所有权边界。
- Qt 通用视图、AI Assistant、音频播放和 AI Controller 已分别进入
  `src/gui/views/` 与 `src/gui/controllers/`；复合执行流程已进入
  `src/execution/workflows/`，四个旧顶级目录直接删除且无兼容转发。

本轮同时完成：

- `src/devices/transports/devices/` 已删除；ElectricGripper、StepperMotor 与粉末装置
  共置，relay/tool-changer/pipette Adapter 与各自 Driver 共置，Transport 目录只保留
  通信、协议和测试能力。

仍需继续收敛的重点：
- 相机仍使用条件工厂而非与机械臂一致的 Provider 注册表；移动底盘和显示屏的
  Provider/Adapter/Driver 组织也尚未统一。
- `vision` 同时混放 pipeline、专用几何、CLI、模型调用和产物治理；
  `application/localization.py` 同时承担应用服务与 UDP socket/线程 Provider 职责。
- `src/robot_server/ws_server.py` 的传输宿主继续按连接、投递和会话职责细分。
- `src/gui/controllers/main_window.py` 的页面协调逻辑继续按稳定业务域下沉 controller。
- `src/execution/engine.py`
- `src/configuration/config_loader.py` 的超长环境变量解析可继续按领域 settings 拆分。
- `src/devices/robots/realman/driver.py` 的厂商驱动内部职责细分和错误模型。
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
                      devices runtime / capabilities
                                    |
       robots / cameras / motion / tools / displays / transports

     LLMRegistry -> VoiceInteraction -> CommandRuntime -> SkillEngine
                                            |
                         versioned preview / risk approval
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

### 4.2 模块与目录组织

项目继续采用单进程模块化架构，不把整个机器人系统套入传统 Web MVC。MVC/MVVM
仅用于 GUI、WebSocket 和未来 HTTP 等表现层；执行、设备、视觉和数据采集使用
Application Service + Port/Protocol + Provider/Adapter/Driver 的依赖方向。

硬件代码统一收敛到 `src/devices/`，按设备领域再按实现角色组织：

```text
src/devices/
├── __init__.py                 # 对上层公开稳定的设备能力 API
├── runtime/                    # contracts、models、errors、生命周期、资源与 fake
├── transports/                 # 仅包含 serial、TCP、CRC、Modbus RTU 与 transport fake
├── robots/
│   ├── provider.py             # 厂商无关的 provider 定义
│   ├── registry.py             # provider 注册、查找与失败策略
│   └── realman/
│       ├── provider.py         # 配置解析与实例装配
│       ├── adapter.py          # 项目 capability 到厂商 API 的转换
│       └── driver.py           # 厂商连接和底层调用；SDK 来自安装依赖
├── cameras/                    # provider/registry、realsense、opencv
├── sensors/                    # balance 等只读测量能力及其 Provider
├── motion/                     # mobile_base、body_axis、neck
├── tools/                      # relay、tool_changer、pipette、powder
└── displays/                   # t5l_dgusii
```

角色命名必须保持稳定：Service 负责编排用例和资源策略；Provider 选择并创建产品
实现；Adapter 实现项目能力接口并转换模型/错误；Driver 封装厂商协议；Transport
只处理通信。禁止建立混放协议、驱动和应用逻辑的万能 `services/` 目录。

当前容易混淆但应保留的稳定目录职责如下：

| 目录 | 唯一职责 | 禁止放入 |
|---|---|---|
| `skill_system/` | 技能模型、技能库、匹配、参数绑定与动作展开 | LLM Provider、硬件调用、执行线程 |
| `data_collection/` | 示教采样、Episode 编码、schema、事务写入与离线校验 | WebSocket 会话、相机租约、遥操作所有权 |
| `persistence/` | 通用 Repository、JSON 文档和原子文件读写 | 业务流程、Qt、设备 SDK |
| `geometry/` | 跨领域可复用的纯位姿与坐标计算 | 相机 I/O、模型推理、工位会话 |
| `observability/` | 通用日志、指标输出与上下文传播基础设施 | 具体业务指标模型和控制决策 |

以下同名或近似职责属于合理分层，不做机械合并：

- `application/data_collection.py` 编排采集会话、资源租约与状态；
  `data_collection/` 负责采样和持久化实现。
- `CompositionService` 持有已持久化动作、任务和共享序列；
  `TaskComposerService` 持有尚未持久化的 GUI 组合草稿。
- UDP 外部定位与视觉工位重定位不是同一能力；通过更明确的类型和模块命名消除
  `Localization`/`Relocalization` 歧义，不合并状态所有权。

第二轮目录目标：

```text
src/
├── application/               # 所有入口可复用的用例服务
├── execution/
│   ├── handlers/              # 单动作 handler
│   └── workflows/             # circle_dispense、powder_dispense 等复合执行流程
├── devices/
│   ├── runtime/
│   ├── transports/            # 不允许出现语义设备
│   ├── robots/
│   ├── cameras/
│   ├── sensors/
│   ├── motion/
│   ├── tools/
│   └── displays/
├── gui/
│   ├── controllers/           # 包含 AI Qt controller
│   ├── views/                 # 包含通用 widgets 与 AI Assistant
│   ├── bridges/
│   └── view_models/
├── vision/                    # pipeline、relocalization、artifacts 的清晰子域
├── skill_system/
├── data_collection/
├── persistence/
├── geometry/
└── observability/
```

迁移按领域整批直接切换导入和组合根，随后删除旧目录；不保留 import 转发、旧新
目录双栈或兼容开关。当前不为目录整洁强制增加 `presentation/` 或
`infrastructure/` 大目录；真正新增 HTTP 入口时，再评估将 WebSocket/HTTP 统一到
`interfaces/`，避免提前增加无业务收益的层级。

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
| A-006 | P1 | DONE | 唯一 ExecutionManager 已落地；并发 submit、启动前取消、取消/完成竞态、worker 启动失败及资源释放均有确定性回归测试；硬件停止时延由 B-007/ER-006/ER-011 独立验收 |
| A-007 | P1 | DONE | 唯一注册表、全部具体动作 handler 和结构化 ActionHandlerResult 已落地 |
| A-008 | P1 | DONE | WebSocket 已迁移；路由、全部 action 最小合法请求、payload 边界、错误码和响应 DTO contract test 已进入 pytest |
| A-009 | P1 | DONE | GUI 手工与 AI 共用唯一 ApplicationServices/ExecutionRuntime；offscreen smoke 覆盖真实窗口装配及执行控制 |
| A-010 | P1 | DONE | GUI 文本、真实语音和 WebSocket 命令统一进入 CommandRuntime；入口不再持有私有预览缓存 |
| A-011 | P1 | DONE | simulation 与真实模式共用状态机和执行入口 |
| A-012 | P3 | DONE | 删除 legacy executor，未引入 backend 开关 |
| A-013 | P1 | DONE | GUI 成为唯一桌面应用宿主，附加网络服务与 GUI 共用 ApplicationServices，退出顺序由组合根统一管理 |
| A-014 | P1 | TODO | 将执行输入从 `SequenceItem | LoopBlock` 线性集合升级为结构化执行计划，首先覆盖可嵌套 Sequence/Action/Loop，并为已立项的 Parallel 节点定义编译映射、资源集合、join/failure policy 和确定性事件身份；不引入第二执行器 |
| A-015 | P1 | TODO | 在唯一 ExecutionManager/ResourceArbiter 内实现受控并行分支调度；明确资源冲突拒绝、分支失败传播、cancel-all、暂停/恢复、终态聚合和审计语义，使用 fake device 完成竞态与释放测试后再做真实硬件验收 |

### 6.3 完成标准

- `src/execution/` 是唯一动作序列执行实现。
- 每个 run 有唯一终态。
- GUI、AI、WS、voice command 不直接执行 ActionType。
- 新增动作只注册一次。
- 暂停、取消、失败和成功在所有入口语义一致。

## 7. Track B：设备运行时、硬件 SDK 与安全

### 7.1 当前问题

- GUI 与 WebSocket 已共用同一个 `DeviceRuntime`，但真实设备仍需逐项验证统一关闭顺序。
- presentation/transport 层的硬件写操作已通过 Application Service 或统一执行
  handler；保留的 `get_if_ready()` 仅用于只读就绪状态展示。
- 串口访问已收敛到共享 Transport，但部分设备协议驱动仍需继续强类型化。
- 设备连接状态可能只表示“控制器对象存在”，不表示设备真实在线。
- Transport、协议、机械臂、设备生命周期、手动控制、遥操作和安全停止已共用
  设备错误分类；真实设备仍需验证各供应商原始码的含义与恢复策略。
- 部分阻塞 SDK 无法及时响应取消。
- PWM 舵机实际机械限位、方向和不同负载下的动作时长仍需真实设备验收。
- 硬件物理目录和运行时所有权已经统一；剩余风险主要是真实设备关闭顺序、
  阻塞 SDK 取消时延、设备在线判定和第二种机械臂 Provider 的契约验证。

### 7.2 目标

- 引入 `DeviceRuntime` 作为设备实例、健康状态和关闭顺序的唯一所有者。
- 引入 capability 模型，区分设备存在、连接、就绪、故障和模拟。
- 引入 `ResourceArbiter`，统一序列、遥操作、测试和直接控制的资源租约。
- 定义安全停止接口和设备恢复策略。
- 新设备复用 `src/devices/transports` 的 Transport/Protocol；语义设备实现放入所属领域包。
- 厂商协议只能出现在 driver/adapter 层，上层统一使用项目级能力接口。
- 核心机械臂能力与可选能力分离，供应商不必伪造不支持的功能。
- 所有硬件代码收敛到单一 `src/devices/` 领域目录，具体产品采用
  Provider + Adapter + Driver 垂直切片，公共通信协议位于 transports。

### 7.3 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| B-001 | P0 | DONE | 已定义 cancel、quick-stop、emergency-stop 语义、结果状态和逐设备能力矩阵；真实硬件验收由 ER-006 跟踪 |
| B-002 | P0 | DONE | 序列按动作策略申请精确设备集合；设备生命周期、手工控制、遥操作、示教、相机预览/测试/语音和数据采集均使用资源租约 |
| B-003 | P0 | DONE | 设备关闭前取消执行并等待资源释放 |
| B-004 | P1 | DONE | 建立 DeviceRuntime 和 DeviceSnapshot |
| B-005 | P1 | DOING | 主要入口已统一初始化/重连/关闭，待真实设备逐项验收 |
| B-006 | P1 | DONE | 定义设备 capability/state/error |
| B-007 | P1 | DOING | 已建立全动作控制策略矩阵、执行前停止能力校验和事件输出；无即时取消能力的路径已显式标记，待完成 RealMan 最大停止延迟硬件验证 |
| B-008 | P2 | DONE | 身体轴、继电器、快换手、移液枪、PWM 颈部和表情屏统一使用共享 SerialTransport；配置、有限重试、RTS/DTR、超时、互斥收发和幂等关闭均由唯一生命周期入口管理 |
| B-009 | P2 | DONE | relay/tool-changer/pipette 及机械臂 adapter 已落地，视觉不再依赖厂商对象 |
| B-010 | P2 | DONE | PWM neck 已接入 MANIPULATE 动作 schema、handler、控制策略和 DeviceRuntime 资源租约；支持水平、垂直、双轴及复位，越界参数在初始化设备前拒绝 |
| B-011 | P2 | DONE | 建立 unavailable/connection/timeout/protocol/rejected/I/O/internal 设备错误模型；统一执行、设备生命周期、手动控制、遥操作与安全停止消息，并贯通事件、快照及 WebSocket |
| B-012 | P3 | DONE | 已完成全仓设备封装与脚本引用审计；删除旧移液枪串口脚本、身体轴内嵌 Tk GUI、无调用方 tapping_device 包装及伪 Stateful/Snapshot 兼容导出 |
| B-013 | P0 | DONE | 定义厂商无关的机械臂运动、状态、夹爪及可选能力协议 |
| B-014 | P1 | DONE | RealMan adapter 接入统一运行时，业务层移除 `rm_*` 和原生控制器访问 |
| B-015 | P1 | DOING | Provider 注册表和可复用核心契约测试已落地；待明确并接入第二种真实机械臂 adapter，执行软硬件契约验收 |
| B-016 | P1 | DONE | RealMan 型号、双臂连接、运动/夹爪参数、工具架臂/槽位位姿/停留时间已进入强类型 Provider 配置；删除 controller 硬编码工作流和旧配置入口 |
| B-017 | P1 | DONE | DeviceRuntime 统一注册并报告继电器全断、快换手锁止和移液枪回初始化位安全策略，SafetyService 在所有停止模式下执行 |
| B-018 | P1 | DONE | device_runtime、arm_sdk、base_move、cameras、旧 flat devices、device_control_sdk、pwm_sdk、expression_display 已整批收敛到 `src/devices/`；组合根、导入、打包和边界测试已切换，旧目录直接删除且无转发模块 |
| B-019 | P1 | DONE | RealMan 已按 provider/adapter/driver 垂直切片，通用 Provider 定义与注册表分离；Adapter 实现项目 capability，Driver 隔离已安装 SDK，为第二供应商建立平行目录模板 |

### 7.4 完成标准

- 设备状态有唯一可信来源。
- 同一设备不能被两个业务流同时控制。
- shutdown 有明确顺序、超时和结果。
- 硬件错误能够关联设备、操作、run_id 和原始错误。
- simulation device 与真实 device 暴露相同 capability 接口。

### 7.5 设备错误契约

设备边界统一使用以下稳定分类：

| 分类 | 含义 |
|---|---|
| `unavailable` | 设备未注册、依赖缺失、初始化失败或能力不满足 |
| `connection` | 连接打开失败、连接已关闭或连接不可用 |
| `timeout` | 设备在约定时间内没有完整响应 |
| `protocol` | CRC、帧结构、Modbus 异常或响应内容无效 |
| `rejected` | 设备或供应商 SDK 明确拒绝操作 |
| `io` | 其他传输读写失败 |
| `internal` | 未归入上述类型的设备实现错误 |

对外失败 DTO 使用 `code`、`operation`、`device_id`、`error_category` 和
`raw_error_code`。`raw_error_code` 仅保留可安全序列化的供应商/传输错误码；
用户消息使用稳定分类文本，底层异常类型、端口和诊断只进入内部日志。执行事件
继续通过 `run_id` 关联完整操作，取消和执行 deadline 不归类为普通设备失败。

## 8. Track C：WebSocket API、安全与服务拆分

### 8.1 当前问题

- 默认监听 `127.0.0.1`；远程直连强制 TLS、认证和 Origin 白名单，同机可信
  反向代理强制 loopback 后端并由代理终止 TLS，部署配置和验收清单已文档化。
- 多客户端写操作已由单控制客户端租约串行化；公开查询和已认证的相机/聊天
  读取会话仍允许观察者并行访问。
- 请求幂等语义尚未定义。
- 执行与编排事件按设计广播给观察者；后续若增加更多高频事件，需要扩展显式
  订阅类别。
- 聊天协议已经明确存在不支持并发请求的限制。
- WebSocket 已提供不含客户端身份和 payload 的聚合 API/连接/发送指标；生产环境
  仍需接入外部采集与告警系统。

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
| C-001 | P0 | DONE | 写操作使用常量时间比较的共享密钥认证；未配置密钥时服务只读且写操作默认拒绝，认证 payload 不进入日志 |
| C-002 | P0 | DONE | 建立可续期的单控制客户端租约；冲突立即拒绝，超时/控制者断线释放所属遥操作和采集资源，观察者断线不干扰控制者 |
| C-003 | P0 | DONE | 权限拒绝、同步完成/拒绝、执行接受与最终成功/失败/取消均记录结构化 client/request/action/run/outcome，且不含凭据或请求 payload |
| C-004 | P1 | DONE | 所有请求直接响应关联 request_id/action；执行接受、步骤、日志和终态继续关联同一 run_id/request_id |
| C-005 | P1 | DONE | 请求错误统一为带稳定 code/message/request_id/action 的 error 信封；专用模块错误在协议边界归一，内部异常不向客户端泄露 |
| C-006 | P1 | DONE | 所有请求强制声明 api_version=2.0，所有响应携带版本；不维护旧协议适配器，破坏性变更要求客户端与服务端同步升级 |
| C-007 | P1 | DONE | 配置化限制单消息大小、每客户端频率、全服务并发和入站队列；慢客户端发送有 deadline，限流/繁忙返回稳定错误码 |
| C-008 | P1 | DONE | 请求结果单播、系统/执行事件广播、相机帧显式订阅；广播与订阅发送并发隔离慢客户端 |
| C-009 | P2 | DONE | Server 的业务处理方法已清空，仅保留连接、鉴权、限流、顶层路由、投递及 handler host context；执行、编排、交互、设备、遥操作/采集拆为独立 handler，并复用 ApplicationServices |
| C-010 | P2 | DONE | 请求在边界转换为不可变 WebSocketRequest，响应经 WebSocketResponse 统一序列化 |
| C-011 | P2 | DONE | 远程直连强制 TLS、认证和精确 Origin 白名单；同机可信代理强制 loopback、代理终止 TLS 且不采信转发头授权；配置错误启动失败，部署与验收清单已文档化 |
| C-012 | P2 | DONE | 全部 action 具有唯一 payload schema；缺失字段、错误类型和未知字段在鉴权/handler 前统一拒绝，路由缺少 schema 时启动失败 |
| C-013 | P3 | DONE | 增加线程安全的连接/请求/错误/限流/发送延迟聚合指标和已认证 server_metrics 动作；慢发送、发送超时及超时断连独立计数，超时连接明确关闭并释放会话 |
| C-014 | P1 | DONE | WebSocket 改为 GUI 同进程的可选附加服务，共用唯一 ApplicationServices 和受管理 asyncio 生命周期 |
| C-015 | P1 | DONE | 动作库、任务库和当前编排序列已收敛到线程安全 CompositionService；JSON 原子替换并向 GUI/网络入口发布 revision 事件 |

### 8.4 完成标准

- 未授权客户端不能执行、初始化、断开或遥操作设备。
- 每个写操作能够关联用户/客户端、request_id 和结果。
- 多客户端不能无提示抢占控制权。
- Server 类只负责连接生命周期和顶层路由。
- 当前协议行为和版本由自动化 contract test 保证。

## 9. Track D：GUI 应用架构

### 9.1 当前状态与剩余问题

- 任务组合草稿已经由 `TaskComposerService` 独占，`MainWindow` 只渲染和转发用户意图。
- 设备连接状态和执行按钮状态分别由 `DeviceViewModel`、`ExecutionViewModel`
  从运行时快照派生，不再使用 GUI 平行布尔变量。
- `ActionConfigDialog` 已由统一 action schema 生成全部动作和 variant 字段，
  旧的逐动作 UI/参数构建分派已删除。
- `GuiNotificationCenter` 已统一 MainWindow 的日志、状态栏、通知历史、模态错误和确认出口。
- AI Assistant 只通过窄 Qt 信号请求欢迎任务、序列可视化和执行状态展示，
  不再持有或调用 MainWindow。
- `closeEvent` 只发出执行取消、相机中断和交互停止请求；可阻塞的有界等待在
  Qt 事件循环退出后执行，再由应用宿主统一关闭附加服务和设备。
- 动作库/任务库、序列/任务组合器、设备状态/位姿和手动控制已拆成四个稳定视图组件；
  `MainWindow` 只组合组件、连接意图信号并调用渲染接口，不保留旧控件属性别名。
- 新一轮 GUI 评审确认：列表式编排区域仍需升级为受约束的工作流画布；原 GUI 草案中
  新建 `WorkflowExecutor`/节点 Handler、首版移除循环及 Qt/领域模型混合等方案已废止，
  统一改为复用 `CompositionService`、规范 `SequenceEntry`、`ExecutionBridge`、
  `ExecutionManager` 和唯一 `ActionHandlerRegistry`。
- M6 已完成受约束画布与旧编辑器切换，但当前主窗口仍把设备状态/位姿、动作与任务库、
  执行按钮、基础控制和日志同时常驻，竖屏下画布被压缩；`ActionLibraryView` 同时组合
  动作分类、任务和 AI，资源导航层级不清。下一阶段只重组 GUI 工作台壳层，不改动
  Application Service、DeviceRuntime、ExecutionManager 或现有领域模型。

### 9.2 目标

- MainWindow 只负责窗口组合、导航和顶层 Qt 生命周期。
- 引入应用服务或 view-model/state model 管理任务、设备和执行状态。
- Qt worker 只承担线程适配，不承载领域执行。
- 动作表单由统一 action schema 驱动。
- AI、语音、手工控制共享同一状态源。
- 启动和关闭流程可观测、有超时、可取消。
- 主窗口采用 Top Menu、Activity Bar、Side Bar、Editor、Bottom Panel 和 Status Bar
  的工作台信息架构，使画布成为默认主区域。
- 资源、持续状态与操作命令分层展示；低频详情按需展开，关键故障与安全停止始终可见。
- 统一 SVG/Qt Resource 图标、布局偏好持久化和深浅主题状态，不以 Emoji 作为主导航体系。

### 9.3 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| D-001 | P0 | DONE | GUI 手工/AI 共用进程级执行互斥 |
| D-002 | P1 | DONE | ExecutionBridge 已成为纯 Qt 事件 adapter |
| D-003 | P1 | DONE | ExecutionBridge 不再拥有序列 worker；安全停止仅用短生命周期 I/O 调度线程避免阻塞 Qt 主线程 |
| D-004 | P1 | DONE | MainWindow 不再关闭设备；应用宿主先停附加服务，再统一关闭 DeviceRuntime |
| D-005 | P2 | DONE | `TaskComposerService` 独占任务/动作组合草稿、排序、循环和序列展开 |
| D-006 | P2 | DONE | `DeviceViewModel` 从 DeviceManagementService 快照派生 GUI 设备状态，删除窗口连接布尔值 |
| D-007 | P2 | DONE | `ExecutionViewModel` 从 ExecutionService 状态机派生暂停/恢复/取消能力和按钮状态 |
| D-008 | P2 | DONE | `ActionConfigDialog` 按唯一 action schema 通用渲染字段、variant、默认值、范围、枚举、只读和校验 |
| D-009 | P2 | DONE | 生命周期、手动控制、轨迹示教和位姿读取已进入 Application Service |
| D-010 | P2 | DONE | 删除启动布尔组合和嵌套事件循环；显式 startup lifecycle 管理 speech wait、硬件初始化、ready/failed/closed，等待使用异步 signal + 单次超时 |
| D-011 | P3 | DONE | `GuiNotificationCenter` 统一日志、状态栏、通知历史、模态错误和确认；AI 使用窄信号协作；关闭采用非阻塞请求 + 事件循环后有界等待 |
| D-012 | P3 | DONE | Windows/Linux offscreen GUI smoke 构造真实 MainWindow + simulation services，覆盖启动、暂停、恢复、取消、日志终态和资源清理 |
| D-013 | P2 | DONE | 提取 `ActionLibraryView` 与 `WorkflowEditorView`，统一动作库、任务库、序列和任务组合器的构造、意图信号及执行控件渲染接口 |
| D-014 | P2 | DONE | 提取 `DeviceStatusView` 与 `DeviceControlView`，统一设备状态、位姿、定位和手动控制视图；组件不持有设备或应用服务 |
| D-015 | P0 | DONE | 冻结 GUI 功能等价清单与架构 ADR，明确受约束画布、Loop 表现、单一执行入口、安全停止语义和直接切换门槛 |
| D-016 | P1 | DONE | 建立纯 Python WorkflowDocument、Schema 版本、草稿恢复和 revision 冲突保护；该 v1 基础已由 G-029 的 WorkflowDocument v2 和唯一 `*.workflow.json` 正式格式取代 |
| D-017 | P1 | DONE | 实现结构 Validator、执行 Preflight 与 WorkflowCompiler，将合法文档编译为 SequenceEntry，并建立展开步骤索引/Loop UUID 到节点的稳定映射；应用组合根持有唯一实例，未新增执行器和动作 Handler |
| D-018 | P1 | DONE | 实现受约束 QGraphics 工作流画布、Loop 容器、自动布局、“+”插入、带阈值拖动排序及显式上移/下移、按需参数编辑、Undo/Redo、鼠标/键盘/触控导航及执行期编辑锁定 |
| D-019 | P1 | DONE | 任务组合、AI/语音预览、轨迹、相机、日志、设备状态和三类停止控制继续复用现有服务；当前序列统一经 Compiler/Preflight/ExecutionBridge 执行，MainWindow 已删除 QTreeWidgetItem 和画布私有绘制/映射依赖 |
| D-020 | P2 | DONE | GUI 视觉/交互令牌集中定义，提供 `system/light/dark` 三种应用级统一主题和“视图 → 主题”即时切换；中性颜色、字体、画布、表单、菜单、状态和禁用态共享单一 Palette/QSS，支持高 DPI；关键按钮使用 44 px 触控目标和 accessibleName/Description；建立浅色/深色、360×640/720×1280/1280×720 offscreen 矩阵及 100/500 节点版本化性能预算；视觉资产许可证清单已记录 |
| D-021 | P1 | DONE | 新画布已作为唯一编辑器运行；原 QTreeWidget SequenceListWidget 与 components 聚合模块直接删除，动作库、控制面板、日志拆为单责视图文件；任务持久化后续已由 G-029/G-032 一次迁移为唯一 WorkflowDocument v2，不保留双写、双运行、转发或兼容层 |
| D-022 | P1 | DONE | 已建立唯一 Workbench 壳层：顶部文件/编辑/视图/执行/设备菜单、固定 Activity Bar、可调整 Side Bar、中央 Editor、可调整 Bottom Panel 和 Status Bar；左侧资源入口支持二次点击收起，分隔条保持 1 px 视觉与 7 px 命中区；旧箭头抽屉和纵向堆叠主布局已删除，不保留双壳层 |
| D-023 | P1 | DONE | 已将聚合资源区拆为 `TaskLibraryView`、`ActionLibraryView`、独立 AI Assistant 和 `TaskComposerView` 四个 Side Bar 页面；Activity Bar 直接切换/收起页面，中央 Editor 删除任务组合 Tab；所有列表继续渲染 CompositionService/TaskComposerService 状态，未增加 GUI 业务状态源或兼容路径 |
| D-024 | P0 | DONE | 设备详情、位姿、日志和基础控制已迁入非模态 Bottom Panel，Status Bar 展示同源设备摘要和通知；执行/编辑控制由五行大按钮收敛为两行命令区，停止任务、快速停止和设备急停在 Side/Bottom Panel 任意状态下常驻可见 |
| D-025 | P2 | DONE | 已建立项目自制 CC0 单色 SVG + 编译 Qt Resource 图标体系，Activity/Status 入口按 Palette 与 1x/2x/3x 渲染，主要命令区删除 Emoji 装饰；schema v1 QSettings 偏好持久化 Side/Bottom 当前页、可见性与尺寸，严格拒绝损坏/未知版本/已删除页面并恢复默认，提供显式恢复默认布局命令和 wheel 内容门禁 |
| D-026 | P0 | DONE | 主窗口在首次渲染前通过 `CompositionService.list_tasks()` 刷新 TaskLibraryView；真实 MainWindow offscreen 回归预置磁盘任务并验证首屏条目名称，避免已保存任务再次显示为空 |

### 9.4 完成标准

- MainWindow 不拥有硬件和业务执行实现。
- GUI 只有一个执行状态源。
- 新动作不需要在多个 GUI 映射表重复登记。
- UI 主线程不执行阻塞操作。
- 启动、执行和关闭均有明确状态和错误反馈。
- WorkflowDocument、Qt Bridge/Scene 与运行时状态边界清晰，GUI 不定义平行动作模型。
- 工作流只经 Compiler 转为规范 SequenceEntry，再提交唯一 ExecutionManager。
- Loop、任务组合、AI 导入及三类停止控制在新编辑器中无功能回退。
- 新编辑器满足目标分辨率、DPI、纯触控、可访问性和性能预算，切换后旧编辑器已删除。
- 画布在默认布局中占据主要空间，资源页与持续详情不再同时常驻挤压画布。
- Side Bar 分隔线保持 1 px 视觉样式并提供足够透明命中区；Bottom Panel 可调整且非模态。
- SVG 导航在主题、DPI、wheel 打包和无工作目录假设下可用；布局偏好损坏时可恢复。
- 安全停止与关键故障在所有页面、抽屉和面板状态下保持可见、可理解和可操作。

## 10. Track E：LLM、语音交互与技能系统

### 10.1 当前优势

- `src/llm/` 已有 provider 抽象、registry、task runner 和 capability。
- `src/voice_interaction/` 已拆分 session、router、speech runtime 和 camera adapter。
- `src/skill_system/` 已具备技能注册、规划展开和基础校验。
- `src/domain/action_schema.py` 已成为 WebSocket 动作结构与 Skill 参数校验的
  唯一 Schema 来源；技能输入通过显式绑定映射到动作字段。
- `ApplicationServices.commands` 是进程内唯一命令预览、版本、过期、风险审批和
  execution control 状态源；GUI 与 WebSocket 只负责展示和协议适配。
- GUI 文本与真实语音共享一个 `VoiceInteractionController` 和会话历史；
  WebSocket 使用独立交互会话，但复用相同 CommandRuntime 策略。
- OpenAI-compatible 请求使用统一 transport timeout；交互轮次支持总超时和主动
  cancel。`ApplicationServices.llm` 是唯一进程级 Registry，GUI、WebSocket 和
  Voice 共享 provider、健康、熔断和指标；附加服务只释放会话，应用宿主最终关闭。
- 所有 task 通过统一路由代理执行；provider health、熔断、显式 fallback 和
  Prompt/provider/model/技能目录来源追踪已贯通非流式、流式和命令预览。
- 已建立严格 schema v1 的离线 golden 数据集和 runner，固定验证分类、规划、
  Prompt 快照、技能目录及动作展开。
- 唯一 LLMRegistry 已统一聚合逻辑调用结果、延迟、fallback、token 和 provider
  明确报告的成本；不保留 prompt/响应，不使用硬编码模型价格估算缺失成本。

### 10.2 当前问题

- 缺少版本化在线语义质量评测；运行指标不能替代模型质量数据集。
- 命令 Planner 只能返回 `skill_id`，原子设备命令、复合 Skill、已保存 Workflow
  和执行控制尚未形成类型化联合模型；若继续把“打开夹爪”“向前一点”等单步
  指令包装成 Skill，会造成目录膨胀、参数重复和安全语义混淆。
- Skill 已切换为按领域拆分的 schema v2 单文件，内置数据也来自相同 JSON 资源；当前
  剩余问题是 Planner 尚未区分原子命令、复合 Skill 和 Workflow，而不是数据双事实源。

### 10.3 目标

- 统一 GUI 文本和语音会话策略，明确是否共享历史。
- 所有模型任务支持取消、超时、结构化错误和指标。
- 技能规划产出 typed `ExecutionPlan`。
- 规划、审批和执行严格分层。
- 高风险动作支持明确审批策略。
- 技能、prompt、provider 和模型版本可追踪。
- Action、Skill、Workflow 和 ExecutionControl 是互斥且显式的命令种类；所有
  物理命令继续经过同一参数校验、预览、风险确认和 ExecutionManager。
- Skill 保持领域能力语义，不因自然语言同义词或单个设备操作机械扩张。

### 10.4 工作项

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| E-001 | P0 | DONE | 已删除 auto execute 配置和事件分支；GUI/WS 只接受通过校验且要求显式确认的预览，执行前再次校验确认状态，新输入会使旧预览失效 |
| E-002 | P1 | DONE | Voice router 通过 ApplicationServices.commands 完成技能展开、预览注册和执行控制，不直接持有 SkillEngine |
| E-003 | P1 | DONE | classifier/router 使用独立 session_action 与 execution_action；暂停会话不会暂停设备执行 |
| E-004 | P1 | DONE | CommandRuntime 生成单调 version、唯一 preview_id、有效期和状态；确认精确匹配 ID/版本且只能消费一次 |
| E-005 | P1 | DONE | Skill action type 由 `ActionType` 单一映射解析；未知或非字符串类型返回 `unsupported_action_type`，不生成动作预览 |
| E-006 | P1 | DONE | 技能参数使用强类型、单位和显式字段绑定；展开前统一校验必填项、未知字段、选项与范围，WebSocket 复用同一 action schema |
| E-007 | P1 | DONE | 物理运动/操作/换枪/重定位/轨迹动作标记为 high risk；GUI 和 WebSocket 必须提交独立风险确认 |
| E-008 | P2 | DONE | GUI 文本与真实语音共享 controller/session/history，互斥处理单轮请求；WebSocket 会话独立、审批策略共享 |
| E-009 | P2 | DONE | 统一 provider request timeout、interaction turn timeout、跨线程 cancel 和 Registry/provider 幂等 close |
| E-010 | P2 | DONE | 所有 task 统一经过 RoutedLLMClient；支持共享 health、连续失败熔断、半开探测和显式 fallback，单次显式 provider 禁止暗中跨厂商降级 |
| E-011 | P2 | DONE | TaskProfile 强制显式版本；结果记录 Prompt 模板/请求哈希、实际 provider/model、尝试顺序及技能目录版本与指纹，并贯通命令预览和流式协议 |
| E-012 | P2 | DONE | 建立 strict schema v1 固定数据集和离线 runner，覆盖分类/规划解析、Prompt 快照、技能目录及技能参数校验/动作展开 |
| E-013 | P3 | DONE | RoutedLLMClient 统一采集逻辑调用成功/失败/取消、延迟、fallback、task/成功 provider/model、token 和 provider 报告成本；记录 usage/成本覆盖率，不保存 payload，不硬编码价格；通过 ai_status/server_metrics 暴露并纳入性能预算 |
| E-014 | P1 | TODO | 将 Planner 输出升级为 `ActionCommand | SkillCommand | WorkflowCommand | ExecutionControlCommand` 类型化联合模型；GUI 文本、语音与 WebSocket 使用同一解析和错误语义，不允许入口根据字符串自行分派硬件 |
| E-015 | P1 | TODO | 为夹爪控制、机械臂/底盘有界相对移动等高频单步语音指令建立规范 Action schema、上下文消歧、限幅、坐标系和控制策略；“一点”等步长来自强类型配置，歧义时询问而非隐式选臂/设备 |
| E-016 | P2 | TODO | 建立动作/技能/工作流统一命令目录与自然语言 examples/aliases；高置信确定性解析可作为低延迟入口，LLM 作为自然语言回退，但两者产出相同 typed command 并进入同一 CommandRuntime 审批链 |

### 10.5 完成标准

- LLM 只能产生计划，不能绕过审批/策略直接调用硬件。
- 相同文本在 GUI、语音和 WebSocket 中产生一致 command 行为。
- 未知动作和非法参数不会进入执行层。
- provider 失败有明确降级或用户可理解错误。
- 规划回归可以通过固定样例自动验证。

## 11. Track F：视觉、相机、遥操作、数据采集与领域流程

### 11.1 视觉与相机

当前状态与剩余问题：

- 相机、视觉执行和重定位状态均由组合根显式装配，handler 不再持有 bool executor。
- 模型、标定和工位 profile 已具备严格版本语义；未版本化或版本不匹配的数据会失败。
- 抓取与重定位调试产物使用同一运行会话、原子发布和有界保留策略。
- simulation 使用确定性 fixture，不加载真实模型或访问硬件。
- VisionService 已统一采集操作结果、实际处理帧数/推理次数、延迟、观测处理 FPS
  和模型/标定版本；模型语义质量仍由后续版本化真实图像数据集评测。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-V-001 | P1 | DONE | 相机已接入 ResourceArbiter |
| F-V-002 | P1 | DONE | 相机生命周期归 DeviceRuntime，短任务和长预览/采集均使用显式 CameraSession |
| F-V-003 | P1 | DONE | 外部 UDP 定位由 ApplicationServices 持有的 ExternalLocalizationService 提供并注入 UDP Provider，应用/执行/GUI 均使用显式依赖，旧 GUI 全局接收器已删除 |
| F-V-004 | P2 | DONE | 建立组合根持有的 VisionService；capture/relocalization 统一返回包含稳定 code、operation、run_id 和 artifacts 的 typed result |
| F-V-005 | P2 | DONE | 模型、标定和工位 profile 使用显式版本；工位 schema 原子写入并拒绝 legacy、缺失版本或活动配置不匹配的数据 |
| F-V-006 | P2 | DONE | 抓取/重定位统一使用 scoped artifact run、失败 manifest、staging 原子发布、保留天数/最大运行数和残留清理策略 |
| F-V-007 | P2 | DONE | simulation 组合根注入确定性 VisionPipelineFixture；无模型、无相机执行 capture/relocalization 并生成可审计 fixture 产物 |
| F-V-008 | P3 | DONE | pipeline 改为强类型 VisionPipelineResult 并报告实际帧数/推理次数；VisionService 聚合结果、延迟、观测处理 FPS 和模型/标定版本，通过 server_metrics 暴露且不保存图像/payload；指标开销纳入性能预算 |

### 11.2 遥操作

当前状态：

- 遥操作直接调用 RobotController。
- 与序列执行的互斥只分散在 WebSocket handler 中。
- 高频消息缺少明确背压、超时和控制租约。
- 网络断开后的安全行为需要统一定义。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-T-001 | P0 | DONE | 遥操作会话租约与序列执行互斥 |
| F-T-002 | P0 | DOING | 控制心跳超时、断线释放、逐臂指令流 watchdog 和 RealMan 软件快停/急停链路已实现；其他设备停止和真实硬件时延验收待完成 |
| F-T-003 | P1 | DONE | TeleoperationService 是租约、owner、活动臂、指令计数、最后指令时间和夹爪去重状态的唯一所有者；WebSocket/DataCollection 使用独立 owner |
| F-T-004 | P1 | DONE | WebSocket 已实现单控制者租约、续期心跳、超时监控和断线资源释放 |
| F-T-005 | P1 | DONE | 已实现请求限频、并发上限、有界 WebSocket 入站队列和 TCP 背压；硬件调用移出事件循环，命令不静默丢弃 |
| F-T-006 | P2 | DONE | 应用层统一记录会话、指令、跳过、错误、watchdog 和安全释放事件；审计不包含关节值或夹爪位置，sink 故障不影响设备控制 |
| F-T-007 | P3 | DONE | 应用层提供指令耗时、最大指令间隔、最大抖动和观测吞吐快照；无硬件性能预算纳入统一质量门禁，真实硬件停止时延仍由 F-T-002 验收 |

### 11.3 数据采集

当前问题：

- 应用服务、显式 session/episode 状态机和共享遥操作控制会话已经完成；
  transport 不再持有 recorder、相机会话或采集状态。
- schema、原子发布、容量预检、失败恢复、显式格式和完整性工具已完成。
- 已增加 depth scale、相机畸变/硬件时间戳域、可选 `T_reference_camera` 外参；
  单臂/双臂样本使用主机 monotonic clock 检查最大偏差。
- 已接入实际夹爪位置/力、关节电流、推导关节速度和末端六维力；可选字段使用
  validity mask，不再伪造 `joint_forces`。
- Native RLBench 已增加显式 `--trusted-native` 读取和类型 smoke test；仍需在
  实际安装 RLBench 的受信训练环境及真实硬件数据上执行交付验收。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-D-001 | P1 | DONE | 提取 DataCollectionService |
| F-D-002 | P1 | DONE | 建立 session/episode 状态机 |
| F-D-003 | P1 | DONE | 采集与遥操作共享控制会话 |
| F-D-004 | P2 | DONE | 数据 schema、版本、来源、字段单位和文件 manifest |
| F-D-005 | P2 | DONE | 同目录 staged write、校验后原子发布、残留恢复和容量预检 |
| F-D-006 | P2 | DONE | 显式区分 portable 与 native；删除缺依赖时的伪 RLBench 回退 |
| F-D-007 | P2 | DONE | episode/dataset 完整性 API 与 `robot-data-validate` CLI |
| F-D-008 | P1 | DONE | schema v2 已记录 depth scale、畸变、相机设备/主机时间、可选外参，并以 monotonic clock 限制单臂/双臂样本最大偏差 |
| F-D-009 | P1 | DONE | 统一 ArmTelemetry 已接入实际夹爪/电流/末端力和推导速度；Native 提供显式受信读取 smoke test |

### 11.4 智能加粉领域

当前代码只有一个规则型 `PowderDispenseAgent`；`docs/项目综述.md` 描述的是未来四 Agent + LLM 动态决策架构，两者不能视为同一实现。

工作项：

| ID | 优先级 | 状态 | 工作项 |
|---|---|---|---|
| F-P-001 | P0 | DONE | 达到最大轮次但未达到目标明确返回 `MAX_ROUNDS_REACHED` 失败终态，并映射稳定错误码 `target_not_reached` |
| F-P-002 | P1 | DOING | 加粉流程已接入统一取消、结构化 handler result、可取消等待和安全回位；待真实硬件验收 |
| F-P-003 | P1 | DONE | PowderDispenseResult 保留逐轮前后读数、剩余量、容差、旋转步数、增量和判定，执行日志输出结构化轮次审计 |
| F-P-004 | P2 | DONE | 建立版本化无硬件策略案例，覆盖最终轮次达标/超量、异常下降、最大轮次失败及四级步进收敛；阈值改为显式配置并修复最终轮次终态误判 |
| F-P-005 | P2 | DONE | 专项文档明确当前确定性规则闭环、LLM 仅作读数适配器，以及四 Agent 概念方案不代表当前实现 |
| F-P-006 | P3 | DROPPED | 当前缺少多粉种数据、量化收益和可验证安全约束，本轮不立项且不预埋框架；满足明确数据、指标、安全和离线验收条件后作为独立项目重审 |

## 12. Track G：配置、数据、依赖、测试与交付工程

### 12.1 配置

当前问题：

- 环境变量解析仍集中在组合根使用的 `_EnvironmentConfig` 适配器中，但该对象不再作为
  单例导出，也没有业务层调用方。
- 运行时配置已拆分为不可变的 Runtime、Data、DataCollection、Server、Secret、
  Execution、LLM、Robot、Device、Vision、Voice settings。
- GUI、WebSocket、Application Service、执行 handler、设备 factory、视觉、语音和 LLM
  均接收显式配置快照；数据采集、视觉天平和低层设备不再读取环境变量。

目标：

- 分为 `ServerSettings`、`RobotSettings`、`DeviceSettings`、`VisionSettings`、`LLMSettings`、`VoiceSettings`。
- 启动时集中校验，输出可理解错误。
- 业务模块接收配置快照，不依赖全局可变单例。
- 敏感配置与普通配置分离。

### 12.2 数据与持久化

当前问题：

- 内置动作/技能与本机用户数据已分离；缺失用户库时只安装一次内置目录。
- Action、Skill、Workflow 已统一使用 schema v2、原始备份和原子替换。
- 用户可见“任务”只保存为 `workflows/*.workflow.json`；草稿进入 `drafts/`，旧 `.task`、
  `.workflow` 和备份已移入 `migration-backups/workflow-v1/`。
- WorkflowDocument 使用结构化 Sequence/Action/Loop 根节点，布局独立进入 presentation，
  运行状态不持久化；Repository 查询只读且只认识新格式。
- Action 参数已规范为机器字段和 JSON 类型，Skill 已按领域拆为单文件并删除 Python
  第二事实源；Action/Skill/Workflow Schema 均随 wheel 交付。
- 标定、调试图片和其他运行时数据仍需纳入统一数据根与保留策略。

目标：

- 区分版本控制中的 built-in/example data 与运行时 user data。
- 引入 schema version、迁移、备份和原子写。
- 明确数据根目录，支持部署时配置。
- 用户可见“任务”只对应一种规范 `*.workflow.json` 源文档；编辑布局是 presentation
  元数据，运行时计划由 Compiler 派生且不另行持久化为 `.task`。
- 动作库使用稳定机器字段、全局唯一 ID、强类型参数和显式 revision；Skill 以一个
  `*.skill.json` 文件一个领域能力递归加载，不使用手写索引或 Python 数据副本。
- 格式切换使用显式离线迁移、全量校验、备份和一次提交；正常读取不得写盘，切换后
  删除旧 `.task`、`.workflow`、集合式 skill library 和旧配置入口。

### 12.3 依赖与打包

当前问题：

- `pyproject.toml` 已成为唯一依赖声明，`requirements.txt` 和重复 RealSense 声明已删除。
- 基础安装只保留配置解析依赖；GUI、Server、AI、数据、视觉、语音、KWS 和硬件已拆分
  为可选 extra，并提供 `full` 聚合组。
- `dev` 依赖和冻结 lock 已建立，Windows 已通过 `--all-extras` 冻结同步。
- 已提供 `robot-llm` 标准命令和 `python -m src` 模块入口。
- wheel 构建、内容检查、隔离安装和 console entry point 配置校验已进入统一质量门禁。

目标：

- 以 `pyproject.toml` 为唯一依赖源。
- 按 `server/gui/vision/voice/hardware/dev` 拆分 extra。
- 提供标准 CLI entry point。
- 锁文件与支持平台策略清晰。

### 12.4 测试与 CI

当前问题：

- pytest 已建立 50% 全源码覆盖率门禁；Windows/Linux 质量矩阵和 GUI/Server/Hardware
  可选依赖隔离矩阵已建立。
- Ruff 已覆盖全部 `src`、测试和受管脚本；供应商 SDK 与硬件联调脚本按隔离门禁管理。
- Mypy 已覆盖 61 个核心文件，并整包覆盖 `src/execution`；GUI、语音、部分设备驱动和视觉算法等动态边界仍需逐步扩展。
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
| G-001 | P0 | DONE | pytest 统一收集现有测试；Windows/Python 3.12 CI 使用冻结 lock 执行统一质量入口 |
| G-002 | P0 | DONE | 已建立 fake device/runtime、可脚本化 FakeTransport 和可复用 FakeLLMClient，并覆盖调用记录、故障与生命周期测试 |
| G-003 | P0 | DONE | 内置动作/技能由应用版本交付；组合根仅在用户库缺失时安装，现有用户文件永不覆盖 |
| G-004 | P1 | DONE | unit/contract 已覆盖 runtime、资源、执行状态、依赖边界及全部 WebSocket action、错误码和 route/schema 所有权 |
| G-005 | P1 | DONE | Ruff 覆盖收敛主线与测试；Mypy 覆盖 WebSocket、DeviceRuntime、ExecutionRuntime 和 LLM 核心类型 |
| G-006 | P1 | DONE | 删除 requirements 副本与重复依赖；pyproject + uv.lock 成为唯一声明和冻结依赖图 |
| G-007 | P1 | DONE | 增加 `--check-config`、集中活动配置/路径/端口/超时校验、占位凭据拒绝和统一敏感字段脱敏 |
| G-008 | P1 | DONE | 动作、任务、技能使用 schema v1；旧格式一次性前向迁移并保留 v0 原始备份，所有新写入原子替换 |
| G-009 | P2 | DONE | 配置拆分为不可变领域 settings；业务层移除全局单例和隐式回退，敏感配置单独建模 |
| G-010 | P2 | DONE | 依赖按 server/gui/ai/data/vision/voice/kws/hardware 分组，提供 full 聚合组并刷新冻结 lock |
| G-011 | P2 | DONE | 提供 `robot-llm`/`python -m src` 入口；wheel 构建、隔离安装和入口 smoke test 纳入质量门禁 |
| G-012 | P2 | DONE | pytest-cov 纳入统一质量入口并全量统计项目 `src`；初始 44% 门禁在 GUI smoke 纳入后提升到 50%，生成 XML 报告；重复 RealMan 绑定删除后不再需要生成代码排除项 |
| G-013 | P2 | DONE | Windows/Linux Python 3.12 执行统一质量入口；GUI/Server/Hardware extra 在两平台隔离安装并执行无外设冒烟验证 |
| G-014 | P2 | DONE | 集中日志组件输出控制台文本和 JSON Lines 文件；每日轮转、保留周期可配置；执行线程自动绑定 run_id，WebSocket 请求绑定 request_id/action |
| G-015 | P3 | DONE | 版本化预算覆盖请求解析、动作校验、资源租约、schema 快照和离线 LLM 规划；预热后多样本取中位数，JSON 报告和超预算失败进入统一质量门禁 |
| G-016 | P2 | DONE | 表现层采用局部 MVC/MVVM：GUI 已按 views/view_models/controllers/bridges 拆分，WebSocket 已按 protocol/controllers/security/metrics 拆分；ApplicationServices 不再暴露 DeviceRuntime，表现层统一调用 Application Service |
| G-017 | P1 | DONE | LLMRegistry 已提升为 ApplicationServices 持有的唯一进程级生命周期；GUI、WebSocket 和 Voice 共享 provider health、熔断与指标，附加服务只取消自身会话，应用宿主统一关闭 Provider |
| G-018 | P2 | DONE | 原 core 已拆分并删除：bootstrap、configuration、persistence、domain、geometry、observability 形成显式职责；domain/configuration/persistence/geometry 单向依赖由 AST 测试保护，launcher 与 console entry 已直切 |
| G-019 | P2 | DONE | 删除无引用脚本、调试 Widget、源码内轨迹/图片和旧补偿/单相机环境变量；瓶子抓取迁入 vision 且调试产物进入受管目录；Mypy、AST 旧路径和 wheel 禁止内容门禁已扩展 |

### 12.6 第二轮目录职责与 Provider 边界

本轮审查确认：现有目录问题并非全部属于重复实现。处理时必须先判断状态所有者和
变化原因，再选择移动、改名、拆分或保留。禁止只为缩短顶层目录列表建立
`common`、`services`、`infrastructure` 等新的万能目录。

| ID | 优先级 | 状态 | 内容 |
|---|---:|---|---|
| G-020 | P1 | DONE | 视觉天平已抽象为 `BalanceReader` 设备能力和强类型 `BalanceReading`；真实 Provider 复用 `CameraAccessService` 与唯一 `LLMRegistry`，simulation 使用确定性 fake，由 DeviceRuntime 管理生命周期；执行策略同时声明加粉装置和电子秤资源，旧动态导入、独立 OpenCV/HTTP 实现及 VVEAI 专用配置已删除，不保留兼容入口 |
| G-021 | P2 | DONE | Qt 通用组件和 AI Assistant 已迁入 `gui/views`，AI Controller 与音频播放生命周期进入 `gui/controllers`；生产代码、测试、Mypy、wheel 门禁和文档均直切新路径，`widgets` 与 `ai_integration` 顶级目录已删除，不保留转发模块 |
| G-022 | P2 | DONE | 转圈注液与确定性闭环加粉已迁入 `execution/workflows`，handler 和专项测试直切新路径；动作参数、typed result、取消/暂停和安全回位语义保持不变，`actions` 与 `agents` 顶级目录已删除，不保留转发模块 |
| G-023 | P2 | DONE | `devices/transports/devices` 已删除；ElectricGripper、StepperMotor 只有粉末装置调用方，因此直接迁入 `tools/powder_dispenser`；relay/tool-changer/pipette Adapter 分别与 Driver 共置；Transport 顶层不再导出语义设备，只保留通信、策略、协议、错误和测试 fake；源码与 wheel 边界禁止旧目录回归 |
| G-024 | P2 | DONE | 相机已建立静态 Provider/Registry；RealSense 与 OpenCV 使用平行 Provider 并共享 `CameraSource`/camera capability，`CAMERA_PROVIDER` 只接受显式注册值，未知值在 DeviceRuntime 装配阶段失败；删除隐式 RealSense 回退、异常吞并和旧 `camera_factory.py`，不保留 `auto`/`webcam` 兼容值 |
| G-025 | P2 | DONE | 移动底盘已按当前 TCP 产品拆为 Provider/Adapter/Client 纵向切片，Client 独占 socket/JSON 帧，Adapter 实现 `MobileBase`，Provider 只负责装配；显示屏以静态 ProviderDefinition/Registry 取代字符串动态导入；删除旧底盘 Controller/Client 路径且未建立无第二实现的底盘 Registry |
| G-026 | P2 | DONE | 视觉内部已形成 `pipelines/`、`relocalization/`、`artifacts.py`、`cli/` 四个明确边界，抓取算法和离线重定位 CLI 不再平铺/混入算法包；外部 Tag 定位新增 typed reading、Provider contract、UDP Provider 和 simulation Null Provider，socket/线程由 Provider 独占，Application Service 只负责新鲜度/有效性策略；配置、服务字段和执行参数统一使用 `external_localization`，与视觉工位重定位明确区分 |
| G-027 | P3 | DONE | 删除混合职责的 `execution/action_handlers.py`，结果/上下文/Protocol 迁入 `handler_api.py`，分派迁入 `handler_registry.py`，无设备核心 handler 迁入 `handlers/core.py`；README 项目结构、动作模型路径和相机 Provider 说明已更新，旧 application/vision/execution 路径加入禁止回归清单，Mypy 同步覆盖新边界 |
| G-028 | P0 | DONE | GUI Qt binding 一次性直切 PySide6；生产代码、测试、可选依赖、锁文件、打包 smoke 和文档同步迁移，使用原生 `Signal`/`Slot`，不保留兼容层；附加服务结果通过 GUI 线程 QObject receiver 驱动启动卡片到主窗口的过渡；架构测试禁止旧 binding 再次进入源码、脚本、测试或 `pyproject.toml` |
| G-029 | P1 | DONE | `WorkflowDocument` 已升级 schema v2 和唯一 `*.workflow.json`；结构化 `Sequence/Action/Loop` 根节点替代线性 `order` 持久化语义，布局进入独立 presentation，运行状态不写入定义；节点采用显式联合类型，可后续增加 Parallel/Condition 而未预建通用 BPMN |
| G-030 | P1 | DONE | Action 已直切 `actions/library.json` schema v2；拒绝重复 ID/名称并逐项执行 ActionSchema 参数校验，34 个机械臂点位由字符串迁移为 6 元素 JSON 数组；Workflow 当前保存完整 ActionDefinition 快照以保证可复现，ID 仅用于来源追踪，不在执行时动态解析最新目录版本 |
| G-031 | P1 | DONE | 集中式技能集合已拆为 `skills/<domain>/<id>.skill.json`；SkillRegistry 确定性递归加载、逐文件 schema/动作类型/参数/绑定校验、跨文件 ID 唯一校验并在全部成功后替换内存目录；`default_skills.py` 和手写索引已删除，内置与用户数据共享 JSON 格式 |
| G-032 | P1 | DONE | Action/Skill/Workflow 显式迁移均已完成；`robot-workflow-data` 默认 dry-run，临时生成、重新加载和目标冲突校验后原子发布，17 个旧任务及 `.bak` 已移入可恢复归档；runtime 旧集合、`.task` 和旧 `.workflow` 入口全部删除 |
| G-033 | P2 | DONE | `actions/`、`skills/`、`workflows/`、`drafts/`、四个目录级配置、三个版本控制内 JSON Schema、`$schema`、内置资源和 wheel package-data 已落地；GUI 文件对话框与 WebSocket 任务名统一使用 `*.workflow.json` |

完成标准：

- 顶层 `widgets/`、`ai_integration/`、`actions/`、`agents/` 已删除且无 import 转发。
- `devices/transports/` 不包含 ElectricGripper、StepperMotor 或其他语义设备。
- 天平读数不直接创建相机、不绕过 LLMRegistry，也不由 ActionEngine 装配具体实现。
- 相机未知 Provider 显式失败；RealSense/OpenCV 通过同一 capability contract。
- Application Service 不直接创建定位 socket；外部定位与视觉重定位名称和状态所有权清晰。
- AST 边界测试、Mypy、simulation、完整质量门禁和 wheel smoke 全部通过。

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
| ADR-M-007 | Accepted | 配置模型 | 组合根一次解析环境，向业务层注入不可变领域 settings；敏感配置独立快照 |
| ADR-M-008 | Accepted | 数据目录 | built-in catalog 与可配置 user data root 分离；只初始化缺失文件 |
| ADR-M-009 | Accepted | GUI 状态管理 | application service + Qt adapter/view-model；业务状态不由 QWidget 持有 |
| ADR-M-010 | Rejected | 四 Agent 粉末方案 | 当前不立项、不预埋框架；满足版本化数据、量化收益、确定性安全边界和离线回放条件后作为独立项目重新评审 |
| ADR-M-011 | Accepted | 目录架构 | 使用按领域组织的模块化单体；硬件统一进入 `src/devices/`，不按全局 models/controllers/services 技术目录横向堆积 |
| ADR-M-012 | Accepted | MVC 适用范围 | MVC/MVVM 只用于 GUI、WebSocket/HTTP 表现层；执行与硬件采用 Application Service + Ports/Adapters |
| ADR-M-013 | Accepted | 硬件实现角色 | Service 编排用例，Provider 创建产品，Adapter 实现能力，Driver 封装厂商协议，Transport 负责通信；禁止用 `XxxService` 混称底层驱动 |
| ADR-M-014 | Accepted | GUI 工作流画布边界 | 使用受约束画布和纯 WorkflowDocument；复用 CompositionService、SequenceEntry、ExecutionManager 与 ActionHandlerRegistry，不新增 GUI 执行器、Handler 或持久化仓库 |
| ADR-M-015 | Accepted | GUI 工作台信息架构 | 只借鉴 VS Code 的 Top Menu/Activity Bar/Side Bar/Editor/Bottom Panel/Status Bar 分区；保持工业控制的触控尺寸、常驻安全命令和单一应用状态源，不引入扩展宿主或通用多编辑器框架 |
| ADR-M-016 | Accepted | 用户编排数据格式 | 用户可见“任务”以 `*.workflow.json`/WorkflowDocument 作为唯一可编辑源；`.task` 线性快照不再作为并行持久化格式，执行计划由 Compiler 派生。动作、技能、工作流分别使用版本化 JSON Schema；迁移采用离线一次切换，不在读取时写盘或长期双读 |
| ADR-M-017 | Accepted | 自然语言命令分类 | Command 是交互请求，Action 是原子操作，Skill 是可复用领域能力，Workflow 是保存的任务；Planner 产出四类 typed command，单步语音控制不机械包装成 Skill，所有物理命令仍经 CommandRuntime 审批和唯一执行运行时 |

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
       |
       v
M5 模块目录与依赖边界治理
  ├─ hardware directory convergence
  ├─ provider/adapter/driver vertical slices
  ├─ presentation MVC/MVVM cleanup
  ├─ application-owned LLM lifecycle
  ├─ core responsibility split
  └─ legacy/import-boundary removal
       |
       v
M6 GUI 工作流画布与表现层收敛
  ├─ feature-parity baseline and ADR
  ├─ workflow document/validator/compiler
  ├─ constrained touch canvas and loop container
  ├─ MainWindow shell/page decomposition
  ├─ accessibility/performance/offscreen gates
  └─ one-time cutover and old-editor removal
       |
       v
M7 GUI 工作台信息架构与空间收敛
  ├─ top menu and activity navigation
  ├─ resource side bar pages
  ├─ canvas-first editor workspace
  ├─ bottom details panel and status bar
  ├─ persistent safety command strip
  └─ SVG resources and layout persistence
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

### 14.6 M5：模块目录与依赖边界治理

完成条件：

- 硬件代码只存在于 `src/devices/`，旧硬件顶级目录和导入均已删除。
- RealMan 形成 provider/adapter/driver 垂直切片，第二供应商可平行接入。
- 表现层不再直接读取或控制 DeviceRuntime，只依赖 Application Service 和 DTO。
- GUI、WebSocket 与 Voice 共用应用级唯一 LLMRegistry 生命周期。
- 原 `core` 已拆分并删除，启动、配置、持久化、领域模型、几何和日志各有唯一目录。
- GUI Qt 组件只存在于 `src/gui/`；复合执行流程只存在于
  `src/execution/workflows/`，不再使用单模块顶级 `widgets/actions/agents` 目录。
- Transport 目录只处理通信；天平、相机、底盘和显示屏的产品变化点通过明确
  capability 与 Provider/Adapter/Driver 边界隔离。
- 视觉 pipeline、UDP 外部定位和视觉工位重定位具有互不混淆的模块与状态所有者。
- AST 依赖边界、Mypy、simulation、完整质量门禁和 wheel smoke 全部通过。

### 14.7 M6：GUI 工作流画布与表现层收敛

完成条件：

- 受约束画布保持动作、Loop、任务组合、AI/语音预览、轨迹、设备状态和执行控制功能等价。
- WorkflowDocument 是纯编辑模型，并经 Validator/Preflight/Compiler 转换为规范 SequenceEntry。
- GUI 只通过现有 ExecutionBridge 提交唯一 ExecutionManager，仓库中不存在平行执行器、Handler 或持久化入口。
- MainWindow 只承担窗口壳、导航和顶层 Qt 生命周期，页面行为由 Controller/Application Service 协调。
- 鼠标、键盘、纯触控、高 DPI、可访问性、100/500 节点性能和 Qt offscreen 回归通过。
- 数据备份和一次性前向迁移通过后直接切换，旧列表编辑器与旧路径已经删除。

### 14.8 M7：GUI 工作台信息架构与空间收敛

完成条件：

- 主窗口只有一套 Workbench 壳层，Activity Bar 切换独立资源页，画布成为默认主工作区。
- Side Bar 和 Bottom Panel 可拖动、可收起并恢复布局；分隔线视觉保持 1 px，命中区满足鼠标与触控要求。
- 设备状态/位姿、日志和基础控制按需展示，Status Bar 使用同一 ViewModel 快照提供常驻摘要。
- 开始、暂停/恢复、停止任务、快速停止和设备急停在所有布局状态下保持清晰可见；软件急停语义不变。
- 文件/编辑/视图/执行/设备菜单、画布工具栏、上下文菜单和快捷键不存在相互矛盾的重复状态。
- SVG/Qt Resource 图标、许可证、深浅主题、高 DPI、可访问名称、wheel 打包和布局恢复回归全部通过。
- 旧箭头抽屉、悬停扩宽控制条、纵向堆叠主布局和重复的大按钮入口已删除，不保留兼容层。

### 14.9 M8：用户数据模型与自然语言命令收敛

完成条件：

- 启动 GUI 时已保存任务立即显示，保存/删除后的事件刷新与首屏加载使用同一查询入口。
- `.task`、`.workflow` 已通过显式工具一次迁移为唯一 `*.workflow.json`，正常读取无写盘副作用，运行时不存在双格式分支。
- WorkflowDocument v2 区分执行控制流和 presentation 元数据，运行状态不进入定义；现有顺序与 Loop round-trip、编译映射和执行语义不回退。
- 动作 ID 全局唯一，持久化参数使用规范机器字段和 JSON 类型，ActionSchema 继续是 UI/API/Skill/Planner 的唯一字段定义。
- 每个 Skill 独立保存为 `*.skill.json`，跨文件 ID/动作引用/参数绑定严格校验，Python 内不存在第二份完整技能目录。
- Planner 可区分 Action、Skill、Workflow 和 ExecutionControl；夹爪、相对移动等单步语音指令经过消歧、限幅、预览和风险确认后进入唯一 ExecutionManager。
- 迁移报告、JSON Schema、配置、示例、golden、契约、GUI smoke、性能和 wheel 内容门禁全部同步；旧入口直接删除，不设置兼容开关。

## 15. 质量门禁

### Gate 0：任何代码变更

- `uv run --frozen --group dev python scripts/run_quality_checks.py`
- 新增代码无明显循环依赖。
- 不提交密钥、真实设备凭据和用户运行数据。
- 相关文档同步更新。

本地与 CI 的命令、检查范围和分层规则见[工程质量门禁](quality-gates.md)。

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
| 阻塞 SDK 无法取消 | P1 | shutdown 卡住 | 动作控制策略显式区分协作取消、调用后取消和设备辅助停止；继续完成真实时延验收 |
| 配置/任务格式损坏 | P1 | 无法启动或数据丢失 | schema version、原子写、备份 |
| simulation 与真实行为偏差 | P1 | 测试假阳性 | 共享状态机/校验，硬件验收 |
| GUI 线程错误 | P1 | 崩溃或界面异常 | Qt adapter、主线程更新规则 |
| 相机/串口资源泄漏 | P1 | 后续任务不可用 | DeviceRuntime、session、finally/shutdown |
| 文档目标与当前实现混淆 | P2 | 错误决策 | 文档分类、状态和 superseded 标记 |
| 重构范围过大 | P2 | 长期分支、难以合并 | 按能力域拆分，每个域直接切换并删除旧实现 |
| 按目录形状机械合并不同状态所有者 | P2 | 产生万能模块、循环依赖和隐式共享状态 | 先确认唯一所有者与变化原因；合理分层只改名或补文档，不强制合并 |
| 语义设备继续藏在 Transport/视觉工具模块 | P1 | 绕过资源、生命周期和错误治理 | 设备 capability + Provider 注册；AST 禁止上层直接创建具体相机、socket 和硬件客户端 |

## 19. 项目级完成定义

全部满足后，本轮项目重构才可标记完成：

### 架构

- [x] GUI、WebSocket、voice 和 AI 不直接操作具体动作实现。
- [x] ExecutionManager 是唯一序列执行所有者。
- [x] DeviceRuntime 是唯一硬件生命周期所有者。
- [x] 天平、相机等设备型能力不绕过 DeviceRuntime、资源租约和共享 Provider。
- [x] teleop、sequence、测试和直接控制服从资源仲裁。
- [x] runtime/domain/configuration/persistence/geometry 不反向依赖 GUI 或 WebSocket。
- [x] GUI Qt 代码已收敛到 `src/gui/`，执行复合工作流已收敛到
  `src/execution/workflows/`，Transport 不包含语义设备。

### 安全与协议

- [x] cancel、quick-stop、emergency-stop 语义和能力明确。
- [x] WebSocket 写操作有认证、权限和审计。
- [x] 多客户端有控制权和冲突策略。
- [x] 请求、执行、设备错误可通过 ID 关联。

### 领域能力

- [x] WebSocket action schema 和 Skill 参数展开使用统一 schema。
- [x] GUI 通用表单、动作持久化入口和 handler 参数模型完全由统一 schema 驱动。
- [x] 未知动作和非法参数在执行前拒绝。
- [x] 相机、视觉、标定和工位数据有清晰生命周期。
- [x] 数据采集和加粉流程有结构化状态与结果。

### 工程质量

- [x] pytest、Windows 最小 CI、核心 Ruff 和 Mypy 门禁生效。
- [x] 覆盖率阈值和 Windows/Linux 测试矩阵生效。
- [ ] Ruff/Mypy 静态检查在历史问题清零后扩展到全仓。
- [x] 无硬件 simulation 可回归 GUI、统一执行、取消和资源清理主流程。
- [ ] 关键真实硬件验收有记录。
- [x] 配置、依赖、打包和平台支持策略清晰。
- [x] 动作、任务和技能具有版本、原子写和一次性前向迁移。
- [x] 内置动作和技能可重复交付且不覆盖用户数据。
- [x] 文件日志结构化、按日轮转并可通过 request_id/run_id 关联请求和执行。
- [x] 无 I/O 关键热路径具有版本化性能预算和跨平台回归门禁。
- [x] legacy 执行实现和过期文档已清理。
- [x] README、目录职责表和实际源码结构一致，旧目录不会通过打包或导入重新出现。

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
| 2026-07-31 | M4 | B/F/G | 定位、离散安全态、遥操作流控和测试能力收敛 | B-017、F-V-003、F-T-004、F-T-005、F-P-003 → DONE；G-002 DOING → DONE | 定位服务应用级持有；停止统一应用离散安全态；遥操作阻塞 I/O 移出事件循环；补齐粉末逐轮审计和共享 Transport/LLM fake | - |
| 2026-07-31 | M4 | F | 遥操作会话状态收敛 | F-T-003 DOING → DONE；F-T-002 保持 DOING | 将 owner/臂/计数/最后指令/夹爪状态收敛到 TeleoperationService；WebSocket 和 DataCollection 独立持有 owner；增加逐臂指令流 watchdog 和控制租约联动释放 | - |
| 2026-07-31 | M4 | B | 串口 Transport 与工具架设备所有权收敛 | B-008 TODO → DONE；B-011/B-012 TODO → DOING | 六类生产串口设备统一使用组合根注入的 SerialTransport；增加有限打开重试、结构化传输错误和协议测试；工具架放枪按需租用并注入移液枪能力；删除身体轴内嵌 Tk GUI及旧串口双入口 | 303 tests + 27 subtests，14 golden cases；完整质量门禁和 wheel smoke |
| 2026-07-31 | M4 | B/C/G | 设备错误语义整批收敛 | B-011 DOING → DONE | 新增稳定设备错误分类和安全用户消息；Transport、协议、RealMan、生命周期、动作、手动控制、遥操作及安全停止统一归一化；category/raw code 贯通 step/terminal event、快照、状态 API；核心错误边界加入 Mypy | 308 tests + 27 subtests，14 golden cases |
| 2026-07-31 | M4 | B/G | PWM 颈部动作链路与旧设备包装收敛 | B-010 TODO → DONE；B-012 DOING → DONE | 颈部水平/垂直/双轴/复位接入统一 schema、handler、控制策略和 DeviceRuntime；驱动改为显式拒绝越界 PWM/时长；删除无调用方 tapping_device 包装和伪兼容导出；修正 WebSocket watchdog 测试同步条件 | 313 tests + 28 subtests，14 golden cases；完整质量门禁和 wheel smoke |
| 2026-07-31 | M4 | C/G | WebSocket 传输安全与可观测性收敛 | C-011/C-013 TODO → DONE | 远程直连强制 TLS/认证/Origin；代理模式限制同机 loopback 且不信任转发头；新增 server_metrics 聚合连接、请求、限流、延迟、慢发送和超时断连；补首次发送失败清理和超时连接关闭 | 323 tests + 32 subtests，14 golden cases；完整质量门禁和 wheel smoke |
| 2026-07-28 | M2 | A/C/D | GUI 与附加服务统一宿主 | A-013/C-014 TODO → DONE | 删除 GUI/Server 二选一组合路径；WebSocket 进入受管理 asyncio 线程并共享唯一 ApplicationServices，默认仅监听本机 | - |
| 2026-07-28 | M2 | C/D | 编排状态与持久化收敛 | C-015 TODO → DONE | GUI/WebSocket 不再直接访问 JSON 存储；动作、任务和当前序列由线程安全 CompositionService 独占，写入采用原子替换并发布跨线程变更事件 | - |
| 2026-07-29 | M2 | A/B | 动作 handler 与执行控制首批收敛 | B-007 TODO → DOING；A-007 保持 DOING | 建立唯一 ActionHandlerRegistry、注册完整性校验和统一动作 deadline/cancel 上下文；首批拆出 WAIT/INSPECT，阻塞调用不使用脱离资源租约的后台超时线程 | - |
| 2026-07-29 | M2 | A/B | motion handlers 收敛 | A-007/B-007 保持 DOING | 机械臂、身体和底盘动作从 ActionEngine 物理拆分；设备调用统一经过 deadline/cancel 边界，重试与轮询参数配置化，并增加 fake capability 测试 | - |
| 2026-07-29 | M2 | A/B/F | manipulation handlers 收敛 | A-007/B-007/F-P-002 保持 DOING | MANIPULATE 改为执行器子注册表；快换手、继电器、夹爪、移液枪、表情屏、加粉及转圈注液移出 ActionEngine；非法参数在设备初始化前拒绝，智能加粉等待支持取消并保留安全回位 | - |
| 2026-07-29 | M2 | A/B | domain handlers 收敛 | A-007/B-007 保持 DOING | 换枪、轨迹、视觉抓取和视觉重定位移出 ActionEngine；参数先校验再初始化设备，轨迹轮询配置化，视觉 executor 可注入，取消/超时继续向统一运行时透传 | - |
| 2026-07-29 | M2 | A/B/C | 结构化 handler result | A-007 DOING → DONE | 全部 handler 直接切换为 ActionHandlerResult，不保留 bool 兼容；稳定 code、message、operation、device_id 贯通运行时快照和事件，并由 WebSocket 状态/步骤失败协议输出 | - |
| 2026-07-29 | M2 | A/B/C | 动作控制策略矩阵 | B-007/ER-011 保持 DOING | 注册表绑定 handler 与不可变控制策略；全部动作参数分支声明取消模式、涉及设备和停止目标，执行前拒绝能力矛盾，并通过 step_started/WebSocket 输出；RealMan 最大停止延迟待硬件验收 | - |
| 2026-07-29 | M2/M3 | A/B/F | 资源所有权与相机会话收敛 | B-002/F-V-001/F-V-002 DOING/TODO → DONE | 序列按动作策略申请精确设备租约；设备生命周期纳入仲裁；GUI/WS 相机测试、语音视觉、WebSocket 预览和数据采集统一使用 CameraAccessService/CameraSession，采集先取得遥操作租约再启动记录 | - |
| 2026-07-29 | M2 | B | 机械臂 Provider 与工具架配置收敛 | B-015 TODO → DOING；B-016 TODO → DONE | 建立 Provider 注册表和共享核心契约测试；RealMan 连接、型号、运动/夹爪及工具架参数强类型化，换枪工作流移入 adapter，删除 controller 旧方法与 `arm_sdk/config.py` | - |
| 2026-07-29 | M3 | C/F | WebSocket 写安全边界 | C-001/C-002 TODO → DONE；C-003 TODO → DOING | 新增共享密钥认证、单控制客户端租约、心跳/超时/断线释放和结构化安全审计；未配置密钥时写操作默认拒绝，观察者断线不再停止控制者遥操作 | - |
| 2026-07-29 | M3 | A/C | WebSocket 请求与执行关联 | C-003/C-004/C-005 DOING/TODO → DONE | 新增请求上下文和统一错误信封；直接响应与执行 accepted/step/log/terminal 贯通 request_id/action/run_id，执行接受及最终成功/失败/取消形成两阶段审计；未预期异常被连接边界隔离 | - |
| 2026-07-29 | M3 | C | WebSocket 版本、流控与投递语义 | C-006/C-007/C-008 TODO → DONE；C-012 TODO → DOING | 强制 api_version=2.0 且不保留旧协议；配置消息/频率/并发/队列/发送超时，明确请求单播、系统广播和相机订阅投递并增加协议契约测试 | - |
| 2026-07-29 | M3 | E/F | AI 执行审批与加粉终态收敛 | E-001/F-P-001 TODO → DONE | 删除自动执行配置和死事件；预览必须通过校验并由 GUI/WebSocket 显式确认；加粉最大轮次未达标改为显式失败并输出 `target_not_reached` | - |
| 2026-07-29 | M3 | E | Skill action type 显式校验 | E-005 TODO → DONE | 删除未知类型回退 MOVE；映射从 `ActionType` 自动生成，校验结果增加稳定 `ValidationCode`，非法技能不会生成预览 | - |
| 2026-07-29 | M3 | E/C | Action/Skill Schema 收敛 | E-006 TODO → DONE | 提取覆盖全部 ActionType 的唯一 action schema；WebSocket 删除内联副本；Skill 参数改为强类型、单位和显式绑定，展开前拒绝未知输入、无效绑定、单位冲突及越界动作参数 | - |
| 2026-07-29 | M3 | E/C | AI/语音命令治理整批收口 | A-010/E-002/E-003/E-004/E-007/E-008/E-009 TODO/DOING → DONE | 新增进程级 CommandRuntime；删除 GUI/WS 私有预览缓存和重复 SkillEngine；预览使用 ID/版本/TTL/来源隔离/单次消费，高风险二次确认；会话与执行控制分离；统一交互超时、取消和 LLM close | 159 tests + 26 subtests |
| 2026-07-30 | M3 | C | WebSocket 领域拆分与 typed contract | C-009/C-010/C-012 TODO/DOING → DONE | 将执行、编排、AI/聊天、设备/相机、遥操作/采集整体迁入领域 handler；Server 从 4222 行降至约 1400 行；新增 route registry、不可变 request DTO、response DTO 和覆盖全部 action 的严格 payload schema，不保留裸字典/未知字段兼容 | 162 tests + 26 subtests |
| 2026-07-30 | M3 | F | 数据采集应用服务与状态机 | F-D-001/F-D-002/F-D-003 TODO → DONE | 新增 DataCollectionService 和显式 session/episode/故障状态；recorder、相机会话及共享遥操作控制从 WebSocket 下沉，阻塞操作移出事件循环；控制租约释放、安全停止和设备关闭统一清理，不保留 host 旧状态 | 170 tests + 26 subtests |
| 2026-07-30 | M3 | F/G | 数据采集格式与存储治理 | F-D-004/F-D-005/F-D-006/F-D-007 TODO → DONE | 新增 schema v1、来源/单位/文件哈希 manifest、portable NPZ 与 Native RLBench 显式格式；同目录 staged write 校验后原子发布，加入容量预检、范围受限残留恢复和离线验证 CLI；删除旧 recorder/formatter 与伪 RLBench 回退 | 183 tests + 26 subtests |
| 2026-07-30 | M3 | B/F | 多臂真实遥测与采集时间语义 | F-D-008/F-D-009 TODO → DONE | schema 直接升级 v2；新增统一 ArmTelemetry/DepthCameraFrame，记录 depth scale、畸变、设备/主机时间、可选外参和实际夹爪/电流/末端力；单/双臂采样强制最大 monotonic 偏差，可选字段使用 validity mask；Native 不伪造 joint_forces，并增加显式受信 pickle 类型 smoke test | 187 tests |
| 2026-07-30 | M3 | E | LLM Provider 治理与规划回归 | E-010/E-011/E-012 TODO → DONE | 新增所有 task 共用的 provider health、熔断、半开和显式 fallback；记录 Prompt/请求/provider/model/技能目录来源；分类失败不再静默伪装成功；建立 strict schema v1 离线 golden runner | 195 tests，14 golden cases |
| 2026-07-30 | M4 | A/C/G | pytest、协议契约与静态质量门禁 | A-008/G-001/G-004/G-005 TODO/DOING → DONE | 建立本地/CI 唯一质量入口和 Windows/Python 3.12 冻结依赖流水线；Ruff 清零收敛主线基础问题，Mypy 检查核心 typed boundary；全部 WebSocket action 具备独立最小合法请求 golden contract，route/schema、payload 边界、稳定错误码和响应 DTO 纳入 pytest | 262 tests + 26 subtests，14 golden cases |
| 2026-07-30 | M4 | G | 依赖、配置与用户数据治理 | G-003/G-006/G-007/G-008 TODO → DONE | 删除 requirements 双来源；内置 catalog 与可配置用户数据根分离；动作/任务/技能统一 schema v1、v0 原始备份、一次性迁移和原子替换；启动前集中校验活动端口、超时、路径、网络暴露及占位凭据，诊断统一脱敏 | 280 tests + 26 subtests，14 golden cases |
| 2026-07-30 | M4 | G | 配置、依赖与可安装交付收敛 | G-009/G-010/G-011 TODO → DONE | 删除公开 Config 单例和所有业务层隐式读取，注入十一类不可变领域 settings 并分离 secrets；数据采集与视觉天平改为显式配置注入；拆分 optional extras、刷新 uv.lock，新增 `robot-llm`/模块入口及 wheel 构建、隔离安装、console smoke 质量门禁 | 285 tests + 26 subtests，14 golden cases；Windows all-extras sync + wheel smoke |
| 2026-08-03 | M4 | G | 覆盖率与跨平台依赖矩阵 | G-012/G-013 TODO → DONE | pytest-cov 进入统一质量入口，以除生成式 RealMan ctypes 绑定外的全量 `src` 建立 44% 门禁；Windows/Linux 运行统一质量矩阵，GUI/Server/Hardware extra 在两个平台分别隔离安装并执行无外设冒烟验证 | 324 tests + 32 subtests，覆盖率 44.42%，14 golden cases；本地完整门禁通过，Linux 由 CI 执行 |
| 2026-08-03 | M4 | G | 运行日志治理 | G-014 TODO → DONE | 删除启动器内嵌日志初始化；新增独立 LoggingSettings 和集中日志组件，控制台保持可读文本，文件统一 JSON Lines、每日轮转和按天保留；ContextVar 传播 operation/request_id，ExecutionManager worker 显式绑定 run_id | 331 tests + 32 subtests，覆盖率 44.65%，14 golden cases；完整质量门禁和 wheel smoke |
| 2026-08-03 | M4 | G | 性能预算与回归监控 | G-015 TODO → DONE | 新增 strict schema v1 性能预算和无硬件 runner；批量执行请求解析、动作校验、资源租约、schema 深拷贝与 LLM golden suite，预热后取 5 次样本中位数；机器可读原子报告和超预算失败进入 Windows/Linux 统一质量入口 | 336 tests + 32 subtests，覆盖率 44.65%，14 golden cases；5/5 性能预算通过，完整质量门禁和 wheel smoke |
| 2026-08-03 | M4 | A/D | GUI 启动状态机与 simulation smoke | A-009 DOING → DONE；D-010/D-012 TODO → DONE | 删除两个启动布尔和构造期嵌套 QEventLoop；新增显式 lifecycle，语音等待改为非阻塞 signal + timer；真实 MainWindow 在 offscreen simulation 中验证共享服务、fake 设备启动、开始/暂停/恢复/取消、终态日志、资源释放和关闭 | 342 tests + 32 subtests，覆盖率 52.35% 并将门禁提升至 50%；完整质量门禁、5/5 性能预算和 wheel smoke |
| 2026-08-03 | M4 | D/G | GUI 应用状态与 schema 表单收敛 | D-005/D-006/D-007/D-008、ER-018 TODO → DONE | TaskComposerService 独占组合草稿；Device/Execution ViewModel 从运行时快照派生状态；删除 MainWindow 平行布尔和 ExecutionBridge 控制双入口；1300 余行逐动作表单分支替换为唯一 schema 通用渲染与校验；新增边界进入 Mypy | 348 tests + 32 subtests，覆盖率 55.09%；21 个 Mypy 文件、14 golden、5/5 性能预算和 wheel smoke 全部通过 |
| 2026-08-03 | M4 | D/G | GUI 通知、协作与关闭生命周期收敛 | D-011 TODO → DONE | 新增 typed GuiNotificationCenter；MainWindow 删除直接 QMessageBox 和散落日志出口；通知通过 QObject signal 回到 GUI 线程；AI Assistant 删除 MainWindow 反向引用并改用窄 Qt 信号；活动执行关闭时立即请求取消，相机/交互先非阻塞停止，事件循环退出后按有界预算等待 | 352 tests + 32 subtests，覆盖率 55.30%；22 个 Mypy 文件、14 golden、5/5 性能预算和 wheel smoke 全部通过 |
| 2026-08-03 | M4 | D/G | GUI 稳定视图域拆分 | D-013/D-014 TODO → DONE | 提取 ActionLibraryView、WorkflowEditorView、DeviceStatusView、DeviceControlView；MainWindow 删除约 800 行控件构造与旧属性，改为连接参数化意图信号和调用渲染接口，不保留兼容别名；新增组件进入 Mypy | 352 tests + 32 subtests，覆盖率 55.34%；22 个 Mypy 文件、14 golden、5/5 性能预算和 wheel smoke 全部通过 |
| 2026-08-04 | M4 | F/G | 视觉服务与数据治理整批收口 | F-V-004/F-V-005/F-V-006/F-V-007 TODO → DONE | 新增 VisionService/typed result；模型、标定、工位 profile 严格版本化；调试产物按 run staging、manifest、原子发布和有界保留；simulation 注入确定性 fixture；删除旧重定位调试目录和 bool executor 双入口 | 359 tests + 32 subtests，覆盖率 55.99%；28 个 Mypy 文件、14 golden、5/5 性能预算和 wheel smoke 全部通过 |
| 2026-08-04 | M4 | F | 遥操作审计与软件性能基线 | F-T-006/F-T-007 TODO → DONE | 新增 typed TeleoperationObservability，统一会话、指令、跳过、错误、watchdog 和安全释放审计；敏感控制载荷不进入事件；聚合耗时、间隔、抖动和吞吐并通过 `server_metrics` 暴露；审计 sink 故障与控制结果隔离；新增确定性无硬件性能预算 | 361 tests + 32 subtests，覆盖率 56.32%；30 个 Mypy 文件、14 golden、6/6 性能预算和 wheel smoke 全部通过 |
| 2026-08-04 | M4 | F | 加粉规则策略与架构边界收敛 | F-P-004/F-P-005 TODO → DONE；F-P-006 TODO → DROPPED | 新增版本化无硬件策略案例和显式四级阈值配置；修复最终允许轮次达标/超量被误判为最大轮次失败；校验有限数值与策略不变量，读数重试保留异常链且不做末次无效等待；文档明确当前确定性闭环和未立项四 Agent 概念方案，不预埋框架 | 366 tests + 43 subtests，覆盖率 56.42%；31 个 Mypy 文件、14 golden、6/6 性能预算和 wheel smoke 全部通过 |
| 2026-08-04 | M4 | E/F/G | LLM 与视觉运行指标收敛 | E-013/F-V-008 TODO → DONE | RoutedLLMClient 成为逻辑调用唯一指标切面，聚合结果、延迟、fallback、token 和 provider 报告成本；VisionService 聚合结果、实际帧数/推理次数、延迟、观测处理 FPS 及模型/标定版本；删除视觉 bool pipeline contract，不保留兼容转换；指标不保存 payload，并由 ai_status/server_metrics 暴露 | 369 tests + 43 subtests，覆盖率 56.82%；35 个 Mypy 文件、14 golden、7/7 性能预算和 wheel smoke 全部通过 |
| 2026-08-04 | M5 | B/G | 模块目录与依赖边界治理立项 | B-018/B-019/G-016/G-017/G-018/G-019 新增为 TODO | 接受领域模块化单体和局部 MVC/MVVM；规划硬件统一目录、Provider/Adapter/Driver 垂直切片、表现层边界、唯一 LLM 生命周期、core 拆分及无兼容迁移 | 文档评审；未执行代码变更 |
| 2026-08-04 | M5 | B/G | 硬件目录与 RealMan 垂直切片整批收敛 | B-018/B-019 TODO → DONE | 全部设备运行时、通信与产品实现迁入 `src/devices/` 并删除七个旧顶级包；RealMan 拆为 Provider/Adapter/Driver，Adapter 不再访问 `rm_*`，注册表与 Provider 定义分离；新增旧目录、具体硬件和厂商 SDK 泄漏边界测试 | 372 tests + 43 subtests，覆盖率 56.61%；39 个 Mypy 文件；Ruff、compileall、7/7 性能预算、hardware extra 与 wheel smoke 通过 |
| 2026-08-04 | M5 | E/G | LLM Registry 唯一生命周期与冗余 SDK 清理 | G-017 TODO → DONE | 组合根唯一创建 LLMRegistry 并由 ApplicationServices 持有；GUI、WebSocket、Voice 共享同一实例，附加服务停止不再关闭 Provider，应用宿主在会话结束后统一关闭；删除未引用的 RealMan vendor 绑定/DLL、打包和质量配置，SDK 只来自 `robotic-arm` 依赖；新增唯一创建点、共享实例和关闭顺序测试 | Compile、Ruff、Mypy（39 files）、Pytest（374 passed + 43 subtests，56.70%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-04 | M5 | D/C/G | GUI 与 WebSocket 表现层目录及设备边界收敛 | G-016 TODO → DONE | GUI 直切 controllers/bridges/view_models/views，WebSocket 直切 controllers/protocol/security/metrics；删除全部旧平铺模块和导入路径；ApplicationServices 删除 DeviceRuntime 字段，相机状态和设备就绪查询提升为应用服务契约；AIController 对 Qt Bridge 改为构造注入和仅类型依赖；新增旧路径与运行时泄漏边界测试 | Compile、Ruff、Mypy（39 files）、Pytest（376 passed + 43 subtests，56.81%）、LLM golden（14/14）、性能回归（7/7）、GUI/Server extra 及 Wheel smoke 全通过 |
| 2026-08-04 | M5 | G/F | core 职责拆分与历史路径清理 | G-018/G-019 TODO → DONE | 删除原 core 聚合目录并直切 bootstrap/configuration/domain/persistence/geometry/observability；抽取唯一机械臂名称规范化定义并建立稳定层单向依赖；删除未引用脚本、调试 Widget、源码内轨迹/图片、旧补偿字段和单相机环境变量；瓶子抓取迁入 vision 并只写受管调试目录；wheel 新增历史内容禁止清单 | Compile、Ruff、Mypy（39 files）、Pytest（380 passed + 43 subtests，58.87%）、LLM golden（14/14）、性能回归（7/7）、GUI/Server/Hardware extra 及 Wheel smoke 全通过 |
| 2026-08-05 | M5 | B/D/F/G | 第二轮目录职责与 Provider 边界评审 | G-020～G-027 新增为 TODO | 确认 GUI、执行工作流、Transport 语义设备、视觉天平、相机/底盘/显示 Provider、视觉/定位边界和过期目录文档等剩余问题；同时明确 data_collection、composition 和两类 localization 的合理分层，不做机械合并 | 只读源码、调用点、依赖边界与专项文档评审；本次仅更新计划文档 |
| 2026-08-05 | M5 | B/E/F/G | 视觉电子秤设备能力收敛 | G-020 TODO → DONE | 新增 BalanceReader/BalanceReading 与 balance 运行时资源；真实 Provider 组合受管相机会话和统一 LLM 视觉任务，模拟 Provider 可无硬件运行；智能加粉从 DeviceRuntime 获取双设备依赖；删除执行引擎动态导入、旧 OpenCV/HTTP 文件和 VVEAI 专用配置，不保留兼容入口 | Compile、Ruff、Mypy（42 files）、Pytest（392 passed + 43 subtests，59.08%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-05 | M5 | A/D/F/G | GUI 顶级目录与执行工作流收敛 | G-021/G-022 TODO → DONE | 通用 Qt 组件和 AI Assistant 迁入 gui/views，AI/音频控制进入 gui/controllers；转圈注液和闭环加粉迁入 execution/workflows；删除 widgets、ai_integration、actions、agents 四个顶级目录及全部旧导入，不保留转发；旧目录加入源码与 wheel 禁止清单 | Compile、Ruff、Mypy（42 files）、Pytest（392 passed + 43 subtests，59.07%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-05 | M5 | B/G | Transport 语义设备与工具垂直切片收敛 | G-023 TODO → DONE | ElectricGripper/StepperMotor 迁入 powder_dispenser；Relay/ToolChanger/Pipette Adapter 与 Driver 共置；删除 tools/adapters.py 和 transports/devices，不保留转发；Transport 只导出通信/协议能力，并增加真实 Modbus 帧特征测试与旧目录/wheel 门禁 | Compile、Ruff、Mypy（42 files）、Pytest（395 passed + 43 subtests，59.53%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-05 | M5 | B/G | 相机、移动底盘与显示 Provider 边界收敛 | G-024/G-025 TODO → DONE | 相机建立 RealSense/OpenCV 静态 Provider Registry 和共享 capability，未知配置在组合根失败；移动底盘拆为单一 TCP Provider/Adapter/Client 产品切片；显示屏动态导入替换为静态 Provider 注册；删除 camera_factory、旧底盘 Controller/Client，不增加兼容值或空底盘 Registry | Compile、Ruff、Mypy（42 files）、Pytest（401 passed + 43 subtests，60.79%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-06 | M5 | A/F/G | 视觉、外部定位与 Handler API 边界收敛 | G-026/G-027 TODO → DONE | 视觉抓取算法迁入 pipelines，离线 CLI 与工位重定位算法分离；UDP 外部定位改为可注入 Provider，simulation 不创建网络资源，Application Service 只保留读取策略；action_handlers 拆为 handler API/Registry/core handlers；README、配置和旧路径门禁同步更新，不保留转发模块 | Compile、Ruff、Mypy（47 files）、Pytest（406 passed + 43 subtests，60.91%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-06 | M5 | A/G | ExecutionManager 并发状态机与类型边界收口 | A-006 DOING → DONE | 修复 STARTING 阶段取消后 worker 回写 RUNNING 的状态回退；终态后抑制迟到生命周期事件；worker 可注入并覆盖并发 submit、启动前取消、取消/完成竞态、启动失败和租约释放；Mypy 扩展为整包检查 `src/execution` | Compile、Ruff、Mypy（61 files）、Pytest（410 passed + 43 subtests，61.00%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-06 | M5 | D/G | GUI Qt binding 许可证风险收敛 | G-028 → DONE | 删除旧 Qt binding、Qt6/SIP 依赖和全部导入，生产与测试代码直切 PySide6 原生 Signal/Slot；修复普通 Python 回调在 worker 线程注册 reveal timer 导致初始化卡片停在 100% 的问题，结果改由 GUI 线程 QObject receiver 处理；GUI/full extra、uv lock、可选依赖 smoke、README 和项目说明同步更新；新增旧 binding 和启动过渡回归门禁，不保留适配或兼容层 | Compile、Ruff、Mypy（61 files）、Pytest（412 passed + 43 subtests，61.03%）、LLM golden（14/14）、性能回归（7/7）、GUI extra 及 Wheel smoke 全通过 |
| 2026-08-06 | M6 | D | GUI 工作流画布方案修订与立项 | D-015～D-021 新增为 TODO | 修订 GUI 专项计划：废止平行 WorkflowExecutor/节点 Handler 和首版移除 Loop 的方案；确立纯编辑模型、CompositionService 唯一持久化、Validator/Preflight/Compiler、ExecutionManager 唯一执行链、受约束触控画布、三类停止、安全验收及一次切换路线 | 文档评审；本次仅更新计划文档，未执行代码变更 |
| 2026-08-06 | M6 | D/A/G | GUI 工作流模型、持久化与编译边界落地 | D-015/D-016/D-017 TODO → DONE；ADR-M-014 Proposed → Accepted | 新增纯 WorkflowDocument 与版本化序列化；CompositionService 独占 `.workflow` 原子保存、revision 冲突和崩溃草稿；Validator 区分结构错误，Compiler 输出规范 SequenceEntry 及步骤/Loop 节点映射，Preflight 无副作用检查运行占用、策略和设备就绪；组合根持有唯一 Compiler/Preflight，未新增执行器或 Handler | Compile、Ruff、Mypy（68 files）、Pytest（423 passed + 43 subtests，61.52%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-06 | M6 | D/A/G | 受约束工作流画布与现有功能等价接入 | D-018/D-019 TODO → DONE | 新增轻量 QGraphics 自绘画布、Start/End 表现节点、Loop 容器、自动布局、“+”插入、动作拖放/双击、拖动排序、多选、Undo/Redo、参数摘要、循环次数/展开、缩放和触控滚动；执行期禁用编辑；GUI 当前序列经 Compiler/Preflight 提交原 ExecutionBridge，AI 直接执行事件补建同一节点映射；MainWindow 删除全部旧树图元私有访问，任务组合、日志、设备状态和三类停止控制保持原服务边界 | Compile、Ruff、Mypy（75 files）、Pytest（428 passed + 43 subtests，61.42%）、LLM golden（14/14）、性能回归（7/7）及 Wheel smoke 全通过 |
| 2026-08-06 | M6 | D/G | GUI 视觉性能门禁与旧编辑器一次切换 | D-020/D-021 TODO → DONE | 集中画布 Palette/字体/间距/触控/状态色令牌，增加主题变化刷新、可访问说明和大场景最小更新；新增浅/深主题及三档窗口 offscreen 回归、100/500 节点性能预算和视觉资产许可证清单；删除近千行 components 聚合文件及 SequenceListWidget，动作库、控制面板、日志拆为单责组件，不保留旧路径或兼容层 | Compile、Ruff、Mypy（78 files）、Pytest（432 passed + 43 subtests，62.48%）、LLM golden（14/14）、性能回归（9/9；100 节点 9.77 ms/150 ms，500 节点 46.17 ms/750 ms）及 Wheel smoke 全通过 |
| 2026-08-06 | M6 | D/G | GUI 应用级统一主题 | D-020 能力增强 | 新增唯一 ThemeController 和 `system/light/dark` 三套模式；启动配置、系统颜色变化和“视图 → 主题”即时切换统一驱动应用 Palette/QSS；迁移 AI、设备、工作流、对话框、启动卡片和语义按钮的局部浅色覆盖，删除 MainWindow 旧全局浅色样式事实源；配置与模板增加 `GUI_THEME` 严格校验 | Compile、Ruff、Mypy（79 files）、Pytest（438 passed + 43 subtests，62.69%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过；浅色/深色真实 MainWindow offscreen 截图复核通过 |
| 2026-08-06 | M6 | D/G | GUI 画布交互复核与循环可视化增强 | D-018/D-020 能力增强 | 修复节点间“+”未接受 press 导致真实点击无效的问题，并让循环头、子动作间和循环末尾插入点执行真实插入命令；Loop 改为展开子动作、循环完成节点及双侧回路；动作库开合箭头移入左侧分隔边缘，保留记忆宽度和 220 ms 动画；画布统一为左键选择/双击编辑、Shift 多选、右键上下文菜单、Ctrl+左键平移、普通滚轮滚动及 Ctrl+滚轮缩放，并补充 Ctrl+A、Esc、Ctrl+0、Delete 和撤销/重做快捷键；执行期隐藏循环插入点 | Compile、Ruff、Mypy（80 files）、Pytest（448 passed + 43 subtests，63.01%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过；真实 Qt 点击链路和主窗口离屏布局复核通过 |
| 2026-08-06 | M6 | D/G | GUI 多选稳定性、抽屉命中区与动作分类选择 | D-018/D-020 能力增强 | 节点单击不再误触发拖动完成和 Scene 重建，Shift 多选保持图元身份、坐标、SceneRect 与滚动位置稳定；抽屉边缘默认收敛为细线，悬停时整条高度显示高亮箭头并可点击；“+”动作选择改为类型与分类内动作双栏结构，复用唯一类型标签并隐藏空分类 | Compile、Ruff、Mypy（81 files）、Pytest（449 passed + 43 subtests，63.07%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过；默认/悬停抽屉状态和分类选择器深色离屏复核通过 |
| 2026-08-06 | M6 | D/G | GUI 选择状态单一化与可拖动抽屉分隔条 | D-018/D-020 能力增强 | 删除节点自定义选择与 QGraphicsItem 默认选择/移动并行处理，普通左键只做单选，Shift+左键只做集合切换，节点排序统一走显式命令；抽屉分隔条默认实际占用 4 px、悬停实际扩为 28 px，整条支持单击开合和水平拖动实时调整宽度，并记忆拖动结果 | Compile、Ruff、Mypy（81 files）、Pytest（450 passed + 43 subtests，63.18%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过；4 px/28 px 主窗口离屏布局和拖动回归通过 |
| 2026-08-06 | M6 | D/G | GUI 节点拖动排序与参数按需展示 | D-018 能力增强 | 在单一自定义选择状态机上增加 8 px 阈值纵向拖动，松开后按落点提交一次可撤销排序并自动吸附；Shift 多选不进入拖动，单击不重建场景；删除常驻节点参数摘要面板，参数编辑统一由双击、右键或修改命令按需打开 | Compile、Ruff、Mypy（81 files）、Pytest（451 passed + 43 subtests，63.12%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过 |
| 2026-08-06 | M7 | D/G | GUI 工作台信息架构评审与立项 | D-022～D-025 新增为 TODO；ADR-M-015 → Accepted | 基于当前竖屏拥挤问题确立画布优先的 Workbench：顶部菜单、Activity Bar、独立资源 Side Bar、中央 Editor、非模态 Bottom Panel 和常驻 Status Bar；规定细线可拖动分隔、任务/动作/AI/组合拆页、设备/位姿/日志/基础控制按需展示、SVG/Qt Resource 与布局持久化；三类停止和关键故障不得因面板收起而隐藏，不改变 Application Service、DeviceRuntime 或唯一执行链 | 文档评审；本次仅更新计划文档，未执行代码变更 |
| 2026-08-06 | M7 | D/G | Workbench 壳层与常驻安全控制整批切换 | D-022/D-024 TODO → DONE | 新增独立 WorkbenchView，使用 Activity Bar、水平/垂直细线 Splitter、中央 Editor、非模态 Bottom Panel 和自有 Status Bar；设备健康与位姿拆为独立被动视图，设备/位姿/基础控制/日志改为底部按需页；五行大按钮收敛为编辑/执行两行命令，停止、快停、设备急停常驻；新增文件/编辑/视图/执行/设备菜单并删除 AnimatedSplitterDrawer、箭头按钮、固定日志高度和旧纵向堆叠壳层，不保留兼容入口 | Compile、Ruff、Mypy（83 files）、Pytest（452 passed + 43 subtests，63.17%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过；900×960 深色 Workbench 和位姿面板展开状态离屏复核通过 |
| 2026-08-06 | M7 | D/G | GUI 独立资源页整批拆分 | D-023 TODO → DONE | 将聚合动作库拆为已保存任务、分类基础动作、AI 助手和任务组合四个 Side Bar 页面，Activity Bar 可直接切换/收起；中央 Editor 删除任务组合 Tab 与第二套暂停/停止控件，只保留画布和唯一执行控制区；任务组合页提供显式任务/动作选择，继续由 CompositionService 与 TaskComposerService 独占状态，不保留旧属性或兼容路径 | Compile、Ruff、Mypy（83 files）、Pytest（452 passed + 43 subtests，63.14%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过 |
| 2026-08-06 | M7 | D/G | GUI SVG 资产与布局偏好收口 | D-025 TODO → DONE；M7 完成 | 新增 8 个项目自制 CC0 单色 SVG、Qt Resource 清单及编译资源模块，Activity/Status 入口按 Palette 和 1x/2x/3x 渲染，主要命令区删除 Emoji 装饰；新增 schema v1 WorkbenchLayoutState 与 QSettings 存储，持久化 Side/Bottom 页、可见性和尺寸，严格拒绝损坏/未知版本/已删除页面并恢复默认；视图菜单提供恢复默认布局，wheel 强制包含源 SVG、许可证、qrc 与编译资源 | Compile、Ruff、Mypy（83 files）、Pytest（460 passed + 43 subtests，63.41%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过 |
| 2026-08-06 | M7 | D/G | GUI Tooltip 与低描边视觉层级复核 | D-020/D-025 能力增强 | Activity Bar 原生大 Tooltip 替换为 350 ms 延迟的紧凑圆角主题气泡；浅色气泡使用浅色表面，深色气泡使用深色表面；全局 QSS 从重复容器描边改为背景层级，删除普通按钮、Tab、GroupBox、列表、StyledPanel、菜单栏、Activity Bar 和画布外框的装饰线，仅保留输入焦点、Splitter、节点与安全状态的必要边界 | Compile、Ruff、Mypy（83 files）、Pytest（464 passed + 43 subtests，63.47%）、LLM golden（14/14）、性能回归（9/9）及 Wheel smoke 全通过 |
| 2026-08-07 | M8 | A/D/E/G | 用户数据模型与自然语言命令重构立项 | A-014/A-015、D-026、E-014～E-016、G-029～G-033 新增为 TODO；ADR-M-016/ADR-M-017 → Accepted | 确认任务列表首屏漏刷新；确立唯一 `*.workflow.json`、WorkflowDocument v2 结构化控制流、动作库强类型/唯一 ID、按文件拆分 Skill、显式一次性迁移及 Action/Skill/Workflow/ExecutionControl typed command；不把单步设备命令机械包装成 Skill，不保留双格式或读取时迁移 | 文档评审；未执行代码和数据迁移 |
| 2026-08-07 | M8 | D/G | 任务首屏回归与读取副作用清理 | D-026 TODO → DONE；G-030/G-032 前置能力完成 | 主窗口首次渲染前刷新已保存任务；Action/Task/Skill 的普通 load/list 不再迁移或写盘，新增 `robot-library-data validate|migrate` 显式入口与机器可读报告；动作加载增加重复 ID 拒绝，并修复活动数据中的 2 个冲突 ID | Ruff（src/tests）通过；聚焦 Pytest 27 passed；真实 `data/` 只读校验为 46 actions / 17 tasks / 11 skills；相关目录 Mypy 暴露既有 MainWindow/SkillRegistry 历史注解问题，本批未扩大范围清理 |
| 2026-08-07 | M8 | E/G | Action/Skill schema v2 与目录化整批切换 | G-030/G-031 TODO → DONE；G-032/G-033 TODO → DOING | Action 切换为目录集合并强制 ID/名称/参数唯一有效，34 个点位规范化为数组；13 个 Skill 按领域拆分、确定性加载和原子 Registry 替换，合并旧 Python/用户双事实源差异后删除 `default_skills.py`；配置直切目录变量，内置 JSON/Schema 纳入 package-data；活动旧集合移动到可恢复备份，runtime 不保留旧入口 | 活动目录只读校验 46 actions / 13 skills，数量与语义指纹稳定；Ruff、相关 Mypy、Pytest 471 passed + 48 subtests、LLM golden 14/14、wheel 构建与隔离安装 smoke 全通过 |
| 2026-08-07 | M8 | D/G | WorkflowDocument v2 与唯一任务格式整批切换 | G-029/G-032/G-033 → DONE | 删除 Repository 的 `.task`/旧 `.workflow` 双 API，任务 UI/API 统一落到结构化 WorkflowDocument v2；控制流与 presentation 分离，运行状态不落盘；新增 `workflows/`、`drafts/`、Workflow JSON Schema、目录配置和 `robot-workflow-data` 显式迁移工具；17 个活动任务转换为 `*.workflow.json`，旧文件及 `.bak` 可恢复归档 | 活动数据 17 workflows / 272 顶层条目全部由新 Repository 加载；统一门禁通过：Mypy 83 files、Pytest 473 passed + 48 subtests、coverage 63.90%、LLM golden 14/14、性能 9/9、wheel smoke 通过 |

## 22. 建议的首批实施顺序

1. **E-014/E-015/E-016**：在新目录模型上实现 typed command 和高频单步语音动作，保持统一预览、确认、资源租约与执行入口。
2. **A-014/A-015**：在已有顺序/Loop 语义稳定后扩展结构化控制流 Compiler 与并行调度，先完成无硬件资源冲突、失败传播和取消测试，再进入真实设备验收。
3. **B-015**：确定下一种真实机械臂供应商/协议，在新 `devices/robots/<provider>/` 结构实现 adapter，并运行同一套核心契约测试和真实硬件验收。
4. **B-007/ER-006/ER-011**：在限速、可控环境中测量 RealMan quick/emergency stop 最大响应延迟，并记录停止后的恢复条件。
5. 在受信 RLBench 环境对 schema v2 Native episode 执行 `--trusted-native` 验收，并在真实双臂硬件上测量采样偏差分布。

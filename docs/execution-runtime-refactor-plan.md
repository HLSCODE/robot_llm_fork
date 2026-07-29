# 统一执行与设备运行时重构计划

> 文档状态：Active  
> 创建日期：2026-07-27  
> 最近更新：2026-07-29
> 当前阶段：核心运行时、入口、handler、结果协议和资源所有权已收敛，进入供应商验证与硬件验收
> 上级计划：[Robot LLM 项目重构总计划](project-refactor-master-plan.md)

## 1. 范围与决策

本专项负责收敛两类所有权：

1. `ExecutionManager` 是进程内唯一动作序列执行所有者。
2. `DeviceRuntime` 是进程内唯一硬件实例和生命周期所有者。

已接受的迁移决策：

- 直接切换到最终架构。
- 不保留 legacy/v2 双后端。
- 不保留旧模块导入转发、配置开关或行为兼容包装。
- 引用迁移完成后立即删除旧实现。
- 回滚依赖版本控制和已验证发布版本，不依赖运行时双栈。
- 设备 adapter 是最终协议适配边界，不是旧业务接口兼容层。

本轮不重写 RealMan、Modbus、串口、相机等底层协议算法，但上层不得自行创建或关闭这些设备。

## 2. 当前实现

```text
                         Desktop Application Host
                      +-------------+-------------+
                      |                           |
                Qt GUI 主线程           AuxiliaryServiceHost
                                                  |
                                      WebSocket / future HTTP
                      \-------------+-------------/
                                    |
                           ApplicationServices
                   +----------------+----------------+----------------+
                   |                |                |                |
           ExecutionService  DeviceManagement  CameraAccess    SafetyService
                   |            Service          Service             |
                   |                |                |                |
           ExecutionManager        DeviceRuntime --------------------+
            state/run/event          state/lifecycle |
                   |                |                |
              ActionEngine ---------+-------- ResourceArbiter
                                                   |
                                      Manual/Teleop/Teaching
                                    |
                    contracts / adapters / fakes
                                    |
                arm / base / serial / camera / display
```

### 2.1 已落地模块

| 模块 | 职责 | 当前状态 |
|---|---|---|
| `src/application/` | 应用用例入口和 composition | 已落地 |
| `AuxiliaryServiceHost` | 独立 asyncio 线程、附加服务状态、启动/逆序停止和超时 | 已落地 |
| `CompositionService` | 动作库、任务库、当前序列、revision 事件和线程安全持久化事务 | 已落地 |
| `ExecutionService` | submit/pause/resume/cancel/snapshot/wait | 已落地 |
| `DeviceManagementService` | initialize/status/shutdown | 已落地 |
| `ManualControlService` | 夹爪、继电器、移液枪、遥操作初始化 | 已落地 |
| `CameraAccessService` | 相机短任务与长会话的独占租约 | 已落地 |
| `TeleoperationService` | 遥操作会话和机械臂资源租约 | 已落地 |
| `RobotQueryService` | 厂商无关的机械臂状态读取 | 已落地 |
| `TrajectoryTeachingService` | 拖动示教会话及资源租约 | 已落地 |
| `SafetyService` | 编排受控取消、软件快停/急停、会话释放和逐设备结果 | 软件链路已落地，待真实硬件验收 |
| `src/execution/models.py` | 状态、事件、快照、结果、错误 | 已落地 |
| `ExecutionManager` | 唯一 worker、run_id、终态和事件 | 已落地 |
| `ActionHandlerRegistry` | 唯一 ActionType 分发、重复注册拒绝和完整性校验 | 已落地 |
| `ActionExecutionContext` | 单动作 deadline、暂停、协作取消和阻塞调用边界 | 已落地，待逐设备补强 |
| `ActionEngine` | 序列展开、状态推进、事件转换和 handler 分发 | 已不包含具体设备/领域执行实现 |
| `src/device_runtime/contracts.py` | 设备能力协议 | 已落地 |
| `DeviceRuntime` | 注册、初始化、查询、停止、重连和关闭 | 已落地 |
| `ResourceArbiter` | 非阻塞独占资源租约 | 已落地 |
| `src/device_runtime/adapters.py` | RealMan、继电器、快换手、移液枪协议适配 | 已落地 |
| `src/device_runtime/fakes.py` | 无硬件 simulation | 已落地 |

### 2.2 已注册设备

| Device ID | 能力接口 | 真实实现来源 | Simulation |
|---|---|---|---|
| `robot-system` | `RobotSystem` 核心能力及可选 teleop/trajectory/tool-rack 能力 | `ROBOT_PROVIDER=realman` adapter | 有 |
| `body-axis` | `BodyAxis` | ModbusMotor | 有 |
| `mobile-base` | `MobileBase` | RobotMoveController | 有 |
| `neck` | `NeckMotion` | PWMNeckController | 有 |
| `relay-bank` | `DigitalOutputs` | RelayController adapter | 有 |
| `tool-changer` | `ToolChanger` | Kuaihuanshou adapter | 有 |
| `pipette` | `Pipette` | ADP + 枪头 adapter | 有 |
| `powder-dispenser` | `PowderDispenser` | TappingController | 有 |
| `camera` | `CameraSource`/`DepthCameraSource` | Camera manager | 有 |
| `expression-display` | `ExpressionDisplay` | 表情屏 backend | 有 |

### 2.3 入口迁移

| 入口 | 迁移结果 | 后续工作 |
|---|---|---|
| launcher | GUI 是唯一桌面宿主，只组装一份 `ApplicationServices`；WebSocket 作为可选附加服务启动 | 增加 GUI 服务状态展示 |
| GUI 手工序列 | 通过 Qt `ExecutionBridge` 提交到统一 manager | GUI smoke test |
| GUI AI/语音序列 | 与手工序列共享 manager 和最终事件 | preview/command 状态模型 |
| WebSocket execute/task/AI | 通过 `ExecutionService` 提交，并贯通 request_id/run_id 与业务终态审计 | API version、限流和 handler 拆分 |
| WebSocket status/init/disconnect | 通过应用服务和 runtime | typed DTO、错误码 |
| GUI/WebSocket quick-stop/emergency-stop | 通过 `SafetyService`，返回逐设备结果 | RealMan 真实硬件验收、其他运动设备能力补齐 |
| 遥操作 | 持有会话级机械臂资源租约，软件停止后统一释放 | 心跳、所有者、超时自动停止 |
| 相机测试/语音视觉/视觉动作 | 使用 runtime-owned camera 和精确资源租约 | 模型、标定和视觉结果治理 |
| WebSocket 相机预览/数据采集 | 持有完整会话周期的 CameraSession；采集开始记录前先申请 teleop 租约 | DataCollectionService 和状态机 |

### 2.4 已删除内容

- `src/gui/execution.py`
- `src/robot_server/action_executor.py`
- `src/actions/gui_legacy.py`
- `src/actions/base_controller.py`
- `src/actions/adp_absorb.py`
- `src/actions/adp_dispense.py`
- AI bridge 内部 worker 和独立 simulation 执行路径
- expression display 的进程外全局默认实例入口
- camera factory 的公开 start/stop 生命周期入口和 manager 全局单例
- `RobotController` 中未使用的快换手/继电器组合流程和硬编码演示主程序

### 2.5 附加服务生命周期

- GUI 主线程只运行 Qt 事件循环；所有异步附加服务共用一个受管理的后台
  asyncio 线程。
- 附加服务实现统一的异步 `start()`/`stop()` 契约。启动失败只影响该服务，
  不阻止 GUI 和其他附加服务继续运行。
- 应用退出时先逆序停止 WebSocket/未来 HTTP 等附加服务，再关闭
  `DeviceRuntime`，避免网络请求在设备释放过程中继续进入。
- 附加服务只持有共享 `ApplicationServices`，不创建、替换或关闭硬件实例。
- 远程监听会输出安全警告；认证与客户端控制租约完成前默认只监听本机。

## 3. 状态与事件语义

### 3.1 ExecutionState

```text
IDLE
  -> STARTING
  -> RUNNING
       -> PAUSED -> RUNNING
       -> CANCELLING -> CANCELLED
       -> SUCCEEDED
       -> FAILED
```

约束：

- 同一进程同时只允许一个 active run。
- submit 返回只表示请求已接受，不表示执行成功。
- 最终结果只由 `SUCCEEDED`、`FAILED` 或 `CANCELLED` 事件确定。
- 第二个 run 或资源冲突立即显式拒绝。
- listener 异常不得终止执行 worker。
- shutdown 必须先停止遥操作、取消 active run、等待结束，再逆序关闭设备。

### 3.2 停止能力

| 能力 | 当前状态 | 语义 |
|---|---|---|
| pause/resume | 已实现 | 在引擎安全检查点暂停和恢复 |
| cancel | 已实现 | 协作式任务取消，不冒充硬件急停 |
| quick-stop | 软件链路已实现，待硬件验收 | 绕过资源租约，向声明该能力的已就绪运动设备发送快停 |
| emergency-stop | 软件链路已实现，待硬件验收 | 向声明该能力的已就绪运动设备发送软件急停；不替代物理急停回路 |
| shutdown | 已实现基础流程 | 取消、等待、释放租约、关闭设备 |

当前设备停止能力矩阵：

| 设备 | quick-stop | emergency-stop | 当前结论 |
|---|---|---|---|
| RealMan 双臂机械臂 | 已接入 `rm_set_arm_slow_stop` | 已接入 `rm_set_arm_stop` | adapter、ApplicationService、GUI、WebSocket 和 simulation 测试已完成；真实双臂仍需限速验收 |
| 身体升降轴 | 不支持 | 不支持 | 现有底层急停会吞掉异常并重新使能，未达到统一停止契约要求 |
| 移动底盘 | 不支持 | 不支持 | 当前协议未提供经过确认的停止命令，阻塞 TCP 也缺少完整超时 |
| 颈部舵机 | 不支持 | 不支持 | 当前驱动没有统一停止原语 |
| 加粉装置 | 不支持 | 不支持 | 只有升降/旋转局部停止，不能保证整个设备停止 |

结果状态统一为 `stopped`、`not_ready`、`unsupported`、`failed`。其中
`stopped` 只表示 adapter/SDK 调用成功返回，不代表已经完成真实硬件停稳确认；
`not_ready` 表示运行时没有持有可执行命令的就绪实例。
本矩阵当前覆盖连续运动设备；继电器、快换手、移液枪等离散输出的断电安全态和
停机策略由总计划 `B-017` 单独跟踪，不能直接套用“停止运动”语义。

### 3.3 动作控制策略

`ActionHandlerRegistry` 现在同时绑定 handler 和不可变
`ActionControlPolicy`。每次执行会先按参数解析实际动作路径，再校验策略要求的
设备停止模式是否已由 `DeviceRuntime` 注册；不一致时不初始化设备，直接返回
`control_policy_mismatch`。策略随 `step_started` 事件传递，并由 WebSocket
作为 `control_policy` 输出。应用组装时还会校验 MOVE 目标、BASE_MOVE 模式和
MANIPULATE 执行器的 handler 路由集合与策略集合完全一致，新增或删除一侧而
遗漏另一侧会直接导致启动失败。

取消模式：

| 模式 | 语义 | 延迟声明 |
|---|---|---|
| `bounded_cooperative` | 不进入同步硬件调用，在统一检查点响应取消 | 当前预期上限 0.1 秒 |
| `after_blocking_call` | 在同步 SDK/协议调用前后检查；调用返回前不能承诺即时取消 | 上限未知，明确不支持即时取消 |
| `device_assisted` | 除调用前后检查外，可由 SafetyService 对声明目标发送 quick/emergency stop | 软件能力已校验；最大物理响应延迟待真实硬件验收 |

当前动作策略矩阵：

| 动作路径 | 取消模式 | 涉及设备 | 设备级停止 | 当前结论 |
|---|---|---|---|---|
| WAIT、INSPECT | `bounded_cooperative` | 无 | 不需要 | simulation 预期最大响应 0.1 秒 |
| MOVE/机械臂 | `device_assisted` | robot-system | quick + emergency | 注册能力执行前校验；RealMan 时延待验收 |
| MOVE/身体 | `after_blocking_call` | body-axis | 不支持 | 阻塞调用返回前不承诺取消 |
| BASE_MOVE/position、distance | `after_blocking_call` | mobile-base | 不支持 | 阻塞调用返回前不承诺取消 |
| MANIPULATE/快换手、继电器、夹爪、吸液枪、表情屏、智能加粉、加粉装置 | `after_blocking_call` | 对应设备 | 不支持统一运动停止 | 离散输出安全态另由 B-017 跟踪 |
| MANIPULATE/右臂转圈注液 | `device_assisted` | robot-system + pipette | robot-system quick + emergency | 机械臂可辅助停止；移液枪离散输出安全态待定义 |
| CHANGE_GUN | `device_assisted` | robot-system | quick + emergency | 工具架运动可辅助停止；RealMan 时延待验收 |
| VISION_CAPTURE、VISION_RELOCALIZE | `device_assisted` | robot-system + camera | robot-system quick + emergency | 内部同步视觉流程仍需真实场景验证 |
| TRAJECTORY | `device_assisted` | robot-system | quick + emergency | 轮询可取消；在途 SDK 调用由机械臂停止辅助 |

`expected_max_cancel_latency_seconds = null` 不是遗漏，而是禁止在硬件验证前
伪造上限。真实验收必须记录“发出停止请求—运动停止/SDK 返回”的测量值和设备
最终状态。

## 4. 优先级与工作项

状态：`TODO`、`DOING`、`BLOCKED`、`DONE`、`DROPPED`。

| ID | 优先级 | 状态 | 工作项 | 验收结果 |
|---|---|---|---|---|
| ER-001 | P0 | DONE | 唯一 ExecutionManager | 并发 submit 被拒绝 |
| ER-002 | P0 | DONE | AI 最终结果语义 | 不再把 submit 成功当终态 |
| ER-003 | P0 | DONE | GUI 手工/AI 执行互斥 | 共用同一 manager |
| ER-004 | P0 | DONE | stop/disconnect 顺序 | worker 结束前不关设备 |
| ER-005 | P0 | DONE | teleop/sequence 资源互斥 | 会话租约冲突测试通过 |
| ER-006 | P0 | DOING | quick-stop/emergency-stop 能力矩阵 | 每类设备完成硬件验证 |
| ER-007 | P0 | DONE | WebSocket 认证和控制租约 | 未授权客户端不能写硬件 |
| ER-008 | P1 | DONE | DeviceRuntime 和 capability | 状态和生命周期唯一 |
| ER-009 | P1 | DONE | 统一 simulation runtime | 与真实模式共用状态机 |
| ER-010 | P1 | DONE | ActionHandlerRegistry | 全部具体动作 handler 已迁出 ActionEngine，并统一返回结构化 ActionHandlerResult |
| ER-011 | P1 | DOING | 阻塞动作可取消和超时 | 全动作控制策略、执行前能力校验和事件输出已落地；待 RealMan 最大停止延迟验收 |
| ER-012 | P1 | DONE | camera session/resource | 预览、测试、语音、视觉动作和数据采集通过显式租约互斥，非相机序列不被阻塞 |
| ER-013 | P1 | DONE | 机械臂能力接口补强 | GUI、执行、视觉、示教和数据采集不依赖 RM 原生字段 |
| ER-014 | P1 | DOING | WebSocket contract tests | 请求/执行关联、同步快速终态、权限和错误信封已覆盖；待全 action schema 固化 |
| ER-015 | P1 | TODO | GUI simulation smoke tests | 按钮和步骤状态正确 |
| ER-016 | P1 | TODO | 真实设备验收 | 所有设备逐项记录结果 |
| ER-017 | P2 | TODO | 拆分 WebSocket handler | 协议和应用用例分离 |
| ER-018 | P2 | TODO | 拆分 MainWindow service/view-model | UI 不含设备细节 |
| ER-019 | P2 | TODO | typed action schema | GUI/WS/Skill 共用校验 |
| ER-020 | P3 | DONE | 删除旧执行器和开关 | 不存在 legacy backend |

## 5. 下一阶段实施顺序

### Phase A：安全停止和硬件能力

1. 已定义运动设备的 quick-stop/emergency-stop 能力声明和显式能力矩阵。
2. 已区分 `not_ready`、`unsupported`、`stopped`、`failed`；不把 SDK 成功返回表述为设备停稳确认。
3. 已建立覆盖全部 ActionType 和参数分支的动作控制策略；deadline 不会通过
   脱离资源租约的后台线程伪中断 SDK 调用。纯协作路径声明 0.1 秒预期上限，
   其他路径明确标记为“调用返回后取消”或“设备停止辅助”。
4. 待在真实设备上验证 cancel、quick-stop、emergency-stop。
5. handler 失败已关联稳定 code、operation、device_id，并通过带 `run_id`
   的执行事件和快照向入口传播；待底层设备错误进一步细分。

完成标准：

- 任何长耗时动作都有超时。
- cancel 延迟有可测上限或明确“不支持即时取消”。
- UI/API 不把任务取消显示为硬件急停。
- shutdown 超时不会静默继续关闭运动设备。

### Phase B：ActionHandlerRegistry

1. 已定义 typed `ActionHandler` 协议、`ActionHandlerRegistry`、统一执行上下文和
   `ActionHandlerResult`；注册表拒绝旧 `bool` 返回。
2. 已拆 WAIT、INSPECT、表达屏、换枪、轨迹和视觉动作。
3. 机械臂、身体和底盘已拆为组合式 motion handlers；末端执行器已拆为
   二级执行器注册表。
4. 智能加粉和转圈注液已迁出；轨迹轮询间隔已配置化，视觉 executor
   通过组合注入并统一经过 deadline/cancel 调用边界。
5. ActionType 缺少 handler 时在 submit 前显式失败。

完成标准：

- 新动作只注册一次。
- ActionEngine 不再包含巨型 `_execute_*` 映射。
- handler 不依赖 Qt、WebSocket 或具体入口。
- handler 单元测试使用 fake capability。

### Phase C：设备能力继续收敛

1. 已将机械臂运动、状态、夹爪、遥操作、轨迹示教和工具架提升为项目级接口。
2. 已移除业务代码对 `robot1_ctrl/robot2_ctrl` 和 RM SDK 对象的了解。
3. 相机预览、语音视觉、测试和数据采集已建立 session/lease；序列按动作
   控制策略申请精确设备集合。
4. Provider 注册表、RealMan 强类型配置和共享核心契约测试已完成；下一步在明确
   目标厂商/协议后接入第二种真实 adapter，验证核心能力与可选能力拆分。
5. 统一设备 health、错误码、重试与重连策略。
6. 清理底层驱动文件中的业务 GUI 和过期演示入口。

完成标准：

- presentation/transport/execution 层不导入具体硬件模块。
- 更换同功能设备只需新增/替换 adapter 和 factory 注册。
- 设备实例只由 DeviceRuntime 创建和关闭。
- 数据采集、teleop、sequence、camera test 都服从资源仲裁。

### Phase D：协议和界面治理

1. WebSocket 已增加认证、控制所有者、`request_id` 和稳定错误码。
2. `execution_finished` 已明确返回 `run_id/state/success/error/failure`，
   并通过同一 request/run 完成两阶段审计。
3. 拆分 execution/device/camera/teleop/data-collection handler。
4. GUI 提取 execution/device view-model。
5. 建立 WebSocket contract test 和 GUI simulation smoke test。

## 6. 自动化验证

当前命令：

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
git diff --check
```

现有测试覆盖：

- DeviceRuntime 唯一实例、关闭和重新初始化。
- ResourceArbiter 冲突和释放。
- 动作策略到序列资源集合的精确映射。
- 相机预览、测试、语音和数据采集会话的异常释放。
- 数据记录启动前取得 teleop 机械臂租约。
- ExecutionManager 单 run、暂停、恢复、取消和终态。
- worker 结束后资源释放。
- simulation ActionEngine 执行。
- teleop session 阻止需要机械臂的 sequence。
- quick-stop 取消 active execution、释放 teleop lease 并调用 ready robot。
- DeviceRuntime 对停止成功、未就绪、不支持和失败逐项报告，单设备失败不阻断其他设备。
- 全动作控制策略矩阵、矛盾声明拒绝和设备注册能力执行前校验。
- `step_started` 执行事件及 WebSocket 协议输出当前动作控制策略。
- RealMan adapter 停止双臂且不等待被阻塞运动持有的普通 SDK 锁。
- presentation/transport/execution 层不得导入具体硬件。
- CompositionService 并发动作写入、任务编辑、防御性快照和跨入口事件广播。

下一批测试：

- pause 与 cancel 并发。
- shutdown timeout。
- listener 异常隔离。
- LoopBlock 展开和非法 repeat。
- 全 ActionType simulation。
- WebSocket accepted/step/terminal 事件顺序。
- GUI signal 的主线程投递。
- 多客户端相机 subscribe/unsubscribe 与断线竞态。

## 7. 真实硬件验收

必须在 simulation 通过后按顺序执行：

1. 双臂初始化、状态读取和关闭。
2. Robot1/Robot2 move_j、move_l、夹爪和轨迹。
3. 身体升降、底盘定位和距离移动。
4. 继电器、快换手、移液枪和表情屏。
5. 加粉装置手动动作和智能闭环。
6. 相机预览、语音视觉、抓取和重定位。
7. teleop 独占、断线释放和 sequence 冲突。
8. 普通取消、quick-stop 和 emergency-stop。
9. 执行中关闭应用和设备故障恢复。

每项记录：

- 配置和设备/固件版本。
- run_id、执行入口和动作参数。
- 状态转换、最终结果和原始错误。
- 停止延迟与设备最终状态。

RealMan 停止专项验收：

1. 在空载、限速、无人员进入运动范围且物理急停可立即触达的条件下进行。
2. 测试前由项目安全负责人给出 quick-stop 和 emergency-stop 各自允许的
   最大响应时间；代码在完成测量前保持延迟字段为 `null`，不得自行假定阈值。
3. 分别对左臂、右臂和双臂并行运动执行 MOVE 与 TRAJECTORY；CHANGE_GUN、
   VISION_CAPTURE、VISION_RELOCALIZE 仅在其真实流程会驱动机械臂时执行。
4. 每条适用路径分别触发 quick-stop 和 emergency-stop，至少重复三次，
   同时记录请求发出、SDK 返回、确认停止运动三个时间点。
5. 验证 worker 最终状态、资源租约释放、设备告警/使能状态，以及再次执行前
   必须完成的恢复操作；任何单臂未停止、超时、状态未知都判定失败。
6. 只有全部适用路径满足预先批准的阈值，才能将 ER-006、ER-011 和 B-007
   标记为 DONE，并把测得的最坏值写回动作策略或对应 provider 配置。

记录模板：

| 日期 | provider/固件 | 动作路径 | 臂 | 停止模式 | 请求→SDK 返回 | 请求→确认停稳 | worker 终态 | 恢复步骤 | 结果 |
|---|---|---|---|---|---:|---:|---|---|---|
| 待填写 | RealMan / 待填写 | MOVE/机械臂 | 左/右/双臂 | quick/emergency | - | - | - | - | 待验收 |

## 8. 风险

| 风险 | 优先级 | 处理 |
|---|---|---|
| 阻塞 SDK 延迟响应 cancel | P0 | timeout、quick-stop、硬件验收 |
| WebSocket 远程暴露缺少 Origin/TLS 部署验收 | P1 | 保持默认仅监听本机；远程部署通过可信反向代理提供 wss 并校验 Origin |
| 视觉流程内部仍包含长同步调用 | P1 | 标记不可即时取消区段，并通过设备停止能力和硬件测试验证最大延迟 |
| 底层设备错误尚未统一映射到细分错误码 | P1 | 在 adapter 边界建立厂商错误映射并保留原始诊断上下文 |
| GUI/WebSocket 大类仍承担过多状态 | P2 | 提取 handler、service 和 view-model |
| simulation 与真实设备差异 | P1 | 同状态机 + contract + 硬件清单 |

## 9. 架构决策记录

| ADR | 状态 | 决策 |
|---|---|---|
| ADR-ER-001 | Accepted | 使用进程级单一 `ExecutionManager` |
| ADR-ER-002 | Accepted | 使用 `DeviceRuntime` 独占设备生命周期 |
| ADR-ER-003 | Accepted | 使用立即拒绝的显式资源租约 |
| ADR-ER-004 | Accepted | teleop 不进入普通序列，但持有会话级资源租约 |
| ADR-ER-005 | Accepted | simulation 替换设备实现，不替换状态机 |
| ADR-ER-006 | Accepted | 直接切换，不保留 legacy/v2 后端或兼容开关 |

## 10. 实施记录

| 日期 | 阶段 | 状态变化 | 说明 |
|---|---|---|---|
| 2026-07-27 | 计划 | TODO → DONE | 创建专项计划 |
| 2026-07-27 | 核心 Runtime | TODO → DOING | 落地 ApplicationServices、ExecutionManager、DeviceRuntime、ResourceArbiter 和 fakes |
| 2026-07-27 | 入口迁移 | TODO → DOING | GUI、AI、WebSocket、voice 和 teleop 直接切换 |
| 2026-07-27 | legacy 清理 | TODO → DONE | 删除两套旧执行器及无引用旧硬件入口 |
| 2026-07-27 | 机械臂能力收敛 | DOING → DONE | RealMan 进入 adapter 边界；GUI、视觉、示教、轨迹和数据采集切换到厂商无关接口 |
| 2026-07-28 | 安全停止软件链路 | TODO → DOING | 新增统一停止契约、设备能力矩阵和 SafetyService；RealMan 双臂快停/急停接入 GUI 与 WebSocket，真实硬件验收仍待完成 |
| 2026-07-28 | 应用与附加服务宿主 | TODO → DONE | GUI/WebSocket 直接切换为单进程共享 ApplicationServices；WebSocket 具备异步 start/stop，网络服务先于设备运行时关闭 |
| 2026-07-28 | 编排状态服务 | TODO → DONE | 动作库、任务库和当前序列进入线程安全 CompositionService；GUI/WS 删除 StorageManager 直连并通过 revision 事件同步 |
| 2026-07-29 | handler 与动作控制首批收敛 | ER-010/ER-011 保持 DOING | 唯一 ActionHandlerRegistry、注册完整性校验和动作 deadline/cancel 上下文落地；WAIT/INSPECT 拆出，阻塞调用超时后仍等待真实返回并保留资源租约 |
| 2026-07-29 | motion handlers 收敛 | ER-010/ER-011 保持 DOING | 机械臂、身体、底盘从 ActionEngine 拆出；设备 I/O 接入统一 invoke/checkpoint，重试和轮询配置化，取消/超时不再被设备异常边界吞掉 |
| 2026-07-29 | manipulation handlers 收敛 | ER-010/ER-011 保持 DOING | MANIPULATE 使用二级执行器注册表；末端执行器、智能加粉和转圈注液移出引擎；移液 typed command 在设备初始化前校验，智能加粉等待可取消且 finally 安全回位 |
| 2026-07-29 | domain handlers 收敛 | ER-010/ER-011 保持 DOING | 换枪、轨迹、视觉抓取和视觉重定位移出 ActionEngine；轨迹轮询配置化，参数校验前置，视觉流程改为可注入 executor，四类调用统一透传取消与超时 |
| 2026-07-29 | 结构化 handler result | ER-010 DOING → DONE | 全部 handler 直接切换为 ActionHandlerResult，不保留 bool 兼容；失败 code、message、operation、device_id 贯通 EngineResult、ExecutionSnapshot 和执行事件 |
| 2026-07-29 | 动作控制策略矩阵 | ER-011 保持 DOING | 注册表直接绑定 handler 与控制策略；全部动作分支声明取消模式、设备和停止目标，执行前拒绝能力矛盾，并通过运行时事件/WebSocket 输出；RealMan 最大停止延迟仍待硬件验收 |
| 2026-07-29 | 精确资源租约与相机会话 | ER-012 DOING → DONE | ExecutionManager 按动作策略申请实际设备集合；设备初始化/关闭纳入仲裁；相机测试、语音、WebSocket 预览和数据采集直接切换到 CameraAccessService/CameraSession，不保留 manager_factory 兼容入口 |
| 2026-07-29 | 机械臂 Provider 配置收敛 | B-015 TODO → DOING；B-016 TODO → DONE | Provider 注册表和共享核心契约测试落地；RealMan 型号、连接、运动/夹爪及工具架参数强类型化，删除 controller 换枪硬编码、旧配置模块和旧环境键 |
| 2026-07-29 | WebSocket 安全与执行关联 | ER-007 TODO → DONE；ER-014 TODO → DOING | 写操作认证和单控制租约落地；直接响应、执行事件及业务终态贯通 request_id/action/run_id，错误信封稳定且内部异常不泄露，补充同步快速终态竞态测试 |

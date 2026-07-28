# 统一执行与设备运行时重构计划

> 文档状态：Active  
> 创建日期：2026-07-27  
> 最近更新：2026-07-28
> 当前阶段：核心运行时和主要入口已直接切换，进入安全能力、handler 拆分与硬件验收  
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
           ExecutionService  DeviceManagement  Manual/Teleop   SafetyService
                   |            Service           Service            |
                   |                |                |                |
           ExecutionManager        DeviceRuntime --------------------+
            state/run/event          state/lifecycle |
                   |                |                |
              ActionEngine ---------+-------- ResourceArbiter
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
| `TeleoperationService` | 遥操作会话和机械臂资源租约 | 已落地 |
| `RobotQueryService` | 厂商无关的机械臂状态读取 | 已落地 |
| `TrajectoryTeachingService` | 拖动示教会话及资源租约 | 已落地 |
| `SafetyService` | 编排受控取消、软件快停/急停、会话释放和逐设备结果 | 软件链路已落地，待真实硬件验收 |
| `src/execution/models.py` | 状态、事件、快照、结果、错误 | 已落地 |
| `ExecutionManager` | 唯一 worker、run_id、终态和事件 | 已落地 |
| `ActionEngine` | 当前唯一 ActionType 执行分发 | 已落地，待拆 handler |
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
| WebSocket execute/task/AI | 通过 `ExecutionService` 提交 | request_id/run_id 协议补强 |
| WebSocket status/init/disconnect | 通过应用服务和 runtime | typed DTO、错误码 |
| GUI/WebSocket quick-stop/emergency-stop | 通过 `SafetyService`，返回逐设备结果 | RealMan 真实硬件验收、其他运动设备能力补齐 |
| 遥操作 | 持有会话级机械臂资源租约，软件停止后统一释放 | 心跳、所有者、超时自动停止 |
| 相机测试/语音视觉/视觉动作 | 使用 runtime-owned camera | 预览 session 和细粒度租约 |

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
| ER-007 | P0 | TODO | WebSocket 认证和控制租约 | 未授权客户端不能写硬件 |
| ER-008 | P1 | DONE | DeviceRuntime 和 capability | 状态和生命周期唯一 |
| ER-009 | P1 | DONE | 统一 simulation runtime | 与真实模式共用状态机 |
| ER-010 | P1 | DOING | ActionHandlerRegistry | 拆除 ActionEngine 巨型分发 |
| ER-011 | P1 | DOING | 阻塞动作可取消和超时 | 每个长动作声明停止能力 |
| ER-012 | P1 | DOING | camera session/resource | 预览和视觉任务不争用 |
| ER-013 | P1 | DONE | 机械臂能力接口补强 | GUI、执行、视觉、示教和数据采集不依赖 RM 原生字段 |
| ER-014 | P1 | TODO | WebSocket contract tests | 事件顺序、终态和错误稳定 |
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
3. 待为每个阻塞 ActionEngine 路径增加超时和 cooperative cancel。
4. 待在真实设备上验证 cancel、quick-stop、emergency-stop。
5. 待将设备错误进一步关联到 operation 和 `run_id`；当前报告已包含 `device_id`。

完成标准：

- 任何长耗时动作都有超时。
- cancel 延迟有可测上限或明确“不支持即时取消”。
- UI/API 不把任务取消显示为硬件急停。
- shutdown 超时不会静默继续关闭运动设备。

### Phase B：ActionHandlerRegistry

1. 定义 typed `ActionHandler` 协议和 handler result。
2. 先拆 WAIT、INSPECT、表达屏等低耦合动作。
3. 再拆机械臂、身体、底盘、末端执行器。
4. 最后拆视觉、轨迹、智能加粉等领域 flow。
5. ActionType 缺少 handler 时在 submit 前显式失败。

完成标准：

- 新动作只注册一次。
- ActionEngine 不再包含巨型 `_execute_*` 映射。
- handler 不依赖 Qt、WebSocket 或具体入口。
- handler 单元测试使用 fake capability。

### Phase C：设备能力继续收敛

1. 已将机械臂运动、状态、夹爪、遥操作、轨迹示教和工具架提升为项目级接口。
2. 已移除业务代码对 `robot1_ctrl/robot2_ctrl` 和 RM SDK 对象的了解。
3. 下一步接入第二种供应商 adapter，验证核心能力与可选能力拆分。
4. 为相机预览、语音视觉和测试建立 session/lease。
4. 统一设备 health、错误码、重试与重连策略。
5. 清理底层驱动文件中的业务 GUI 和过期演示入口。

完成标准：

- presentation/transport/execution 层不导入具体硬件模块。
- 更换同功能设备只需新增/替换 adapter 和 factory 注册。
- 设备实例只由 DeviceRuntime 创建和关闭。
- 数据采集、teleop、sequence、camera test 都服从资源仲裁。

### Phase D：协议和界面治理

1. WebSocket 增加认证、控制所有者、`request_id` 和稳定错误码。
2. `execution_finished` 明确返回 `run_id/state/success/error`。
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
- ExecutionManager 单 run、暂停、恢复、取消和终态。
- worker 结束后资源释放。
- simulation ActionEngine 执行。
- teleop session 阻止 sequence。
- quick-stop 取消 active execution、释放 teleop lease 并调用 ready robot。
- DeviceRuntime 对停止成功、未就绪、不支持和失败逐项报告，单设备失败不阻断其他设备。
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
- camera/teleop/data collection 资源冲突。

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

## 8. 风险

| 风险 | 优先级 | 处理 |
|---|---|---|
| 阻塞 SDK 延迟响应 cancel | P0 | timeout、quick-stop、硬件验收 |
| WebSocket 无认证和控制所有者 | P0 | 在开放网络部署前完成 |
| 视觉仍依赖 RM 原生对象 | P1 | 提升 RobotSystem 视觉/示教能力 |
| 相机预览尚无持久 session lease | P1 | CameraSession + ResourceArbiter |
| ActionEngine 仍为大类 | P1 | 分批迁移 handler registry |
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

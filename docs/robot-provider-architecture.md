# 机械臂供应商适配架构

> 状态：Active  
> 最近更新：2026-08-04

## 1. 目标

业务代码只表达“控制哪条机械臂、执行什么能力”，不依赖 RealMan 的类名、方法名、错误码或连接对象。替换机械臂供应商时，只新增 provider adapter 和配置，不修改 GUI、执行引擎、视觉、数据采集或 WebSocket 用例。

本次采用直接切换策略：旧的 `move_robot1`、`robot1_ctrl`、`rm_*` 等业务入口已移除，不提供兼容包装或双后端开关。

## 2. 分层

```text
GUI / WebSocket / Vision / Data Collection
                    |
           Application / Execution
                    |
          devices contracts + models
                    |
     provider registry + typed settings
                    |
       provider / adapter (RealMan)
                    |
         driver / installed vendor SDK
```

依赖规则：

- 业务层只能依赖 `src.devices` 导出的模型和 Protocol。
- `rm_*`、`robot1_ctrl`、`robot2_ctrl` 只允许出现在
  `src/devices/robots/realman/driver.py` 中；AST 边界测试禁止它们
  重新进入 Adapter 或业务层。
- adapter 把厂商返回码统一转换为异常，把厂商状态统一转换为 `ArmState`。
- `devices/robots/provider.py` 只定义厂商无关的 Provider 类型；
  `devices/robots/registry.py` 是名称、真实 capability 和创建函数的唯一注册点。
- 型号、连接和厂商工作流配置先转换为强类型 settings，再创建厂商 driver。
- 位姿单位固定为米和弧度，关节角固定为度，并体现在类型字段名中。
- 机械臂实例只由 `DeviceRuntime` 创建、缓存和关闭。

### 2.1 角色定义

| 角色 | 职责 | 禁止承担 |
|---|---|---|
| Application Service | 用例、资源、安全和生命周期编排 | 厂商协议与 SDK 调用 |
| Provider | 解析产品配置、声明能力、装配 Adapter/Driver | 业务动作流程 |
| Adapter | 实现项目 capability，转换参数、状态和错误 | UI、网络协议和全局对象创建 |
| Driver | 封装厂商连接和原生命令 | 应用权限、资源仲裁和业务状态 |
| Transport | 串口/TCP 等通信和收发策略 | 设备业务语义 |

不建立混放上述角色的 `robot_services` 或通用 `services` 目录。不同机械臂产品按
垂直切片组织，产品内部再分 provider/adapter/driver。

### 2.2 当前目录

```text
src/devices/robots/
├── provider.py              # Provider 类型与核心能力约束
├── registry.py              # 唯一注册和查找入口
├── realman/
│   ├── provider.py
│   ├── adapter.py
│   └── driver.py
└── <next-provider>/
    ├── provider.py
    ├── adapter.py
    └── driver.py
```

该目录已完成直接切换：组合根、测试、打包和全部导入已更新，
`device_runtime/`、`arm_sdk/` 等旧位置已经删除，不提供转发模块或新旧双栈。
共享机械臂模型与 Protocol 位于 `src/devices/runtime/`。
RealMan SDK、ctypes 绑定和平台原生库统一由 `robotic-arm` 可选依赖提供，项目不再
保存或打包第二份厂商代码。

## 3. 能力模型

核心能力由每个 provider 必须实现：

- `ArmMotion`：按关节插值或直线插值移动到笛卡尔位姿。
- `ArmStateReader`：读取标准化位姿、关节角和设备错误状态。
- `GripperControl`：打开、关闭或移动夹爪。
- `RobotSystem`：以上核心能力和设备关闭能力的组合。

可选能力按具体设备声明：

- `ArmTelemetryReader`：提供带 UTC/monotonic 时间的实际关节、夹爪及可选
  速度、电流和末端 wrench，数据采集不读取厂商 SDK 字典
- `RobotTeleoperation`
- `TrajectoryControl`
- `ToolRackControl`
- `StoppableDevice`：只声明真实实现的 `quick`/`emergency` 模式

上层用例按所需 Protocol 请求能力。新增供应商不支持某项可选能力时，不应返回伪成功，也不应实现空操作；运行时应明确报告能力缺失。

## 4. 当前 RealMan 实现

`RealManRobotAdapter` 负责：

- 左/右机械臂与 RealMan 双控制器的映射。
- `MotionOptions` 到 RealMan 运动参数的转换。
- 夹爪实际位置/力、关节电流和末端六维力的单位归一化，以及基于相邻实际
  关节样本和 monotonic 时间推导关节速度。
- SDK 调用锁和连接状态检查。
- 状态、夹爪、遥操作、拖动示教、轨迹下发和工具架工作流。
- SDK 返回码到 `RobotOperationError` 的转换。

`RealManProviderSettings` 在连接硬件前校验：

- `ROBOT_MODEL` 和左右臂 IP、端口、初始位姿。
- 默认运动参数和夹爪参数。
- `ROBOT_TOOL_RACK_ARM`。
- 每个工具槽的 approach、attach、detach 位姿及 attach/detach 停留时间。

工具架的三段运动和枪头弹出错误转换由 `RealManRobotAdapter` 负责；
`RobotController` 只保留连接、SDK 生命周期和轨迹底层操作。通过
`ROBOT_PROVIDER=realman` 选择当前实现。未知 Provider 在
`DeviceRuntime` 组装阶段显式失败，不会回退到 RealMan。

本次直接移除了 `GUN1_*`、`GUN2_*`、`MOVE_SPEED` 配置和旧的
`arm_sdk/config.py`，不提供别名、转发或兼容读取。实际部署的
`config.env` 如自定义过工具架点位，必须迁移到新的
`ROBOT_TOOL_RACK_*` 键。

## 5. 新增供应商步骤

1. 实现 `RobotSystem` 的核心 Protocol；只实现硬件真实支持的可选 Protocol。
2. 在 provider adapter 内完成单位转换、错误码映射、并发锁和生命周期管理。
3. 在 `devices.robots.registry` 注册 Provider 名称、创建函数和实际
   capability 集合；`devices.runtime.factory` 不增加供应商分支。
4. 复用核心契约测试，至少覆盖双臂运动、状态读取、夹爪、关闭和结构化接口；
   再为厂商 adapter 增加参数、错误转换、停止和并发专项测试。
5. 使用 simulation 回归业务流程，再进行真实硬件限速验收。
6. 将型号相关的工具点位、关节数量、速度范围和工作空间限制放入 provider 配置。

接入过程中不得在业务层增加供应商判断，也不得为旧调用方恢复厂商原生对象。

## 6. 当前限制与下一步

- 当前只实现 RealMan Provider；Provider 注册表和 RealMan/simulation 共享核心
  契约测试已落地，但“可替换性”仍需第二种真实机械臂和硬件验证。
- RealMan driver 仍是较大的控制器类，后续应按连接、运动、轨迹和工具工作流拆分。
- 工具架已按 RealMan Provider 配置臂、槽位位姿和停留时间；当前配置 schema
  仍固定为两个槽位，扩展为动态槽位应在出现真实需求时单独设计。
- 已形成厂商无关的 quick-stop/emergency-stop 契约和逐设备结果；RealMan
  adapter 已映射到双臂 SDK 停止命令，但真实硬件停机延迟、最终状态和恢复流程尚待验收。
- 软件 emergency-stop 不能替代独立物理急停回路；当前 `stopped` 仅表示 SDK
  调用成功返回，不表示控制器或机械臂已经完成物理停稳确认。
- 真实设备回归尚需覆盖左右臂运动、夹爪、轨迹示教、轨迹下发、视觉抓取和关闭流程。

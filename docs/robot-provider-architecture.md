# 机械臂供应商适配架构

> 状态：Active  
> 最近更新：2026-07-27

## 1. 目标

业务代码只表达“控制哪条机械臂、执行什么能力”，不依赖 RealMan 的类名、方法名、错误码或连接对象。替换机械臂供应商时，只新增 provider adapter 和配置，不修改 GUI、执行引擎、视觉、数据采集或 WebSocket 用例。

本次采用直接切换策略：旧的 `move_robot1`、`robot1_ctrl`、`rm_*` 等业务入口已移除，不提供兼容包装或双后端开关。

## 2. 分层

```text
GUI / WebSocket / Vision / Data Collection
                    |
           Application / Execution
                    |
      device_runtime contracts + models
                    |
          provider adapter (RealMan)
                    |
          vendor driver / vendor SDK
```

依赖规则：

- 业务层只能依赖 `src.device_runtime` 导出的模型和 Protocol。
- `rm_*`、`robot1_ctrl`、`robot2_ctrl` 只允许出现在 `src/arm_sdk/` 和 RealMan adapter 中。
- adapter 把厂商返回码统一转换为异常，把厂商状态统一转换为 `ArmState`。
- 位姿单位固定为米和弧度，关节角固定为度，并体现在类型字段名中。
- 机械臂实例只由 `DeviceRuntime` 创建、缓存和关闭。

## 3. 能力模型

核心能力由每个 provider 必须实现：

- `ArmMotion`：按关节插值或直线插值移动到笛卡尔位姿。
- `ArmStateReader`：读取标准化位姿、关节角和设备错误状态。
- `GripperControl`：打开、关闭或移动夹爪。
- `RobotSystem`：以上核心能力和设备关闭能力的组合。

可选能力按具体设备声明：

- `RobotTeleoperation`
- `TrajectoryControl`
- `ToolRackControl`

上层用例按所需 Protocol 请求能力。新增供应商不支持某项可选能力时，不应返回伪成功，也不应实现空操作；运行时应明确报告能力缺失。

## 4. 当前 RealMan 实现

`RealManRobotAdapter` 负责：

- 左/右机械臂与 RealMan 双控制器的映射。
- `MotionOptions` 到 RealMan 运动参数的转换。
- SDK 调用锁和连接状态检查。
- 状态、夹爪、遥操作、拖动示教、轨迹下发和工具架动作适配。
- SDK 返回码到 `RobotOperationError` 的转换。

通过 `ROBOT_PROVIDER=realman` 选择当前实现。未知 provider 会在设备初始化时显式失败，不会回退到 RealMan。

## 5. 新增供应商步骤

1. 实现 `RobotSystem` 的核心 Protocol；只实现硬件真实支持的可选 Protocol。
2. 在 provider adapter 内完成单位转换、错误码映射、并发锁和生命周期管理。
3. 在 `device_runtime.factory` 注册 provider 名称、factory 和实际 capability 集合。
4. 为 adapter 运行统一契约测试，至少覆盖运动参数、状态转换、错误转换、关闭和并发访问。
5. 使用 simulation 回归业务流程，再进行真实硬件限速验收。
6. 将型号相关的工具点位、关节数量、速度范围和工作空间限制放入 provider 配置。

接入过程中不得在业务层增加供应商判断，也不得为旧调用方恢复厂商原生对象。

## 6. 当前限制与下一步

- 当前只实现 RealMan provider；架构已可扩展，但“可替换性”仍需第二种真实机械臂验证。
- RealMan driver 仍是较大的控制器类，后续应按连接、运动、轨迹和工具工作流拆分。
- 工具架动作当前绑定右臂和既有点位，需按机械臂型号配置化。
- quick-stop、emergency-stop、超时和恢复策略尚未形成跨供应商统一能力。
- 真实设备回归尚需覆盖左右臂运动、夹爪、轨迹示教、轨迹下发、视觉抓取和关闭流程。

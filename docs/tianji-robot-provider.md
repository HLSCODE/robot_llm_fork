# 天机机械臂 Provider

天机双臂通过 `src.devices.robots.tianji` 接入统一设备运行时。应用层、执行器与
GUI 只依赖项目的 `ArmMotion`、`ArmStateReader` 等能力接口，厂商 SDK 仅允许在
Tianji 驱动边界内导入。

## SDK 与平台

当前固定使用 `tj-robot-proj 0.2.0` 的公开入口 `RobotClient`，不再依赖
`TJArmsApp` 或 SDK 内部的 `native/session`、`SDK_PYTHON` 模块。

项目内置以下平台 wheel：

```text
third_party/wheels/windows-x86_64/
  tj_robot_proj-0.2.0-py3-none-win_amd64.whl
third_party/wheels/linux-x86_64/
  tj_robot_proj-0.2.0-py3-none-linux_x86_64.whl
```

在 Windows AMD64 或 Linux x86_64 上执行：

```shell
uv sync --extra hardware
```

仓库根目录的 `tj_robot_proj/` 仍是 SDK 源码与构建产物的临时参考目录，不参与
运行时导入；正式运行使用 `third_party/wheels` 中的平台 wheel。

## 配置

在 `config/fragments/robot.example.toml` 的 `[robot]` 中选择 Provider。完整字段和
注释见 `config/fragments/robots/tianji.example.toml`：

```toml
[robot]
provider = "tianji"
move_velocity = 10
move_radius = 0
move_connect = 0
move_block = 1

[robot_providers.tianji]
kind = "tianji"
model = "tianji-dual"
controller_ip = "192.168.1.190"
subscription_interval_seconds = 0.01
```

还必须核对以下现场标定参数：

- `left_base_transform`、`right_base_transform`：两臂基座相对
  世界坐标系的 4x4 齐次变换；
- `left_tool_transform`、`right_tool_transform`：工具相对末端
  的 4x4 齐次变换；
- `joint_limits_rad`：七个关节的弧度限位。

示例值来自 SDK 0.2 文档，仅作为默认机型起点，不能替代真实设备标定。

## 坐标、单位与臂映射

- 项目 `left/right` 映射到 SDK `Arm.LEFT/Arm.RIGHT`；驱动内部稳定键为 `A/B`；
- 笛卡尔位姿均为 `[x, y, z, rx, ry, rz]`，平移单位米，XYZ 欧拉角单位弧度；
- 关节状态由统一接口继续使用角度，驱动在 SDK 边界读取 `pos_deg`；
- SDK 0.2 的笛卡尔目标是世界坐标系目标，因此基座和工具变换必须正确。

## 当前能力

已接入：

- 双臂连接、初始化、状态订阅与幂等关闭；
- 双臂 TCP 位姿和七关节角读取；
- `move_l` 直线笛卡尔目标运动；
- SDK 异常向统一 `RobotOperationError` 的错误码与诊断信息转换。

明确不声明：

- `move_j` 笛卡尔目标运动：SDK 的 `movej` 只接受关节角，没有公开笛卡尔目标
  逆解接口；
- 快速停止、软件急停：SDK 0.2 的 `RobotClient` 没有公开停止方法；
- 夹爪、遥操作、拖拽示教、轨迹保存与复现、工具架换装。

这些能力不会通过访问 `_session` 等私有属性或空实现伪装支持。若 SDK 后续新增
公开接口，应先升级 SDK wheel，再扩展本 Provider 的能力声明。

## 验收

自动测试验证配置、能力声明、A/B 映射、单位边界、异常转换和资源释放，不连接
真实设备。上线前还需要在 Windows 与 Linux 分别完成：

1. wheel 导入及原生动态库加载；
2. 控制器连接与左右臂映射；
3. 世界/基座/工具坐标标定；
4. 关节限位和目标可达性；
5. 阻塞与非阻塞直线运动；
6. 断线、控制器拒绝、设备故障与关闭流程。

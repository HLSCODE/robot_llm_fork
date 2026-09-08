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
- `move_to_joints(arm, JointVector, options)` 七关节绝对目标（角度）；
- `move_joint_increment` 七关节增量，基于反馈计算目标并校验限位；
- `move_pose_increment` 末端局部坐标系增量，委托 SDK `movel_step`；
- 快速停止双臂、软件急停；停止调用不等待阻塞运动持有的命令锁；
- 单臂拖拽模式切换、双臂统一采集、按目录保存 raw/TXT/FMV；
- 指定 FMV 文件的回放接口 `run_trajectory`；
- SDK 异常向统一 `RobotOperationError` 的错误码与诊断信息转换。

明确不声明：

- `move_j` 笛卡尔目标运动：SDK 的 `movej` 只接受关节角，没有公开笛卡尔目标
  逆解接口；
- 夹爪、流式遥操作、工具架换装；
- 通用轨迹回放：SDK 没有公开回放完成状态。录制已独立接入，GUI 可直接使用；
  录制不依赖回放能力，不会自动创建当前设备无法执行的回放动作。

## 新版 SDK 使用约定

### 创建关节运动动作

当前 Provider 为 `tianji` 时，移动类选择“机械臂”，运动模式可选“七关节目标运动
（move_j）”或“直线运动（move_l）”。前者显示七关节角输入框（单位：度），可通过
获取按钮读取所选机械臂的实时关节角；后者使用六维位姿及定位补偿。

七关节动作保存为明确的目标类型，内部模式名为 `move_joints`，避免与 RealMan 的
六维笛卡尔 `move_j` 混淆：

```json
{"目标": "机械臂", "臂": "左", "模式": "move_joints", "关节角": [0, 0, 0, 0, 0, 0, 0]}
```

数组必须恰好包含七个有限数值，禁止同时包含 `点位` 或 `补偿`。执行器通过
`ArmJointMotion.move_to_joints()` 调用 SDK `movej(joints_deg=...)`；设备边界继续校验
现场配置的关节限位。示例零值仅用于说明格式，不是建议的真机目标。

编辑旧 Tianji 六维 `move_j` 时，会提示重新获取七关节角或选择直线运动，不将六维
点位自动解释为关节角。未重新保存的旧动作仍保持原来的语义。RealMan 的表单及执行
路径保持不变，Robot Profile 隔离也继续生效。

### 录制与回放

GUI 录制通过 `TrajectoryRecordingDevice.trajectory_recorder` 获取厂商适配器。
RealMan 保留单臂拖拽、停止、保存单文件的流程；Tianji 拖拽所选臂，同时采集双臂。
点击确定后恢复所选臂正常模式，再停止采集并保存到当前 Robot Profile 的轨迹目录。
每次录制独占一个目录，结果包含原始文件及左右臂 TXT/FMV，界面显示全部产物。

开始、保存和取消均在后台线程执行。取消 Tianji 录制时，因 SDK 没有停止且丢弃接口，
数据保留在轨迹根目录的 `recovery/<会话ID>` 中。保存或取消失败会保留设备占用，
防止在采集状态不明确时继续执行工作流。安全停止后的清理不会重新切换正常位置模式。

录制协议与 `TrajectoryPlayback` 回放协议独立；旧 `TrajectoryControl` 作为兼容组合
接口保留，现有 RealMan 回放继续使用。Tianji 只声明 `trajectory_recording` 能力。

`RobotConfig` 新增的左右臂采集选项已由驱动提供，默认采集两臂全部七个关节的位置。
默认 SDK 录制目录使用应用配置解析后的当前 Robot Profile 轨迹目录（包括用户配置的
路径覆盖），不使用 SDK 的 `C:\tj_trajectory_data` 或 `/tmp/tj_trajectory_data` 默认值。

显式录制接口调用顺序为：

```python
adapter.start_drag_teaching(ArmId.LEFT)
adapter.start_recording()  # 控制器级双臂采集，一次只能有一个采集任务
adapter.stop_drag_teaching(ArmId.LEFT)
adapter.save_recording(output_directory)  # 停止采集并保存多个文件
```

保存失败和重复启动采集会报错；恢复正常模式与停止采集是两个独立操作。回放必须明确
指定已有 `.fmv` 文件，不会自动选择 SDK 临时目录中的“最新轨迹”。七关节目标越限会
在发送前拒绝，避免 SDK 自动截断目标后仍返回成功。

新版 SDK 仍有以下限制，需在 SDK 层继续补齐：

- 阻塞运动及回放内部没有超时，停止机械臂后等待循环也未必退出；不要在 GUI 主线程
  调用这些阻塞接口。停止接口本身可并发调用，但不等于运动线程已完成取消。
- `run_trajectory(is_block=False)` 仍会阻塞等待到达轨迹首点，不是完全异步接口。
- 初始化会自动启动末端按钮监控。按钮释放回调硬编码调用 `stop_and_save_data("data")`，
  此 SDK 内部路径覆盖无法通过 `traj_data_dir` 改变；需要 SDK 增加按钮开关/回调和目录配置。
- 暂无公开的错误状态、反馈时间戳、轨迹句柄及取消/完成查询；不能将位置接近终点等同于
  控制器确认运动完成。

临时源码目录不加入运行依赖，也不通过 SDK 私有成员绕过上述限制。同版本 wheel 更新后，
应提交 `uv.lock` 中的新哈希及 `transitions` 依赖，再运行 `uv sync --frozen --extra hardware`。

## 验收

自动测试验证配置、能力声明、A/B 映射、单位边界、异常转换和资源释放，不连接
真实设备。上线前还需要在 Windows 与 Linux 分别完成：

1. wheel 导入及原生动态库加载；
2. 控制器连接与左右臂映射；
3. 世界/基座/工具坐标标定；
4. 关节限位和目标可达性；
5. 阻塞与非阻塞直线运动；
6. 断线、控制器拒绝、设备故障与关闭流程。

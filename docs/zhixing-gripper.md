# 知行夹爪 Provider

原始协议联调 Demo 已整理到 [examples/hardware/gripper_control_v2.py](../examples/hardware/gripper_control_v2.py)；正式运行使用本文的 Provider 配置，不应同时运行 Demo 占用同一串口。

知行夹爪通过统一末端 Provider 使用，站号不再隐含左右臂。旧 `zhixing_gripper_*` 配置、四个厂商专用按钮及“知行夹爪”动作已移除，不做兼容。

## 配置

在 `end-effectors.toml`（由入口 include）或入口 TOML 中声明，例如左臂固定外接夹爪：

```toml
[end_effectors.connections.gripper_bus]
port = "COM4"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1

[end_effectors.tools.zhixing_gripper_1]
kind = "gripper"
provider = "zhixing"
connection = "gripper_bus"
station = 17
response_timeout_seconds = 1.0
motion_timeout_seconds = 8.0
speed = 100
torque = 60
acceleration = 2000
deceleration = 2000
position_min = 0
position_max = 4000

[end_effectors.mounts.left]
mode = "fixed"
tool = "zhixing_gripper_1"
```

按实际串口填写 COM 或 Linux 设备路径。两臂共享总线时声明同一 connection，但工具实例与站号必须分别配置。
作为快换工具时，将实例加入相应 exchangeable 挂载的 tools，和快换手引用同一连接。
配置的行程直接用于比例映射（寄存器单位按设备协议）；没有启动时自动读取行程或旧标定文件的兼容路径。

## 操作与安全

GUI 选择“机械臂 + 工具实例”。动作选择通用“夹爪”，例如：

```json
{"执行器":"夹爪","arm":"left","tool_id":"zhixing_gripper_1","操作":"定位","闭合度":37.5}
```

闭合度 0 为全开、100 为全闭。首次操作默认读取设备行程（`read_device_range = true`），不会在启动时读取，也不会执行回零或标定；读取失败使用配置的 `position_min` / `position_max` 并记录警告。若要严格使用手动标定端点，设置 `read_device_range = false`。默认最小位置对应全开；实机方向相反时设置 `reverse_direction = true`，此时最大位置对应全开。位置到达或力矩到达视为完成，力矩到达表示接触/受阻，不等于完全闭合，不能以此判断空载全闭。日志记录站号、闭合度、原始目标位置、映射范围和到位标志；报警、通信异常和超时报错。不要为了追求全闭盲目增大夹持力。
共享串口的收发互斥；同连接的执行资源保守串行。启动不发送握手或运动指令，明确操作时才通信。
动作失败不盲目重发。取消在当前阻塞调用返回后生效，不宣称不存在的硬件停止能力。
退出等待后台调用结束后释放串口，不发送可能松开负载的失能指令。

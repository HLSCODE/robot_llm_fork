# 末端配置场景速查

入口为 `config/config.toml` 的 `include`：加载 `fragments/end-effectors.toml`、`fragments/end-effectors/connections.toml`，以及所需厂商片段。子配置不使用嵌套 include。

所有示例中的实例 ID 都与左右臂无关，由 `mounts.left/right` 决定实际归属。修改场景是**替换现有表**，不要重复追加同名表。

## 1. 左臂大寰、右臂知行，独立串口

`end-effectors.toml`：

```toml
[end_effectors]
status_poll_interval_seconds = 2.0

[end_effectors.mounts.left]
mode = "fixed"
tool = "dh_rgd_gripper_1"

[end_effectors.mounts.right]
mode = "fixed"
tool = "zhixing_gripper_1"
```

`connections.toml`（端口仅为示例，必须按现场替换）：

```toml
[end_effectors.connections.tool_bus_1]
transport = "serial"
port = "COM9"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1

[end_effectors.connections.tool_bus_2]
transport = "serial"
port = "COM10"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1
```

`zhixing.toml` 使用 `connection = "tool_bus_1"`，`dh-rgd.toml` 使用 `connection = "tool_bus_2"`；各自完整工具表复制对应 `.example.toml`，保留厂商运动参数。两个独立串口的站号均可为1。

本地实际配置也可以使用其他连接 ID，例如 `dh_rgd_bus_1`，只要声明与引用一致即可。

## 2. 两臂同型号，共用一条 RS485 总线

连接只声明一次，两个工具通过不同站号区分。以下两只大寰配置可替换厂商片段中的原工具表：

```toml
[end_effectors.tools.dh_rgd_gripper_1]
kind = "gripper"
provider = "dh_rgd"
connection = "tool_bus_1"
station = 1
motion_timeout_seconds = 8.0
rotation_units_per_degree = 1.0

[end_effectors.tools.dh_rgd_gripper_2]
kind = "gripper"
provider = "dh_rgd"
connection = "tool_bus_1"
station = 2
motion_timeout_seconds = 8.0
rotation_units_per_degree = 1.0
```

左右臂分别固定挂载 `_1` 和 `_2`，其余参数采用模型默认值；若需调整力、速度、端点等，分别从厂商完整示例补入，两个实例不相互继承。角度比例必须与各自固件一致。

两只知行同理，ID 改为 `zhixing_gripper_1/2`，Provider 改为 `zhixing`，删除 RGD 专属参数，按各夹爪实际行程配置 `position_min/max` 等知行参数。

**硬件站号必须预先设置成不同值。** 写 `station = 2` 不会把硬件地址改为2。同一总线上串口通信参数必须一致，程序只保证通信事务互斥，不保证两只夹爪同时启动运动。

不同厂商设备共用同一物理总线也必须满足电气、协议及站号兼容条件，不能因为 Provider 名不同就使用相同 Modbus 地址。优先使用独立连接。

## 3. 两臂同型号，独立串口

沿用场景2的两个工具实例，但第二实例改为 `connection = "tool_bus_2"`，站号可以仍为1。连接按场景1声明两个不同物理端口。两臂分别挂载不同工具实例。

## 4. SDK 内置夹爪

```toml
[end_effectors.tools.robot_gripper_1]
kind = "gripper"
provider = "robot"

[end_effectors.tools.robot_gripper_2]
kind = "gripper"
provider = "robot"
```

两臂分别固定挂载这两个 ID，不填写串口连接或 SDK 机械臂编号。必须由当前机器人 Provider 提供内置夹爪能力；外接大寰/知行不使用这种配置。

## 5. 快换手与备选工具

```toml
[end_effectors.mounts.right]
mode = "exchangeable"
changer = "tool_changer_1"
tools = ["pipette_1", "zhixing_gripper_1", "dh_rgd_gripper_1"]
```

同时加载快换手、吸液枪及夹爪片段，所有这些实例引用同一通信连接。实际动作明确选择工具实例；系统显示快换手锁紧/供电状态，不推断实际工具身份，工作流保证装上正确工具。快换模式不能同时声明 `tool`。

## 6. 单臂工具或无工具

未安装工具的臂只配置 `mode = "none"`，不能保留 `tool`、`changer` 或 `tools`。另一臂可独立使用任何上述挂载模式。示例默认两臂均为 none，避免初始化模板后误操作硬件。

## 实机使用前

- 检查端口、硬件站号、线缆、供电及 RS485 接线。不要把两个独立 USB 适配器配置成同一个端口。
- 大寰先显式执行初始化，再测试开闭与小角度旋转；核对 `rotation_units_per_degree` 和 `open_position/closed_position`，详见 [RGD 接入说明](dh-rgd-gripper.md)。
- 工具未连接不阻止主界面启动，但实际工具动作会失败；不自动回退到其他夹爪。
- 本地实际 `.toml` 不提交版本库，提交 `.example.toml` 与说明文档。配置初始化为增量复制，不覆盖已存在的实际配置。

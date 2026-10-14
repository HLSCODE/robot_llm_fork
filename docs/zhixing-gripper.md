# 知行夹爪

两只知行夹爪共用一条 Modbus RTU 总线、一个串口连接：左侧站号为 **1**，右侧站号为 **2**。原有机械臂夹爪控制保持独立。

## 配置

在 `config/fragments/devices.toml` 的 `[devices]` 节设置总线串口；如果使用单文件配置，则放在 `config/config.toml` 的 `[devices]` 节。

```toml
zhixing_gripper_serial_port = "COM4"
zhixing_gripper_baudrate = 115200
zhixing_gripper_timeout = 1.0
zhixing_gripper_wait_timeout = 8.0
```

默认串口来自项目根目录的 `gripper_control_v2.py`，实际部署时应按 USB 转串口的名称填写，例如 Windows 的 `COM4` 或 Linux 的 `/dev/ttyUSB3`。串口应专用于这条总线。完整运动参数和备用行程配置见 `config/fragments/devices.example.toml`；也可通过 `ZHIXING_GRIPPER_SERIAL_PORT` 等同名大写环境变量覆盖。

启动时检查两个站号，并分别读取设备的 `0x0404/0x0406` 有符号 32 位行程映射。读取失败或映射异常时，日志会提示使用该侧的备用范围，默认 `0..4000`（单位为 0.01 mm）。旧脚本的 `gripper_config.json` 不会自动加载；如使用过本地标定，请将其值填入对应侧的 `zhixing_gripper_left_pos_min/max` 或 `zhixing_gripper_right_pos_min/max`。

## 界面与动作

“基础控制”区域原有夹爪按钮下方新增四个按钮：知行夹爪左开、左关、右开、右关。**开 = 0%，关 = 100%**。设备未连接或手动操作执行期间，按钮禁用；串口读写与等待到位在后台线程执行。

新建执行器动作时，选择“知行夹爪”，再选择站号和开合度。开合度支持 `0..100` 的小数值，保存和再次编辑时保留参数。例如右侧夹爪移动到 37.5%：

```json
{"执行器": "知行夹爪", "编号": 2, "开合度": 37.5}
```

执行器动作和手动按钮使用同一个总线资源锁。左右动作应顺序编排；不会同时打开两个串口连接。位置到达或力矩到达均视为完成（与参考脚本一致），设备报警、通信异常和运动超时会报告失败。取消采用项目现有的阻塞设备动作策略，在当前设备调用返回后生效；不声明未经验证的硬件急停能力。

程序退出时等待后台调用结束，再对已尝试使能的夹爪逐一失能并关闭总线。仿真模式可验证按钮和动作编排，无需连接实物。

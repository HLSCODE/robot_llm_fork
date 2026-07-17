# 加粉装置控制模块

## 概述

加粉装置是安装在机械臂末端的一个执行器模块，通过同一条 RS-485 总线控制三个设备：

| 设备 | 功能 | 默认Modbus地址 | 系列 |
|---|---|---|---|
| **加粉夹爪** (Electric Gripper) | 夹取/释放粉盒 | 9 | DK EF-series |
| **针升降电机** (Stepper Motor) | 控制针的上升/下降 | 7 | M系列 |
| **针旋转电机** (Stepper Motor) | 控制针的旋转 | 6 | M系列 |

三个设备共用一条 RS-485 总线（通过 USB 转 485 连接主机），通过 Modbus RTU 协议通信。

---

## 配置文件

配置项在 `config.env` 或 `src/core/config_loader.py` 中设置：

```ini
# 串口
TAPPING_SERIAL_PORT=/dev/ttyACM0
TAPPING_BAUDRATE=115200
TAPPING_TIMEOUT=0.5

# 设备地址
TAPPING_GRIPPER_ADDRESS=9
TAPPING_LIFT_ADDRESS=7
TAPPING_ROTATION_ADDRESS=6
```

---

## 程序化调用

### 直接使用 TappingController

```python
from src.devices.tapping_controller import TappingController

# 从全局配置创建
ctrl = TappingController.from_config()
try:
    # 使能两个电机
    ctrl.enable_all()

    # 夹爪操作
    ctrl.gripper_grip()         # 完全闭合
    ctrl.gripper_release()      # 完全张开
    ctrl.gripper_move_to(50)    # 移动到 50% 开度

    # 针升降
    ctrl.lift_up(2000)          # 上升 2000 步
    ctrl.lift_down(2000)        # 下降 2000 步
    ctrl.lift_stop()            # 停止

    # 针旋转
    ctrl.rotation_cw(5000)      # 正转 5000 步
    ctrl.rotation_ccw(5000)     # 反转 5000 步
    ctrl.rotation_stop()        # 停止

finally:
    ctrl.close()  # 释放串口
```

---

## 在动作序列中使用

在 `.task` 任务文件中，通过 `MANIPULATE` 动作类型，`执行器` 参数设为 `"加粉装置"` 即可编排。

### 夹爪操作

```json
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "夹爪闭合"}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "夹爪张开"}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "夹爪移动到", "开度": 50}}
```

**注意：** 此夹爪型号通过 `TARGET_POSITION_PERCENT`（0x0105）寄存器控制开度，不支持独立的 grip/release 寄存器（0x0109）。"夹爪闭合"等效于 `move_to(100)`，"夹爪张开"等效于 `move_to(0)`。

### 针升降操作

```json
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针上升", "步数": 2000}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针下降", "步数": 2000}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针停止"}}
```

### 针旋转操作

```json
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针正转", "步数": 5000}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针反转", "步数": 5000}},
{"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针旋转停止"}}
```

### 完整取粉流程示例

```json
[
  {"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "使能"}},
  {"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "夹爪张开"}},
  {"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针上升", "步数": 2000}},
  {"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "夹爪闭合"}},
  {"type": "MANIPULATE", "parameters": {"执行器": "加粉装置", "操作": "针下降", "步数": 2000}}
]
```

---

## 支持的完整操作列表

| 操作名称 | 参数 | 说明 |
|---|---|---|
| `夹爪闭合` | — | 完全闭合 (move_to 100) |
| `夹爪张开` | — | 完全张开 (move_to 0) |
| `夹爪移动到` | `开度` (int, 0-100) | 移动到指定百分比 |
| `针上升` | `步数` (int, 默认5000) | 升降电机移动到指定绝对位置，用于上升/回收 |
| `针下降` | `步数` (int, 默认5000) | 升降电机移动到指定绝对位置，用于下降/加粉 |
| `针停止` | — | 急停升降电机 |
| `针正转` | `步数` (int, 默认5000) | 旋转电机正转到指定步数 |
| `针反转` | `步数` (int, 默认5000) | 旋转电机反转到零位 |
| `针旋转停止` | — | 急停旋转电机 |
| `使能` | — | 使能升降和旋转两个电机 |

---

## 软件架构

```
action_executor.py
  └── _execute_manipulate()
        └── elif executor == '加粉装置'
              └── _execute_tapping()
                    └── TappingController.from_config()
                          ├── SerialTransport (共享串口)
                          ├── ElectricGripper  (夹爪)
                          └── StepperBus
                                ├── StepperMotor(地址7, 升降)
                                └── StepperMotor(地址6, 旋转)
```

- `tapping_controller.py` 封装了所有设备操作，供 `action_executor.py` 调用
- 不修改任何已有代码，通过添加 `elif` 分支扩展
- 串口连接在每次执行时创建，执行完毕后关闭

---

## 硬件接线

```
PC (USB-A)
  └── USB 转 RS-485 适配器 (/dev/ttyACM0)
        └── RS-485 总线 (A/B 双线)
              ├── 夹爪 (地址 9)
              ├── 针升降电机 (地址 7)
              └── 针旋转电机 (地址 6)
```

**注意：** 如果使用不带自动方向控制的 RS-485 适配器，可能需要启用 Linux 内核的 RS-485 模式（`ioctl` 设置 `SERIAL_RS485`）。调试脚本 `test_devices.py` 支持 `--rs485` 参数启用该模式。

---

## 调试工具

项目根目录下的 `test_devices.py` 提供了独立的调试脚本：

```bash
# 扫描总线上的设备
python test_devices.py --scan

# 交互模式，逐条控制
python test_devices.py -i

# 自动化测试序列
python test_devices.py
```

交互模式下支持全部加粉装置操作命令：`grip`, `release`, `gripper_open`, `lift_up`, `lift_down`, `rot_cw`, `rot_ccw` 等。

# 智能闭环加粉任务

## 概述

智能闭环加粉是在任务流中使用的高级动作。它不是固定执行几条“针下降/旋转/上升”指令，而是在运行时持续读取天平，根据目标加粉量自动控制加粉装置旋转，直到达到目标重量后返回主任务流程。

当前实现保留原有 `"加粉装置"` 执行器作为手动调试动作，新增 `"智能加粉"` 执行器用于闭环控制。

核心链路：

```text
GUI/任务流
  -> ARM_ACTION / 执行器=智能加粉
  -> ExecutionManager / ActionEngine
  -> PowderDispenseAgent
  -> balance_reader_simple.read_balance()
  -> DeviceRuntime / PowderDispenser
  -> 加粉装置升降/旋转
```

## 任务配置

在 GUI 中新增执行类动作，执行器选择 `"智能加粉"`，参数如下：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `目标重量mg` | 100 | 目标加粉量，单位 mg |
| `容差mg` | 5 | 达到目标的允许误差，单位 mg |
| `最大轮次` | 20 | 最多尝试多少轮加粉 |
| `稳定等待秒数` | 2 | 每次旋转加粉后等待天平稳定的时间 |
| `安全位置步数` | 0 | 结束/异常时升降电机返回的绝对位置 |
| `加粉位置步数` | 50000 | 执行加粉时升降电机前往的绝对位置 |
| `旋转原点步数` | 0 | 结束/异常时旋转电机返回的绝对位置 |
| `大步步数` | 20000 | 剩余量较大时的旋转步数 |
| `中步步数` | 8000 | 剩余量中等时的旋转步数 |
| `小步步数` | 2000 | 接近目标时的旋转步数 |
| `微步步数` | 500 | 非常接近目标时的旋转步数 |

任务文件中的参数示例：

```json
{
  "type": "ARM_ACTION",
  "parameters": {
    "执行器": "智能加粉",
    "操作": "加粉到目标重量",
    "目标重量mg": 100,
    "容差mg": 5,
    "最大轮次": 20,
    "稳定等待秒数": 2,
    "安全位置步数": 0,
    "加粉位置步数": 50000,
    "旋转原点步数": 0,
    "大步步数": 20000,
    "中步步数": 8000,
    "小步步数": 2000,
    "微步步数": 500
  }
}
```

说明：天平读数接口返回单位按 `g` 处理，智能加粉任务参数按 `mg` 处理，内部会自动换算。

## 控制流程

执行 `"智能加粉"` 时，系统会：

1. 使能加粉装置电机。
2. 读取初始天平重量。
3. 计算目标终点重量：`初始重量 + 目标重量mg / 1000`。
4. 升降电机移动到加粉位置。
5. 按剩余重量选择旋转步数并加粉。
6. 等待天平稳定。
7. 再次读取天平并判断是否达到目标。
8. 达到目标、超量、失败或停止时，都尝试回到安全状态。

当前步数策略按实测 `2000步≈2mg` 估算，即约 `1000步/mg`。默认规则为：

| 剩余量 | 默认旋转步数 |
|---:|---:|
| `> 25mg` | 20000，约 20mg |
| `10-25mg` | 8000，约 8mg |
| `3-10mg` | 2000，约 2mg |
| `<= 3mg` | 500，约 0.5mg |

大模型只用于读取天平画面，不直接决定设备动作。

## 相关配置

可在环境变量或 `src/core/config_loader.py` 中调整：

```ini
# 加粉装置串口和地址
TAPPING_SERIAL_PORT=/dev/ttyACM0
TAPPING_BAUDRATE=115200
TAPPING_TIMEOUT=0.5
TAPPING_GRIPPER_ADDRESS=9
TAPPING_LIFT_ADDRESS=7
TAPPING_ROTATION_ADDRESS=6

# 智能加粉位置
TAPPING_LIFT_SAFE_POSITION=0
TAPPING_LIFT_DISPENSE_POSITION=50000
TAPPING_ROTATION_HOME_POSITION=0

# 智能加粉步数策略
POWDER_DISPENSE_LARGE_STEP=20000
POWDER_DISPENSE_MEDIUM_STEP=8000
POWDER_DISPENSE_SMALL_STEP=2000
POWDER_DISPENSE_MICRO_STEP=500
```

视觉读数使用 `src/vision/balance_reader_simple.py`，需要配置：

```ini
VVEAI_API_KEY=你的key
VVEAI_BASE_URL=https://api.vveai.com/v1
VVEAI_MODEL=doubao-seed-1-8-251228
```

## 安全保护

智能加粉包含以下保护：

- 连续天平读数失败会停止任务。
- 达到最大轮次会结束闭环并返回成功，让后续任务继续执行。
- 超过目标重量并超过容差会返回失败。
- 当前重量相比上一轮明显下降会返回失败。
- 用户停止任务时会尽快退出闭环。
- 无论成功、失败或异常，都会尝试执行：
  - 停止旋转
  - 升降回安全位置
  - 旋转回原点
  - 串口由 DeviceRuntime 在应用关闭时统一释放

## 调试建议

首次联调不要直接使用 `100mg`，建议按以下顺序：

```text
1. 使用 test_devices.py 确认升降、旋转、夹爪方向和地址正确。
2. 单独运行 balance_reader_simple.py，确认天平读数稳定。
3. 将智能加粉目标设为 10mg，观察每轮日志和实际加粉效果。
4. 根据粉末流速调整 POWDER_DISPENSE_*_STEP。
5. 再测试 100mg。
```

运行无硬件单元测试：

```bash
python -m unittest tests.test_powder_dispense_agent
```

语法检查：

```bash
python -m py_compile \
  src/agents/powder_dispense_agent.py \
  src/devices/tapping_controller.py \
  src/execution/engine.py \
  src/device_runtime/factory.py \
  src/gui/dialogs.py \
  src/robot_server/ws_server.py \
  src/core/config_loader.py
```

## 代码位置

| 文件 | 作用 |
|---|---|
| `src/agents/powder_dispense_agent.py` | 智能闭环加粉核心逻辑 |
| `src/devices/tapping_controller.py` | 加粉装置底层控制 |
| `src/vision/balance_reader_simple.py` | 大模型视觉读取天平 |
| `src/execution/engine.py` | 唯一动作执行入口 |
| `src/device_runtime/factory.py` | 加粉设备注册和生命周期 |
| `src/gui/dialogs.py` | GUI 动作配置面板 |
| `src/robot_server/ws_server.py` | WebSocket 动作 schema |

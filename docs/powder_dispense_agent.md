# 智能闭环加粉任务

## 概述

智能闭环加粉是在任务流中使用的高级动作。它不是固定执行几条“针下降/旋转/上升”指令，而是在运行时持续读取天平，根据目标加粉量自动控制加粉装置旋转，直到达到目标重量后返回主任务流程。

当前实现保留原有 `"加粉装置"` 执行器作为手动调试动作，新增 `"智能加粉"` 执行器用于闭环控制。

### 当前实现边界

当前生产实现是一个**确定性规则闭环**，不是 LLM 多 Agent 系统：

- `PowderDispenseAgent` 是规则策略和闭环状态的唯一所有者。
- 天平读数、剩余量、阈值和旋转步数共同决定下一步，规则可离线复现。
- 大模型只可以作为天平画面的读数适配器，不直接生成设备命令、修改阈值或决定终态。
- `ExecutionManager`、资源租约、取消和 `DeviceRuntime` 继续负责执行与设备安全边界。

`docs/项目综述.md` 中 RecipeArchitect、DispenseControl、QualityInspector 和
ProcessOrchestrator 四 Agent 属于未立项概念方案，不代表当前代码，也不是当前
重构目标。2026-08-04 评审决定本轮不立项：现阶段没有足够的多粉种数据、量化收益
或可验证安全约束证明其优于规则策略，因此不增加 AgentManager、LLM 决策接口或
兼容层。

核心链路：

```text
GUI/任务流
  -> ARM_ACTION / 执行器=智能加粉
  -> ExecutionManager / PowderDispenseActionHandler
  -> PowderDispenseAgent
  -> DeviceRuntime / BalanceReader
  -> CameraAccessService + LLMRegistry / balance_reading profile
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
| `大步阈值mg` | 25 | 剩余量超过该值时使用大步 |
| `中步阈值mg` | 10 | 剩余量超过该值时使用中步 |
| `小步阈值mg` | 3 | 剩余量超过该值时使用小步，否则使用微步 |

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
    "微步步数": 500,
    "大步阈值mg": 25,
    "中步阈值mg": 10,
    "小步阈值mg": 3
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

每次实际旋转并完成天平复读后，结果都会追加一条不可变轮次审计记录，包含：
轮次号、动作前后读数、动作前已加/剩余重量、目标容差、旋转步数、本轮重量增量
和轮次判定。`PowderDispenseResult.round_records` 保留完整记录，执行日志同时以
稳定的 `key=value` 字段输出，便于追查阈值选择、异常读数和最终判定。

当前默认步数策略按实测 `2000步≈2mg` 估算，即约 `1000步/mg`。步数与三个
阈值均为显式配置；阈值必须满足 `大步 > 中步 > 小步 > 0`。默认规则为：

| 剩余量 | 默认旋转步数 |
|---:|---:|
| `> 25mg` | 20000，约 20mg |
| `10-25mg` | 8000，约 8mg |
| `3-10mg` | 2000，约 2mg |
| `<= 3mg` | 500，约 0.5mg |

大模型只用于读取天平画面，不直接决定设备动作。

## 相关配置

可在环境变量或 `src/configuration/config_loader.py` 中调整：

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
POWDER_DISPENSE_LARGE_STEP_THRESHOLD_MG=25
POWDER_DISPENSE_MEDIUM_STEP_THRESHOLD_MG=10
POWDER_DISPENSE_SMALL_STEP_THRESHOLD_MG=3
```

电子秤是 `DeviceRuntime` 中的正式设备能力。真实 Provider 通过
`CameraAccessService` 独占获取受管相机画面，再通过唯一 `LLMRegistry` 的
`balance_reading` 视觉任务识别数值；不再自行创建 OpenCV 摄像头或直连独立 HTTP API。

相机与 LLM 使用项目现有统一配置，电子秤只增加以下选择和等待配置：

```ini
# 名称可匹配受管相机返回的 serial 或 name；留空取首个有效画面
BALANCE_CAMERA_NAME=
BALANCE_CAMERA_WAIT_TIMEOUT_SECONDS=2

# balance_reading 默认使用 dashscope 视觉模型
DASHSCOPE_API_KEY=你的key
DASHSCOPE_MODEL=qwen-vl-max
```

摄像头索引、尺寸和 Provider 继续由 `CAMERA_PROVIDER`、`WEBCAM_*` 或
`REALSENSE_*` 配置。所有值只在启动边界解析；设备 Provider 本身不读取环境变量。

## 安全保护

智能加粉包含以下保护：

- 连续天平读数失败会停止任务。
- 达到最大轮次但仍未进入目标容差时返回
  `MAX_ROUNDS_REACHED`，统一执行结果为失败并使用
  `target_not_reached` 错误码，后续动作不会继续执行。
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
2. 在 GUI 相机测试中确认 `BALANCE_CAMERA_NAME` 对应画面稳定，并用定向测试验证读数 Provider。
3. 将智能加粉目标设为 10mg，观察每轮日志和实际加粉效果。
4. 根据粉末流速调整 POWDER_DISPENSE_*_STEP。
5. 再测试 100mg。
```

运行无硬件单元测试：

```bash
python -m pytest -q tests/test_balance_reader.py tests/test_powder_dispense_agent.py
```

版本化离线策略案例位于
`data/regression/powder_dispense_policy_cases.json`，由 pytest 统一质量门禁执行。
案例覆盖最后一轮达标、最后一轮超量、异常下降、最大轮次失败，以及大/中/小/微
步进的完整收敛过程。调整规则、阈值或终态语义时必须同步更新案例并说明理由。

## 四 Agent 方案重开条件

只有同时满足以下条件，才重新评审独立的多 Agent 项目：

1. 已积累多粉种、目标量、环境和逐轮结果的版本化数据集。
2. 已定义规则基线无法满足的量化目标，例如成功率、超调率、轮次或耗时。
3. LLM/策略输出被限制为可校验的计划建议，硬件命令仍经过确定性安全策略。
4. 建立离线回放、simulation、故障注入和真实硬件分阶段验收。
5. 明确 token、延迟、模型不可用和输出漂移时的失败语义，禁止静默退回不同策略。

语法检查：

```bash
python -m py_compile \
  src/execution/workflows/powder_dispense.py \
  src/devices/tools/powder_dispenser/driver.py \
  src/execution/engine.py \
  src/devices/runtime/factory.py \
  src/gui/views/dialogs.py \
  src/robot_server/ws_server.py \
  src/configuration/config_loader.py
```

## 代码位置

| 文件 | 作用 |
|---|---|
| `src/execution/workflows/powder_dispense.py` | 智能闭环加粉核心逻辑 |
| `src/devices/tools/powder_dispenser/driver.py` | 加粉装置底层控制 |
| `src/devices/sensors/balance/provider.py` | 电子秤设备 Provider 与强类型读数 |
| `src/application/balance.py` | 托管相机与统一 LLM 的应用层装配适配器 |
| `src/execution/handlers/manipulation.py` | 加粉 action handler 与统一结果映射 |
| `src/devices/runtime/factory.py` | 加粉装置注册和生命周期 |
| `src/gui/views/dialogs.py` | GUI 动作配置面板 |
| `src/robot_server/ws_server.py` | WebSocket 动作 schema |

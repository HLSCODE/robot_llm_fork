# Robot Control 项目

## 项目简介
机器人操作编排系统，支持双臂机械臂（RealMan RM75）、升降平台、快换手、吸液枪、夹爪、颈部 PWM 舵机、视觉抓取等硬件的协调控制。集成 AI（GPT-4o/DeepSeek）自然语言任务规划。

## 技术栈
- **语言**: Python 3
- **GUI**: PyQt6（可选，`RUN_MODE=gui python run.py` 启动）
- **WebSocket 服务**: asyncio + websockets（默认模式，`python run.py` 启动）
- **机械臂 SDK**: RealMan RM C API (ctypes 封装)
- **视觉**: RealSense D435 + YOLO + SAM2
- **串口设备**: 快换手、ADP 吸液枪、继电器、ModbusMotor、PWM 颈部双轴舵机
- **AI/LLM**: OpenAI / DeepSeek API
- **配置**: python-dotenv (`config.env`)

## 项目结构
```
run.py              # 统一启动入口（根据 RUN_MODE 分派 GUI / Server）
config.env          # 环境变量配置
requirements.txt    # Python 依赖
src/
  core/
    launcher.py         # 统一启动器（init_hardware、run_gui、run_server）
    config_loader.py    # 配置单例加载器
    models.py           # ActionDefinition, SequenceItem 等数据模型
    storage.py          # 动作库/任务序列 JSON 持久化
  gui/
    main_window.py      # 主窗口（PyQt6）
    execution.py        # ExecutionThread (QThread, GUI 模式用)
  robot_server/
    action_executor.py  # ActionExecutor (纯 Python, 无 Qt 依赖)
    ws_server.py        # WebSocket 服务端
  devices/              # 串口/Modbus 设备驱动（ADP、快换手、ModbusMotor、PWMNeckController 等）
  pwm_sdk/              # 颈部双轴舵机 SDK（第三方，勿改业务逻辑）
  arm_sdk/              # 机械臂 SDK（RealMan RM C API ctypes 封装）
  cameras/              # RealSense / OpenCV 相机管理
  vision/               # YOLO + SAM2 视觉抓取
  ai_integration/       # AI 控制器、执行桥接
  llm/                  # LLM 客户端（OpenAI、DeepSeek）
  skill_system/         # 技能引擎、注册表、默认技能
  widgets/              # PyQt6 UI 组件
data/
  actions_library.json  # 动作库
  tasks/*.task          # 保存的任务序列
```

## 两种运行模式
1. **GUI 模式**: `RUN_MODE=gui python run.py` — PyQt6 图形界面，拖拽编排动作
2. **WebSocket 服务模式**（默认）: `python run.py` — 无 GUI，前端通过 WebSocket 发送指令控制机器人

## WebSocket 协议（与 GUI 功能完全对等）
前端指令:
- **执行控制**: `execute`, `execute_task`, `stop`, `pause`, `resume`
- **动作库管理**: `list_actions`, `create_action`, `delete_action`, `update_action`
- **序列编排**: `get_sequence`, `add_to_sequence`, `remove_from_sequence`, `move_in_sequence`, `clear_sequence`
- **任务持久化**: `list_tasks`, `save_task`, `load_task`, `delete_task`
- **AI 助手**: `ai_chat`, `ai_confirm`, `ai_cancel`, `ai_status`, `list_skills`
- **设备管理**: `status`, `init_robots`, `init_body`, `disconnect`, `test_camera`

服务端事件: `step_started`, `step_completed`, `step_failed`, `log`, `execution_finished`, `error`, `ai_status_changed`, `ai_skill_matched`, `ai_preview_ready`, `ai_execution_finished`, `device_status_changed`, `camera_test_result`

## 常用命令
```bash
uv pip install -r requirements.txt   # 安装依赖（推荐 uv）
python run.py                        # WebSocket 服务模式（默认）
python run.py --simulation           # 模拟模式（不连硬件）
python run.py --port 9000            # 自定义端口
RUN_MODE=gui python run.py           # GUI 模式
```

## 开发进度
- [x] GUI 模式完整实现（动作库、序列编排、执行引擎）
- [x] AI 自然语言任务规划（GPT-4o / DeepSeek）
- [x] WebSocket 服务模式（2026-04-08 新增，2026-04-09 功能补全）
  - `action_executor.py` — 纯 Python 执行引擎，去除 Qt 依赖
  - `ws_server.py` — WebSocket 服务端，与 GUI 功能完全对等
  - 支持: 动作库CRUD、序列编排、任务持久化、AI自然语言规划、设备管理、相机测试
- [x] PWM 颈部双轴舵机集成（2026-04-23）
  - `src/pwm_sdk/` — 第三方 SDK 源码（水平 360° + 垂直 180°）
  - `src/devices/pwm_neck.py` — 项目适配层 `PWMNeckController`
  - 统一初始化入口 `launcher.init_hardware()` 返回三元组
  - 两种舵机参数独立可配（`PWM_NECK_H_*` / `PWM_NECK_V_*`，从 `config.env` 自动读取）
- [ ] 前端对接 WebSocket 服务
- [ ] PWM 颈部舵机动作类型（`MOVE_NECK`）接入技能系统
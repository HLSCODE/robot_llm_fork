# Robot Control 项目

## 项目简介
机器人操作编排系统，支持双臂机械臂（RealMan RM75）、升降平台、快换手、吸液枪、夹爪、视觉抓取等硬件的协调控制。集成 AI（GPT-4o/DeepSeek）自然语言任务规划。

## 技术栈
- **语言**: Python 3
- **GUI**: PyQt6（可选，`run.py` 启动）
- **WebSocket 服务**: asyncio + websockets（无 GUI 模式，`run_server.py` 启动）
- **机械臂 SDK**: RealMan RM C API (ctypes 封装)
- **视觉**: RealSense D435 + YOLO + SAM2
- **串口设备**: 快换手、ADP 吸液枪、继电器、ModbusMotor
- **AI/LLM**: OpenAI / DeepSeek API
- **配置**: python-dotenv (`config.env`)

## 项目结构
```
run.py              # GUI 模式入口
run_server.py       # WebSocket 服务模式入口（无 GUI）
config.env          # 环境变量配置
requirements.txt    # Python 依赖
src/
  main.py           # GUI 启动（QApplication）
  main_window.py    # 主窗口（PyQt6）
  models.py         # ActionDefinition, SequenceItem 等数据模型
  storage.py        # 动作库/任务序列 JSON 持久化
  config_loader.py  # 配置单例加载器
  execution.py      # ExecutionThread (QThread, GUI 模式用)
  action_executor.py # ActionExecutor (纯 Python, 无 Qt 依赖)
  ws_server.py      # WebSocket 服务端
  api_server.py     # 旧版 HTTP/WS 接口（依赖 Qt）
  ai_integration/   # AI 控制器、执行桥接
  llm/              # LLM 客户端（OpenAI、DeepSeek）
  skill_system/     # 技能引擎、注册表、默认技能
  widgets/          # PyQt6 UI 组件
  vertical_grab/    # 机械臂底层控制、视觉抓取
data/
  actions_library.json  # 动作库
  tasks/*.task          # 保存的任务序列
```

## 两种运行模式
1. **GUI 模式**: `python run.py` — PyQt6 图形界面，拖拽编排动作
2. **WebSocket 服务模式**: `python run_server.py` — 无 GUI，前端通过 WebSocket 发送指令控制机器人

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
pip install -r requirements.txt   # 安装依赖
python run.py                     # GUI 模式
python run_server.py              # WebSocket 服务模式
python run_server.py --simulation # 模拟模式（不连硬件）
python run_server.py --port 9000  # 自定义端口
```

## 开发进度
- [x] GUI 模式完整实现（动作库、序列编排、执行引擎）
- [x] AI 自然语言任务规划（GPT-4o / DeepSeek）
- [x] WebSocket 服务模式（2026-04-08 新增，2026-04-09 功能补全）
  - `action_executor.py` — 纯 Python 执行引擎，去除 Qt 依赖
  - `ws_server.py` — WebSocket 服务端，与 GUI 功能完全对等
  - `run_server.py` — 无 GUI 启动入口
  - 支持: 动作库CRUD、序列编排、任务持久化、AI自然语言规划、设备管理、相机测试
- [ ] 前端对接 WebSocket 服务
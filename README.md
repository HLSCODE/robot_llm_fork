# Robot Action Orchestrator

机器人操作编排系统，支持通过 PyQt6 图形界面或 WebSocket 服务编排、执行和管理机器人动作，并集成 AI 自然语言任务规划、视觉感知、双机械臂、底盘、升降平台、快换手、吸液枪和 MiniCPM 聊天代理等能力。

## 功能概览

- 双运行模式：`server` 模式提供 WebSocket API，`gui` 模式提供本地图形界面。
- 动作编排：支持动作库管理、拖拽/接口式序列编排、任务保存与加载。
- 执行控制：支持开始、暂停、恢复、停止，以及逐步骤状态和日志事件。
- AI 规划：可通过自然语言匹配技能，生成可确认执行的动作序列。
- 视觉能力：支持 RealSense / OpenCV 摄像头、YOLO + SAM 目标检测分割、相机帧订阅。
- 硬件控制：覆盖 RM 机械臂、底盘移动、Modbus 升降平台、PWM 颈部舵机、快换手、继电器和 ADP 吸液枪。
- MiniCPM 代理：通过 WebSocket 转发聊天请求，并支持可执行机器人指令识别。

## 运行环境

- Python 3.12
- Windows 或 Linux
- 硬件模式需要可访问的机械臂 IP、串口设备、摄像头和模型文件
- 前端联调或无硬件开发建议使用模拟模式

主要依赖见 `pyproject.toml` 和 `requirements.txt`，包括：

- `PyQt6`
- `websockets`
- `openai`
- `python-dotenv`
- `opencv-python`
- `pyrealsense2`
- `ultralytics`
- `scikit-learn`
- `robotic-arm`
- `pyserial`

## 快速开始

### 1. 安装依赖

使用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果使用 uv：

```bash
uv sync
```

### 2. 准备配置

复制配置模板：

```bash
cp config.env.example config.env
```

Windows PowerShell：

```powershell
Copy-Item config.env.example config.env
```

按实际环境修改 `config.env`。最常用配置项：

```env
RUN_MODE=server
SIMULATION_MODE=false

WEBSOCKET_HOST=0.0.0.0
WEBSOCKET_PORT=8765

OPENAI_API_KEY=
MODEL_PROVIDER=dashscope
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-turbo

CAMERA_PROVIDER=realsense
REALSENSE_DEVICE_SN=
YOLO_MODEL_PATH=models/best.pt
SAM_MODEL_PATH=models/sam2.1_l.pt

ROBOT1_IP=192.168.3.19
ROBOT1_PORT=8080
ROBOT2_IP=192.168.3.18
ROBOT2_PORT=8080
```

`config.env` 用于本机配置和密钥，默认不应提交到仓库。

### 3. 启动服务

默认启动 WebSocket Server：

```bash
python run.py
```

前端联调推荐模拟模式：

```bash
python run.py --simulation
```

指定端口：

```bash
python run.py --port 9000
```

启动 GUI：

```bash
RUN_MODE=gui python run.py
```

Windows PowerShell：

```powershell
$env:RUN_MODE='gui'
python run.py
```

## 运行模式

### WebSocket Server

`RUN_MODE=server` 是默认模式。服务启动后监听：

```text
ws://{host}:{port}/
```

默认地址为：

```text
ws://0.0.0.0:8765/
```

常用 action：

| 分类 | action |
|---|---|
| 状态与设备 | `status`, `init_robots`, `init_body`, `disconnect` |
| 执行控制 | `execute`, `execute_task`, `pause`, `resume`, `stop` |
| 动作库 | `list_actions`, `get_action_schema`, `create_action`, `update_action`, `delete_action` |
| 序列编排 | `get_sequence`, `add_to_sequence`, `remove_from_sequence`, `move_in_sequence`, `clear_sequence` |
| 任务管理 | `list_tasks`, `save_task`, `load_task`, `delete_task`, `get_task_detail`, `rename_task` |
| AI 规划 | `ai_chat`, `ai_confirm`, `ai_cancel`, `ai_status`, `list_skills` |
| 相机 | `camera_status`, `test_camera`, `subscribe_camera_frames`, `unsubscribe_camera_frames` |
| MiniCPM | `minicpm_status`, `chat_connect`, `chat`, `chat_disconnect` |

完整协议见 [docs/websocket-api.md](docs/websocket-api.md)。

### GUI

`RUN_MODE=gui` 启动 PyQt6 图形界面，主要包含：

- 设备状态栏
- 动作库 Tab
- AI 助手 Tab
- 序列编排区
- 姿态与基础控制面板
- 执行控制面板
- 执行日志

GUI 启动时会按配置初始化硬件；没有真实硬件时，请优先使用 `server --simulation` 做接口和流程联调。

## 项目结构

```text
.
├── run.py                    # 统一入口，根据 RUN_MODE 启动 server 或 gui
├── config.env.example         # 配置模板
├── pyproject.toml             # Python 版本与依赖声明
├── requirements.txt           # pip 依赖列表
├── docs/
│   └── websocket-api.md       # WebSocket 接口手册
├── data/
│   ├── actions_library.json   # 动作库
│   ├── tasks/                 # 已保存任务序列
│   └── skills/                # 技能库
└── src/
    ├── core/                  # 数据模型、配置加载、存储、启动器
    ├── gui/                   # PyQt6 主界面与执行线程
    ├── widgets/               # GUI 组件与 AI 助手组件
    ├── robot_server/          # WebSocket 服务、执行器、MiniCPM 代理
    ├── skill_system/          # 技能模型、注册表与匹配引擎
    ├── llm/                   # OpenAI / DeepSeek 等 LLM 客户端
    ├── arm_sdk/               # RM 机械臂控制封装
    ├── base_move/             # 底盘移动控制
    ├── devices/               # 串口设备、快换手、继电器、吸液枪等
    ├── cameras/               # RealSense / OpenCV 相机管理
    ├── vision/                # 视觉抓取、采集与检测逻辑
    └── actions/               # 可执行动作封装与轨迹文件
```

## 动作类型

当前核心动作类型定义在 `src/core/models.py`：

| 类型 | 含义 |
|---|---|
| `MOVE_TO_POINT` | 机械臂移动到点位 |
| `BASE_MOVE` | 底盘移动 |
| `ARM_ACTION` | 夹爪、吸液枪等执行器动作 |
| `INSPECT_AND_OUTPUT` | 检测与输出 |
| `WAIT` | 等待 |
| `CHANGE_GUN` | 取枪头 / 退枪头 |
| `VISION_CAPTURE` | 视觉采集 |
| `TRAJECTORY` | 轨迹执行 |

动作库保存在 `data/actions_library.json`，任务序列保存在 `data/tasks/*.task`。

## AI 与技能

技能系统位于 `src/skill_system/`。启动时会读取 `SKILL_LIBRARY_PATH` 指向的技能库；如果技能库不存在，则使用 `src/skill_system/default_skills.py` 中的默认技能。

AI 规划流程：

1. 客户端发送 `ai_chat`
2. 服务端匹配技能并生成动作序列预览
3. 客户端收到 `ai_preview_ready`
4. 用户确认后发送 `ai_confirm`
5. 服务端执行序列并推送执行事件

LLM 配置通过 `OPENAI_API_KEY`、`MODEL_PROVIDER`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL` 管理，支持 OpenAI 兼容接口。

## 相机与视觉

相机配置由 `CAMERA_PROVIDER` 决定：

- `realsense`：使用 Intel RealSense
- `webcam`：使用本地 USB / 内置摄像头
- `auto`：由相机工厂自动选择

视觉抓取流程中会用到：

- `YOLO_MODEL_PATH`
- `SAM_MODEL_PATH`
- `VISION_DEBUG_SAVE_DIR`
- `VISION_CAMERA_HOST`
- `VISION_CAMERA_PORT`

WebSocket 模式下可通过 `camera_status` 查询相机状态，通过 `subscribe_camera_frames` 订阅 JPEG Base64 帧。

## 硬件说明

硬件模式下，启动器会尝试初始化：

- Robot1 / Robot2 机械臂
- Modbus 升降平台
- PWM 颈部舵机
- 底盘移动控制器
- 相机管理器
- MiniCPM 代理配置

若只调试前端、接口或 AI 流程，请使用：

```bash
python run.py --simulation
```

模拟模式不会连接真实硬件，可避免机械臂、串口或相机不可用导致启动受阻。

## 开发建议

- 新增配置项：优先在 `config.env.example` 和 `src/core/config_loader.py` 中同步维护。
- 新增动作类型：更新 `ActionType`、动作参数 schema、GUI 表单和 WebSocket 执行器。
- 新增技能：维护 `data/skills/skill_library.json`，或扩展默认技能定义。
- 新增 WebSocket 接口：在 `src/robot_server/ws_server.py` 增加 action 处理，并同步更新 `docs/websocket-api.md`。
- 新增硬件动作：优先复用 `src/actions/`、`src/devices/` 和 `src/arm_sdk/` 中已有封装。

## 常见问题

### 启动时报机械臂或串口连接失败

确认是否处于真实硬件模式。没有硬件时使用：

```bash
python run.py --simulation
```

### WebSocket 连不上

确认 `RUN_MODE=server`，并检查 `WEBSOCKET_HOST`、`WEBSOCKET_PORT` 或命令行 `--host`、`--port`。

### AI 规划不可用

检查 `OPENAI_API_KEY`、`MODEL_PROVIDER`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 是否正确，并调用 `ai_status` 查看服务端状态。

### 相机没有画面

检查 `CAMERA_PROVIDER`、`REALSENSE_DEVICE_SN` 或 `WEBCAM_DEVICE_INDEXES`，并先调用 `camera_status` / `test_camera` 排查。

## 参考文档

- [WebSocket 接口手册](docs/websocket-api.md)
- [配置模板](config.env.example)

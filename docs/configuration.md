# 配置系统

项目使用 TOML 保存结构化、非敏感配置，使用 `.env` 或系统环境变量保存密钥和部署覆盖。
所有来源最终只生成一份不可变 `ApplicationSettings`，业务模块不得自行读取文件或进程环境。

## 文件

```text
config/config.example.toml  可提交的完整 TOML 模板
config/config.toml          本机主配置，版本库忽略
.env.example                可提交的敏感字段模板
.env                        本机密钥与覆盖，版本库忽略
```

初始化：

```powershell
Copy-Item config/config.example.toml config/config.toml
Copy-Item .env.example .env
```

也可以通过 `--config` 使用其他完整 TOML 文件：

```powershell
uv run robot-llm --config config/profiles/simulation.toml --check-config
```

## 来源优先级

从低到高依次为：

1. `src.configuration.settings` 中的类型化默认值；
2. TOML 配置；
3. `.env` 和系统环境变量，其中系统环境变量不会被 `.env` 覆盖；
4. 启动命令行参数。

环境变量名称保持大写形式，例如 `WEBSOCKET_PORT`、`GUI_THEME`、
`VOICE_INPUT_ENABLED`。只有 Settings schema 中声明的变量会被读取，其他环境变量不会进入配置。

## TOML 规则

- 根节点必须包含 `schema_version = 3`；旧版本不再兼容。
- 表名对应 `ApplicationSettings` 分组：`runtime`、`gui`、`logging`、`data`、
  `data_collection`、`localization`、`server`、`execution`、`llm`、`model_routing`、`robot`、
  `devices`、`vision` 和 `voice`。
- TOML 中的未知表和未知字段会使启动失败，避免拼写错误被静默忽略。
- 数字、布尔值和数组必须使用 TOML 原生类型，不能用字符串代替。
- `[secrets]` 表被明确禁止；密钥只能来自 `.env` 或系统环境变量。

示例：

```toml
schema_version = 3

[runtime]
simulation_mode = true

[model_routing.general_chat]
provider = "dashscope"
fallback_providers = []
output_mode = "text_then_tts"
speech_provider = "minicpm"
speech_fallback_providers = []

[robot]
robot_provider = "realman"
robot1_ip = "192.168.3.18"
robot1_port = 8080
robot1_initial_pose = [-0.04844, -0.269769, -0.101888, 3.109, -0.094, -1.592]

[server]
websocket_enabled = true
websocket_host = "127.0.0.1"
websocket_port = 8765
websocket_allowed_origins = []

[vision]
realsense_color_width = 1920
realsense_color_height = 1080

[[vision.cameras]]
name = "monitor1"
label = "左臂视觉相机"
provider = "realsense"
device_id = "419522071147"
roles = ["vision_capture", "robot_grasp"]
arms = ["left"]
capture_rotation_matrix = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
capture_translation_vector = [0.0, 0.0, 0.0]
capture_gripper_offset = [0.0, 0.0, 0.0]
camera_matrix = [1361.89, 0.0, 930.72, 0.0, 1361.32, 547.16, 0.0, 0.0, 1.0]
camera_matrix_resolution = [1920.0, 1080.0]
distortion_coefficients = [0.0, 0.0, 0.0, 0.0, 0.0]
# 完成真实手眼标定后再增加 "relocalization" role，并填写 4x4
# end_effector_to_camera。
```

`model_routing` 的子表名称对应稳定的 `TaskProfile.name`。每条路由分别配置推理 provider、推理降级顺序和输出策略：

- `text`：只输出文字，不调用语音模型；
- `native_audio`：推理 provider 必须支持 TTS，并在同一条流中直接输出语音；
- `text_then_tts`：先保留推理模型的流式文字，再把最终文本交给 `speech_provider` 合成语音。

`fallback_providers` 与 `speech_fallback_providers` 相互独立。修改 Prompt 或业务语义只改 `TaskProfile`；切换厂商、模型部署和语音链路只改 TOML。

## 相机目录

配置 schema v3 使用 `[[vision.cameras]]` 作为相机身份、用途和标定的唯一事实来源，
不再接受 `camera_provider`、逗号分隔的设备/名称字段、`vision_camera_name` 或
`vision_relocalization_left/right_*` 字段。每个 profile 包含：

- `name`：动作、工位数据和服务使用的稳定逻辑名；
- `label`：GUI 下拉列表显示名，留空时回退为 `name`；
- `provider`：当前支持 `realsense` 或 `opencv`；
- `device_id`：RealSense 序列号，或 OpenCV 数字设备索引（使用字符串书写）；
- `roles`：可包含 `vision_capture`（通用图像/视觉问答）、`robot_grasp`（带机器人标定的视觉抓取）、`balance`、`relocalization`；相同用途的第一个 profile 是默认相机；
- `arms`：可包含 `left`、`right`，用于为重定位等用途指定机械臂；
- `capture_rotation_matrix`、`capture_translation_vector`、`capture_gripper_offset`：
  视觉抓取管线使用的相机外参与末端姿态偏移；声明 `robot_grasp` 时三项必须完整配置；
- `camera_matrix`、`camera_matrix_resolution`、`distortion_coefficients`、
  `end_effector_to_camera`：视觉重定位使用的相机内参、畸变与手眼标定。

指定机械臂时，相机解析只允许该机械臂或未限定机械臂的通用 profile，不会回退到
另一侧机械臂。执行视觉重定位前会强制校验 `camera_matrix` 和
`end_effector_to_camera`，缺少真实标定时明确拒绝执行，不使用单位矩阵兜底。

当前统一相机运行时一次装配一个 provider，因此同一部署的所有 profile 必须使用相同
`provider`。界面列举相机只读取配置快照，不会为了打开下拉列表而连接硬件。
OpenCV provider 目前只提供彩色帧，不能声明需要深度帧的 `robot_grasp` role；配置加载时会明确拒绝这种组合。
真实设备模式不会为空目录隐式选择 RealSense；至少需要一个明确的相机 profile。

`.env` 仅保存敏感信息或临时部署覆盖：

```dotenv
OPENAI_API_KEY=""
DEEPSEEK_API_KEY=""
DASHSCOPE_API_KEY=""
WEBSOCKET_AUTH_TOKEN=""
```

## 校验与安全

```powershell
uv run robot-llm --check-config --simulation --disable-websocket
```

配置文件不存在时，未显式指定 `--config` 的启动可以使用类型化默认值；显式指定但不存在的文件会直接失败。
解析错误只报告文件名或字段名，不回显被拒绝的原始值。密钥、Token、私钥和凭据在诊断输出中统一脱敏。

新增配置字段时必须同步完成：

1. 在对应 Settings dataclass 中增加字段和唯一默认值；
2. 在 `config/config.example.toml` 中增加示例；
3. 如环境变量名称不能由字段名直接转为大写，在 `environment.py` 中声明映射；
4. 增加解析、覆盖优先级和非法输入测试；
5. 更新使用该字段的专题文档。

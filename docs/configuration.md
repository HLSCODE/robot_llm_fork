# 配置系统

项目使用 TOML 保存结构化、非敏感配置，使用 `.env` 或系统环境变量保存密钥和部署覆盖。
所有来源最终只生成一份不可变 `ApplicationSettings`，业务模块不得自行读取文件或进程环境。

## 文件

```text
config/config.example.toml      可提交的入口配置模板
config/fragments/*.example.toml 可提交的模块子配置模板
config/config.toml              本机入口配置，版本库忽略
config/fragments/*.toml         本机模块子配置，版本库忽略
.env.example                    可提交的敏感字段模板
.env                            本机密钥与覆盖，版本库忽略
```

初始化：

```bash
uv run robot-config-init
```

初始化命令会复制入口配置、所有子配置和 `.env`，采用增量方式执行：目标文件不存在时
创建，已经存在时跳过，绝不覆盖本机修改。可以安全重复执行。若从项目根目录之外调用：

```bash
robot-config-init --project-root /path/to/robot_llm_fork
```

## 交互式完整初始化

`robot-init` 使用 Textual 全屏模式提供跨平台终端向导，并随终端尺寸自动伸缩。
每次只显示当前问题，支持方向键移动、`Space` 勾选、`Enter` 进入下一步、`Esc`
返回以及 `Ctrl+C` 安全取消。执行阶段每个步骤只显示状态摘要，详情默认收起，使用
`Enter`/`→` 展开、`←` 收起、`C` 复制当前步骤的完整详情。模型下载默认不选中；选择后也会先检查本地缓存，
ASR、VAD、标点或 KWS 模型完整存在时直接跳过下载。
依赖同步时若检测到 uv 缓存与项目位于不同文件系统，初始化器会自动使用
`--link-mode=copy`，避免跨盘硬链接警告；显式设置 `UV_LINK_MODE` 时以用户配置为准。

```bash
uv run robot-init
```

自动化环境使用非交互模式：

```bash
uv run robot-init --non-interactive \
  --steps configuration,dependencies,asr_models,kws_model,validation \
  --extras gui,server,ai,voice,kws
```

依赖同步默认使用 `--frozen`；需要主动更新锁文件时可传入 `--no-frozen`。
`--dry-run` 只验证计划，不产生写入、下载或依赖变更。模型也可单独初始化：

```bash
uv run robot-models-init --asr --kws --check
```

运行时只读取不带 `.example` 的本机文件；修改模板不会直接改变当前运行配置。配置在进程
启动时加载，修改后需要重启应用。默认先查找当前工作目录的 `config/config.toml`，找不到时
回退到源码项目根目录，避免 IDE、快捷方式或服务管理器改变工作目录后静默丢失配置。

也可以通过 `--config` 使用其他入口 TOML 文件；其 include 相对该入口文件所在目录解析：

```powershell
uv run robot-llm --config config/profiles/simulation.toml --check-config
```

## 来源优先级

从低到高依次为：

1. `src.configuration.settings` 中的类型化默认值；
2. `include` 子配置，按声明顺序由前到后覆盖；
3. 入口 TOML 自身声明的字段；
4. `.env` 和系统环境变量，其中系统环境变量不会被 `.env` 覆盖；
5. 启动命令行参数。

环境变量名称保持大写形式，例如 `WEBSOCKET_PORT`、`GUI_THEME`、
`VOICE_INPUT_ENABLED`。只有 Settings schema 中声明的变量会被读取，其他环境变量不会进入配置。

## TOML 规则

- 入口文件必须包含 `schema_version = 5`；旧版本不再兼容。
- `include` 是可选的相对路径数组；不使用时可以继续在入口文件中声明全部配置。
- 子配置不能声明 `schema_version` 或再次使用 `include`，避免循环依赖和隐式加载图。
- 后加载的子配置覆盖先加载的同名字段，入口文件覆盖所有子配置；数组字段整体替换，不做隐式拼接。
- include 路径必须位于入口配置目录内，缺失、重复、绝对路径和目录越界都会使启动失败。
- 表名对应 `ApplicationSettings` 分组：`runtime`、`gui`、`logging`、`data`、
  `data_collection`、`localization`、`server`、`execution`、`llm`、`llm_providers`、
  `model_routing`、`robot`、
  `devices`、`vision` 和 `voice`。
- TOML 中的未知表和未知字段会使启动失败，避免拼写错误被静默忽略。
- 数字、布尔值和数组必须使用 TOML 原生类型，不能用字符串代替。
- `[secrets]` 表被明确禁止；密钥只能来自 `.env` 或系统环境变量。

示例：

```toml
schema_version = 5

include = [
  "fragments/application.toml",
  "fragments/services.toml",
  "fragments/ai.toml",
  "fragments/devices.toml",
  "fragments/voice.toml",
]

[runtime]
simulation_mode = true

[llm]
default_provider = "minicpm"
request_timeout_s = 60.0
fallback_providers = []

[llm_providers.dashscope]
kind = "openai_compatible"
enabled = true
model = "qwen-plus"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
credential_env = "DASHSCOPE_API_KEY"
output_modes = ["text"]

[llm_providers.minicpm]
kind = "minicpm_realtime"
enabled = true
model = "minicpm-o"
output_modes = ["text", "native_audio"]
gateway_host = "10.10.17.15"
gateway_port = 8006
ws_scheme = "wss"
realtime_path = "/v1/realtime"

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

`llm_providers.<id>` 定义可被路由引用的 Provider 实例。`id` 是部署内稳定名称，
`kind` 是代码中的适配器类型；因此可以配置多个使用同一
`openai_compatible` 适配器、但模型或地址不同的实例。`credential_env` 只保存环境变量名，
真实密钥仍位于 `.env`。禁用实例不会进入可用 Provider 集合。

`model_routing` 的子表名称对应稳定的 `TaskProfile.name`。每条路由分别配置推理 provider、推理降级顺序和输出策略：

- `text`：只输出文字，不调用语音模型；
- `native_audio`：推理 provider 必须支持 TTS，并在同一条流中直接输出语音；
- `text_then_tts`：先保留推理模型的流式文字，再把最终文本交给 `speech_provider` 合成语音。

`fallback_providers` 与 `speech_fallback_providers` 相互独立。修改 Prompt 或业务语义只改 `TaskProfile`；切换厂商、模型部署和语音链路只改 TOML。

## 相机目录

配置 schema v5 使用 `[[vision.cameras]]` 作为相机身份、用途和标定的唯一事实来源，
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

视觉调试图片与运行清单默认保存到 `data/vision/debug`。可通过
`vision_debug_save_dir` 覆盖该位置；相对路径以项目根目录为基准，运行产物会按视觉操作和
运行 ID 分目录，并由 `vision_debug_retention_days`、`vision_debug_max_runs` 自动清理。
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
2. 在对应的 `config/fragments/*.example.toml` 中增加示例；
3. 如环境变量名称不能由字段名直接转为大写，在 `environment.py` 中声明映射；
4. 增加解析、覆盖优先级和非法输入测试；
5. 更新使用该字段的专题文档。

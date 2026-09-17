# 配置系统

项目使用 TOML 保存结构化、非敏感配置，使用 `.env` 或系统环境变量保存密钥和部署覆盖。
所有来源最终只生成一份不可变 `ApplicationSettings`，业务模块不得自行读取文件或进程环境。

## 文件

```text
config/config.example.toml      可提交的入口配置模板
config/fragments/**/*.example.toml 可提交的模块子配置模板
config/config.toml              本机入口配置，版本库忽略
config/fragments/**/*.toml      本机模块子配置，版本库忽略
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
  --steps configuration,data_migration,dependencies,asr_models,kws_model,validation \
  --extras gui,server,ai,voice,kws
```

只初始化数据目录并迁移旧格式时执行：

```bash
uv run robot-init migrate-data
```

数据迁移只由 `robot-init` 执行，并为支持的原地迁移保留备份。主程序启动阶段只加载和
校验当前配置及数据；检测到旧格式时会拒绝启动或加载，并提示先运行上述命令，不会隐式写盘。

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

- 入口文件必须包含 `schema_version = 6`；旧版本不再兼容。
- `include` 是可选的相对路径数组；不使用时可以继续在入口文件中声明全部配置。
- 子配置不能声明 `schema_version` 或再次使用 `include`，避免循环依赖和隐式加载图。
- 后加载的子配置覆盖先加载的同名字段，入口文件覆盖所有子配置；数组字段整体替换，不做隐式拼接。
- include 路径必须位于入口配置目录内，缺失、重复、绝对路径和目录越界都会使启动失败。
- 表名对应 `ApplicationSettings` 分组：`runtime`、`gui`、`logging`、`data`、
  `data_collection`、`localization`、`server`、`execution`、`llm`、`llm_providers`、
  `model_routing`、`robot`、`robot_providers.<kind>`、
  `devices`、`vision` 和 `voice`。
- TOML 中的未知表和未知字段会使启动失败，避免拼写错误被静默忽略。
- 数字、布尔值和数组必须使用 TOML 原生类型，不能用字符串代替。
- `[secrets]` 表被明确禁止；密钥只能来自 `.env` 或系统环境变量。

示例：

```toml
schema_version = 6

include = [
  "fragments/application.toml",
  "fragments/services.toml",
  "fragments/ai.toml",
  "fragments/robot.toml",
  "fragments/robots/realman.toml",
  "fragments/robots/tianji.toml",
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
provider = "realman"
move_velocity = 10
move_radius = 0
move_connect = 0
move_block = 1

[robot_providers.realman]
kind = "realman"
model = "rm75-dual"
left_controller_ip = "192.168.3.18"
left_controller_port = 8080
left_initial_pose = [-0.04844, -0.269769, -0.101888, 3.109, -0.094, -1.592]

[mobile_base]
host = "192.168.3.216"
port = 12345
client_bind_port = 54321
timeout_seconds = 5.0

[server]
websocket_enabled = true
websocket_host = "127.0.0.1"
websocket_port = 8765
websocket_allowed_origins = []

[vision]
realsense_color_width = 1920
realsense_color_height = 1080
camera_probe_timeout_seconds = 2.5
camera_probe_max_attempts = 2
camera_idle_timeout_seconds = 10.0

[[vision.cameras]]
name = "monitor1"
label = "左臂视觉相机"
provider = "realsense"
device_id = "419522071147"
required = true
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

## 配置字段参考

以下字段均可放在入口 TOML 或被入口 include 的子配置中。表中的“默认”指未配置时的
`ApplicationSettings` 默认值；实际部署通常应从对应 `.example.toml` 开始。空字符串或空数组
通常表示“未显式设置”，并不总是等同于禁用，请结合每项说明判断。

### `[runtime]`、`[gui]` 与 `[logging]`

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `simulation_mode` | `false` | 使用模拟设备运行。开发、GUI 测试可开启；真实硬件部署必须关闭。 |
| `interaction_turn_timeout_s` | `90.0` 秒 | 一轮自然语言交互允许的总时长，超过后取消该轮处理。 |
| `command_preview_ttl_seconds` | `120.0` 秒 | AI/技能生成的待确认动作预览有效期；过期后需要重新生成。 |
| `command_arm_relative_step_mm` / `command_arm_relative_max_mm` | `10.0` / `100.0` mm | 相对机械臂控制的默认步长和单条指令允许的最大位移，用于限制遥操作与命令输入。 |
| `command_base_relative_step_cm` / `command_base_relative_max_cm` | `10.0` / `100.0` cm | 移动底盘相对控制的默认步长和单条指令最大距离。 |
| `theme` | `system` | GUI 主题，可选 `system`、`light`、`dark`；也可由 `GUI_THEME` 临时覆盖。 |
| `level` | `INFO` | 最低日志等级，使用 `DEBUG` 排查问题时会显著增加日志量。 |
| `directory` | `logs` | 日志根目录；相对路径以项目根目录解析。 |
| `retention_days` | `14` 天 | 自动保留日志天数；不用于删除用户数据或原生 crash dump。 |

### `[data]`、`[data_collection]` 与 `[localization]`

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `robot_data_dir` | `data` | 用户运行数据根目录。动作、工作流、草稿和轨迹默认在其 `profiles/<robot-profile-id>/` 下隔离。 |
| `actions_library_directory` | 空 | 覆盖当前 Profile 动作库目录；留空采用默认布局。 |
| `workflows_directory` / `workflow_drafts_directory` | 空 | 覆盖正式工作流或编辑草稿目录；相对路径以项目根目录解析。 |
| `skill_library_directory` | 空 | 覆盖共享技能目录；技能文件共享，但引用动作仍受 Profile 校验。 |
| `trajectories_directory` | 空 | 覆盖当前 Profile 的轨迹目录；不要与 SDK 临时原始录制目录混用。 |
| `fps` | `30` | 数据采集目标帧率。提高会增加 CPU、磁盘和同步压力。 |
| `camera_index` | `0` | 采集服务使用的 OpenCV 相机索引；不替代 `[[vision.cameras]]` 的视觉相机目录。 |
| `arm_ids` | `left`, `right` | 要同步记录的机械臂逻辑 ID。 |
| `save_path` | `data/demos` | 采集 episode 的输出根目录；与工作流/轨迹数据不同。 |
| `format_variant` | `portable_simplified` | 采集数据格式变体；切换前应确认下游训练或回放工具兼容。 |
| `minimum_free_bytes` | `1073741824` | 开始采集前所需的最低可用空间（字节）。 |
| `storage_overhead_factor` | `1.25` | 写入空间估算放大倍数，为索引、临时文件和编码开销预留余量。 |
| `stale_write_seconds` | `3600.0` 秒 | 超过该时长仍未完成的写入任务可识别为陈旧任务。 |
| `random_seed` | `42` | 可重复抽样、数据划分所用随机种子。 |
| `recording_stop_timeout_seconds` | `5.0` 秒 | 停止采集时等待后台写入线程退出的最长时间。 |
| `maximum_sync_skew_ms` | `100.0` ms | 同一采集样本不同来源允许的最大时间偏差。 |
| `camera_extrinsics` | 空 | 采集格式使用的相机外参；需与 `camera_extrinsics_reference_frame`、`calibration_id` 一起记录。 |
| `camera_extrinsics_reference_frame` | 空 | `camera_extrinsics` 的参考坐标系名称。 |
| `calibration_id` | 空 | 标定版本的稳定标识，便于追溯一批数据使用的标定。 |
| `external_localization_host` / `external_localization_port` | `0.0.0.0` / `22222` | UDP 外部定位监听地址和端口；`0.0.0.0` 表示全部网卡。 |
| `external_localization_receive_size_bytes` | `1024` | 单个 UDP 报文的接收缓冲区上限（字节）。 |
| `external_localization_socket_timeout_seconds` / `external_localization_join_timeout_seconds` | `0.2` / `1.0` 秒 | 接收循环轮询超时、停止时等待接收线程的时间。 |

### `[server]` 与 `[execution]`

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `websocket_enabled` | `true` | 是否随主应用启动 WebSocket 附加服务。只使用桌面端时可关闭。 |
| `websocket_security_enabled` | `false` | 启用 Token、Origin、TLS 等安全策略。监听非本机地址时应开启。 |
| `websocket_host` / `websocket_port` | `127.0.0.1` / `8765` | 监听地址与端口；对外暴露前应评估防火墙、TLS 与认证。 |
| `websocket_control_lease_seconds` | `30.0` 秒 | 远程控制租约有效期；客户端超时未续约会释放控制权。 |
| `websocket_max_message_size_bytes` | `1048576` | 单条入站消息大小限制（字节）。 |
| `websocket_max_requests_per_second` | `120` | 单连接请求速率上限。 |
| `websocket_max_concurrent_requests` / `websocket_max_queued_messages` | `16` / `16` | 单连接同时处理请求数和等待发送队列上限，防止慢客户端耗尽资源。 |
| `websocket_send_timeout_seconds` / `websocket_slow_send_threshold_seconds` | `2.0` / `0.5` 秒 | 单次发送超时以及记录慢发送的阈值。 |
| `websocket_allowed_origins` | 空 | 允许的浏览器 Origin 精确列表。启用安全策略的浏览器部署应显式填写。 |
| `websocket_tls_certificate_path` / `websocket_tls_private_key_path` | 空 | TLS 证书链和私钥路径；私钥文件不提交版本库。 |
| `websocket_reverse_proxy_mode` | `false` | 位于可信反向代理后时开启，要求代理正确传递客户端与 Origin 信息。 |
| `teleoperation_command_timeout_seconds` | `1.0` 秒 | 过期的遥操作命令不会继续发送到硬件。 |
| `auxiliary_service_start_timeout_seconds` / `auxiliary_service_stop_timeout_seconds` | `5.0` / `10.0` 秒 | WebSocket 等附加服务启动、关闭的最大等待时间。 |
| `execution_action_timeout_seconds` | `600.0` 秒 | 单个动作的统一执行超时；不应用它替代机械臂控制器自身的安全限位。 |
| `execution_arm_move_max_attempts` / `execution_arm_move_retry_delay_seconds` | `3` / `0.5` 秒 | 机械臂运动失败后的最多尝试次数和间隔。 |
| `execution_body_poll_interval_seconds` | `0.1` 秒 | 升降等身体轴的状态轮询间隔。 |
| `execution_gripper_max_attempts` / `execution_gripper_retry_delay_seconds` | `3` / `0.5` 秒 | 夹爪动作失败后的重试参数。 |
| `execution_trajectory_poll_interval_seconds` | `0.5` 秒 | 轨迹回放完成状态的轮询间隔。 |
| `safety_stop_wait_timeout_seconds` | `2.0` 秒 | 发出安全停止后等待执行线程确认退出的最长时间。 |

### `[llm]`、`[llm_providers.<id>]` 与 `[model_routing.<task>]`

`[llm]` 是全局默认与熔断策略，`[llm_providers.<id>]` 是可复用的部署实例，
`[model_routing.<task>]` 选择每一类任务实际使用的实例。Provider ID 是部署内稳定名称，
不是模型名；同一个 `kind` 可以配置多个地址、模型或凭据不同的实例。

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `llm.default_provider` | `openai` | 没有专属路由时使用的 Provider ID；必须指向已启用实例。 |
| `llm.default_temperature` | `0.3` | 没有专属策略时的生成随机性。 |
| `llm.default_max_tokens` | `512` | 默认最大输出 token 数。 |
| `llm.request_timeout_s` | `60.0` 秒 | 单次 LLM 请求超时。 |
| `llm.fallback_providers` | 空 | 全局降级 Provider ID 顺序；路由自身的 fallback 优先用于该任务。 |
| `llm.circuit_failure_threshold` / `_circuit_recovery_seconds` | `3` / `30.0` 秒 | 连续失败多少次后熔断，以及熔断后多久允许再次尝试。 |
| `llm_providers.<id>.kind` | 无 | 适配器类型，如 `openai_compatible` 或 `minicpm_realtime`。 |
| `.enabled` | `true` | 是否加入可用 Provider 集合；禁用实例不能被路由选择。 |
| `.model` | 空 | 发送给 Provider 的模型标识。 |
| `.base_url` | 空 | OpenAI-compatible API 基地址。 |
| `.credential_env` | 空 | 保存 API Key 的环境变量名，不能填写 Key 本身。 |
| `.output_modes` | `['text']` | 此实例能提供的输出模式，如 `text`、`native_audio`。 |
| `.gateway_host` / `.gateway_port` | 空 / `0` | Realtime gateway 地址与端口。 |
| `.ws_scheme` / `.gateway_path_prefix` / `.realtime_path` | `wss` / 空 / `/v1/realtime` | Realtime WebSocket 的协议、网关前缀和接口路径。 |
| `model_routing.<task>.provider` | 任务默认 | 该任务的首选 Provider ID。 |
| `.fallback_providers` | 空 | 该任务的推理降级顺序。 |
| `.output_mode` | `text` | `text`、`native_audio` 或 `text_then_tts`，必须被所选 Provider 支持。 |
| `.speech_provider` / `.speech_fallback_providers` | 空 | `text_then_tts` 模式下的语音实例及其降级顺序。 |

当前稳定任务路由表为：`instruction_classifier`、`general_chat`、`robot_command_planner`、
`vision_fusion`、`voice_feedback`、`repeat`、`balance_reading`。每个子表字段相同；缺省字段
使用 `ModelRoutingSettings` 对应任务的默认路由。修改任务名称需要同时修改代码中的 `TaskProfile`，
不是仅增加一个 TOML 子表即可生效。

### `[robot]` 与 `[robot_providers.*]`

`[robot]` 只保存 Provider 无关的选择和运动默认值；厂商专属连接、坐标系、自由度和工具参数
必须放到相应 Provider 表。`profile_id` 留空时按 provider 与 model 派生，用于隔离动作、工作流
和轨迹数据，不能通过切换 provider 复用另一种机械臂的数据。

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `provider` | `realman` | 当前机械臂 provider，如 `realman` 或 `tianji`；运行时只装配选中的实现。 |
| `profile_id` | 空 | 显式数据 Profile ID；仅在需要稳定自定义目录名时设置。 |
| `move_velocity` | `10` | Provider 无关的默认运动速度，具体单位和可接受范围由机械臂 adapter 定义。 |
| `move_radius` | `0` | 默认轨迹过渡/圆角参数；语义由机械臂 provider 定义。 |
| `move_connect` | `0` | 默认运动连接方式参数，传给支持它的 provider。 |
| `move_block` | `1` | 默认是否等待运动完成；动作可在 schema 允许时覆盖。 |
| `robot_providers.realman.kind` / `.model` | `realman` / `rm75-dual` | 适配器类型和 RealMan 型号，同时参与默认 Profile ID。 |
| `left_controller_ip` / `right_controller_ip` | `192.168.3.18` / `192.168.3.19` | 左右控制器地址。部署前应逐臂确认网络与控制器配置。 |
| `left_controller_port` / `right_controller_port` | `8080` / `8080` | 左右控制器端口。 |
| `left_initial_pose` / `right_initial_pose` | 空 | 启动或视觉流程使用的左右臂初始六维位姿；必须来自实际标定。 |
| `tool_rack_arm` | `right` | 执行工具架换装的机械臂。 |
| `tool_rack_slot_<1|2>_approach_pose` | 空 | 到达槽位前的安全接近位姿。 |
| `tool_rack_slot_<1|2>_attach_pose` / `_detach_pose` | 空 | 对应槽位的装入、卸下接触位姿；更改前需真实设备验收。 |
| `tool_rack_slot_<1|2>_attach_dwell_seconds` / `_detach_dwell_seconds` | `0.5` 秒 | 到达装入/卸下位姿后的停留时间，给机构动作留出稳定时间。 |
| `max_attempts` | `5` | RealMan 专属夹爪动作最大尝试次数。 |
| `gripper_pick_speed` / `gripper_pick_force` / `gripper_pick_timeout` | `200` / `1000` / `3` 秒 | 夹取速度、力度和超时；单位及安全范围遵循 RealMan SDK。 |
| `gripper_release_speed` / `gripper_release_timeout` | `100` / `3` 秒 | 释放速度和超时。 |
| `robot_providers.tianji.kind` / `.model` | `tianji` / `tianji-dual` | 天机适配器类型和型号，决定七轴能力与默认 Profile。 |
| `controller_ip` | `192.168.1.190` | 天机控制系统地址。 |
| `subscription_interval_seconds` | `0.01` 秒 | SDK 状态订阅周期；过小会增加通信与 CPU 压力。 |
| `left_base_transform` / `right_base_transform` | 内置 4×4 矩阵 | 左右机械臂基座相对世界坐标系的齐次变换。 |
| `left_tool_transform` / `right_tool_transform` | 内置 4×4 矩阵 | 左右工具相对末端坐标系的齐次变换。 |
| `joint_limits_rad` | 内置 7 组范围 | 七个关节的弧度上下限；必须与机械臂控制器和实际安全范围一致。 |

`left/right_initial_pose`、工具架位姿和天机变换矩阵均属于安全相关标定值。不要从其他设备、
其他 Profile 或未验证的录制文件直接复制。

### `[mobile_base]` 与 `[devices]`

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `host` / `port` | `192.168.1.216` / `12345` | 移动底盘 TCP 服务地址与端口。 |
| `client_bind_port` | `null` | 本地绑定端口；留空由系统选择。 |
| `timeout_seconds` | `5.0` 秒 | 移动底盘通信超时。 |
| `body_serial_port` / `body_baudrate` / `body_slave_id` / `body_timeout` | `/dev/ttyUSB1` / `115200` / `1` / `1` 秒 | 身体升降轴 Modbus 串口、波特率、从站地址与响应超时。 |
| `body_di_pan` | `false` | 是否使用身体轴 DI 输入判断到位。 |
| `kuaihuanshou_*` | `/dev/ttyUSB2`、`115200`、`3` 秒 | 快换手串口、波特率和超时。 |
| `adp_*` / `adp_max_retries` | `/dev/ttyUSB2`、`115200`、`5` 秒、`3` | ADP 移液设备连接与失败重试参数。 |
| `relay_*` | `/dev/ttyUSB0`、`38400`、`1` 秒 | 继电器串口、波特率和超时。 |
| `pwm_neck_serial_port` / `pwm_neck_baudrate` | `/dev/neck` / `9600` | 颈部 PWM 控制板连接参数。 |
| `pwm_neck_[h|v]_servo_id` | `0` / `1` | 水平、垂直舵机通道 ID。 |
| `pwm_neck_[h|v]_initial_pwm` | `1600` / `1600` | 启动默认脉宽。 |
| `pwm_neck_[h|v]_pwm_min` / `_pwm_max` | 各轴内置值 | 允许脉宽范围；错误设置会造成机械干涉风险。 |
| `pwm_neck_[h|v]_default_time` | `1500` / `2500` ms | 默认转动时长。 |
| `expression_display_enabled` / `expression_display_provider` | `false` / `t5l_dgusii` | 是否启用表情屏及其 provider。 |
| `expression_display_config` | 空 | 独立显示配置文件；留空使用本节内联显示字段。 |
| `expression_display_serial_port`、`_baudrate`、`_timeout`、`_write_timeout` | `COM4`、`115200`、`0.5`、`1.0` 秒 | 表情屏串口与读写超时。 |
| `expression_display_vp_addr` / `_sp_addr` | `0x5602` / `0x8000` | DGUS VP/SP 地址，使用十六进制字符串。 |
| `expression_display_start_value` / `_stop_value` / `_hide_value` | `0x0000` / `0x0001` / `0x0002` | 启动、停止、隐藏显示命令值。 |
| `expression_display_clear_before_switch` / `_switch_delay` | `stop` / `0.1` 秒 | 表情切换前的清理动作与等待时间。 |
| `expression_display_update_icon_range` | `true` | 切换时是否同步图标帧范围。 |
| `expression_display_expressions` | 内置映射 | `name:icon_id:start_frame:end_frame` 的逗号分隔映射。 |
| `expression_display_clear_vps` / `_test_interval` / `_tx_delay` | 空 / `1.5` / `0.05` 秒 | 额外清理 VP、测试轮换间隔和连续帧发送间隔。 |
| `tapping_serial_port` / `_baudrate` / `_timeout` | `/dev/ttyACM0` / `115200` / `0.5` 秒 | 敲击/加粉装置总线连接参数。 |
| `tapping_gripper_address` / `_lift_address` / `_rotation_address` | `9` / `7` / `6` | 加粉夹爪、升降、旋转执行器的总线地址。 |
| `tapping_lift_safe_position` / `_dispense_position` / `tapping_rotation_home_position` | `0` / `50000` / `0` | 升降安全位、加粉位和旋转原点，单位为设备步数。 |
| `powder_dispense_large_step` / `_medium_step` / `_small_step` / `_micro_step` | `20000` / `8000` / `2000` / `500` | 自适应加粉四档电机步数。 |
| `powder_dispense_large_step_threshold_mg` / `_medium_...` / `_small_...` | `25.0` / `10.0` / `3.0` mg | 根据剩余目标质量选择档位的阈值。 |

### `[vision]`（除相机目录外）

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `realsense_color_width` / `_height` | `640` / `480` | RealSense 彩色流分辨率。 |
| `realsense_depth_width` / `_height` | `640` / `480` | RealSense 深度流分辨率。 |
| `realsense_fps` | `0` | RealSense 帧率；`0` 交由设备/SDK 协商。 |
| `realsense_jpeg_quality` / `webcam_jpeg_quality` | `85` / `85` | 编码 JPEG 质量（0–100）；更高会提高带宽和 CPU 开销。 |
| `realsense_align_depth_to_color` | `true` | 是否把深度图对齐到彩色图；抓取需要深度时通常开启。 |
| `camera_encode_fps` | `5` | 对网络或视觉消费者编码帧的目标速率。 |
| `camera_probe_timeout_seconds` / `_max_attempts` / `_idle_timeout_seconds` | `2.5` 秒 / `2` / `10.0` 秒 | 相机健康检查单台超时、最大尝试次数（仅 1 或 2）和任务后的空闲关闭时间。 |
| `webcam_width` / `_height` / `_fps` | `640` / `480` / `30` | OpenCV 相机的彩色流参数。 |
| `vision_camera_host` / `_port` | `localhost` / `12345` | 独立视觉相机服务使用的地址和端口；不是相机 profile 的设备地址。 |
| `yolo_model_path` / `sam_model_path` | `models/best.pt` / `models/sam2.1_l.pt` | 检测、分割模型文件位置。 |
| `vision_schema_version` / `_model_version` / `_calibration_version` | `1` / `default-model-v1` / `default-calibration-v1` | 结果、模型和标定的追溯版本。 |
| `vision_debug_save_dir` | `data/vision/debug` | 视觉调试图片、运行清单根目录；相对路径以项目根目录解析。 |
| `vision_debug_retention_days` / `_max_runs` | `7` 天 / `100` | 调试产物保留时长和最大运行批次数。 |
| `balance_camera_wait_timeout_seconds` | `2.0` 秒 | 等待具有 `balance` role 相机的最长时间。 |
| `vision_default_confidence` / `_velocity` / `_gripper_length` | `0.7` / `15` / `150.0` mm | 视觉抓取动作创建时的默认置信度、速度与夹爪长度。 |
| `vision_default_workflow` | `bottle` | 视觉动作默认引用的工作流名称。 |
| `vision_prep_offset_x` / `vision_grasp_z` / `vision_bottle_target_offset_x` / `_y` | 内置值（米） | 瓶体流程的准备、抓取和目标平面偏移；属于现场标定参数。 |
| `vision_gmm_components` | `1` | 点云/目标聚类使用的 GMM 分量数。 |
| `vision_relocalization_stations_file` | `data/vision_stations/profiles.json` | 示教工位配置文件位置。 |
| `vision_relocalization_default_marker_width` / `_height` | `0.158` / `0.158` m | 新建重定位示教的默认 Marker 尺寸。 |
| `vision_relocalization_pose_rotation_type` / `_pose_angle_unit` | `rpy` / `rad` | 重定位输出位姿的旋转表示和角度单位。 |
| `vision_relocalization_mode` / `_planar_constraint` | `planar` / `none` | 重定位算法模式和平面约束策略。 |
| `vision_relocalization_save_debug_images` | `true` | 是否保留重定位调试图片。 |
| `initial_pose` / `left_initial_pose` / `right_initial_pose` | 空 | 视觉流程使用的通用、左臂、右臂初始六维位姿。 |
| `place_drop_height` | `0.06` m | 放置时释放位置的高度偏移。 |
| `place_above` / `place_pos2` / `place_transfer_pose` | 内置六维位姿 | 视觉流程固定的上方、放置、中转位姿；需现场标定。 |
| `max_attempts` | `5` | 视觉动作通用最大尝试次数。 |

### `[voice]`

| 字段 | 默认 | 功能与使用建议 |
|---|---:|---|
| `voice_session_timeout_s` / `voice_session_history_turns` | `30.0` 秒 / `6` | 唤醒会话的空闲超时和送给 LLM 的最大历史轮数。 |
| `voice_speech_startup_wait_timeout_s` | `30.0` 秒 | GUI 启动等待语音模型就绪的最长时间，超时后转后台加载。 |
| `voice_tts_enabled` | `false` | 是否请求 LLM/TTS 音频回复；不等同于打开麦克风。 |
| `voice_input_enabled` | `false` | 语音输入总开关；开启后装配音频输入、ASR、VAD 和唤醒词链路。 |
| `voice_audio_sample_rate` / `_channels` / `_block_ms` / `_queue_size` | `16000` / `1` / `100` ms / `300` | 输入音频格式、块大小和队列容量。 |
| `voice_audio_latency` / `_device` / `_show_status` | `high` / 空 / `false` | sounddevice 延迟策略、输入设备名和底层状态日志开关。 |
| `voice_vad_model` / `_vad_chunk_ms` | `fsmn-vad` / `200` ms | VAD 模型标识及送入模型的音频块时长。 |
| `voice_min_utterance_ms` / `_max_utterance_ms` / `_end_silence_ms` | `500` / `30000` / `800` ms | 一句话的最短、最长时长以及结束所需静音时长。 |
| `voice_speech_start_rms_threshold` / `_confirm_chunks` | `0.025` / `1` | 判定开始说话的音量阈值和连续确认块数。 |
| `voice_listening_timeout_s` / `_follow_up_listening_timeout_s` | `8.0` / `25.0` 秒 | 首轮与连续对话等待用户开口的时限。 |
| `voice_wake_cooldown_s` | `1.5` 秒 | 两次唤醒触发之间的冷却时间。 |
| `voice_wake_feedback_enabled` / `_feedback_text` | `true` / 内置文本 | 是否给出唤醒反馈及其默认文本。 |
| `voice_wake_welcome_enabled` / `_welcome_workflow` | `false` / 空 | 唤醒后是否执行欢迎工作流及其工作流名称；启用前应确认动作安全性。 |
| `voice_silence_rms_threshold` | `0.01` | 静音判定阈值，通常低于开始说话阈值。 |
| `voice_suppress_model_output` / `_show_asr_timing` | `true` / `false` | 是否抑制模型直接输出、是否显示 ASR 耗时诊断。 |
| `voice_asr_model` / `_asr_punc_model` / `_asr_device` / `_asr_batch_size_s` | `iic/SenseVoiceSmall` / `ct-punc` / 空 / `60` 秒 | ASR、标点模型、计算设备与最大动态批处理音频时长。 |
| `voice_wake_engine` | `sherpa` | 唤醒引擎，可选 `sherpa` 或 `openwakeword`。 |
| `voice_wake_auto_trigger` | `false` | 启动后自动触发一次唤醒，仅用于调试。 |
| `voice_kws_encoder` / `_decoder` / `_joiner` / `_tokens` | 空 | sherpa-onnx KWS 模型文件路径。 |
| `voice_kws_keywords_file` | `models/kws/keywords.txt` | KWS 关键词文件；词表可提交，模型权重由初始化准备。 |
| `voice_kws_provider` / `_threshold` / `_score` / `_num_threads` / `_max_active_paths` | `cpu` / `0.35` / `1.5` / `1` / `4` | KWS 推理后端、触发阈值、关键词分数、线程和搜索参数。 |
| `voice_openwakeword_model_paths` / `_threshold` | 空 / `0.6` | openWakeWord 模型路径（英文逗号分隔）和触发阈值。 |

### 密钥和环境变量

`.env` 或系统环境变量保存 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、
`WEBSOCKET_AUTH_TOKEN` 等敏感值；TOML 的 `credential_env` 仅引用变量名。所有 Settings 字段
也有对应的受控环境变量名称：未在 `environment.py` 显式映射的字段使用字段名的大写形式，
例如 `VOICE_INPUT_ENABLED`、`VISION_DEBUG_MAX_RUNS`。对数组和元组，请使用项目支持的环境变量
值格式；复杂嵌套表（相机目录、Provider catalog）应留在 TOML，不建议通过环境变量表达。

系统环境变量优先于 `.env`：`.env` 只会补充进程中尚未存在的变量。因此容器、服务管理器或
CI 注入的密钥无需写回 `.env`。不要把 `.env`、私钥、TLS 证书或包含真实网络地址和标定值的
现场配置提交到版本库。

## 相机目录

配置 schema v6 使用 `[[vision.cameras]]` 作为相机身份、用途和标定的唯一事实来源，
不再接受 `camera_provider`、逗号分隔的设备/名称字段、`vision_camera_name` 或
`vision_relocalization_left/right_*` 字段。每个 profile 包含：

- `name`：动作、工位数据和服务使用的稳定逻辑名；
- `label`：GUI 下拉列表显示名，留空时回退为 `name`；
- `provider`：当前支持 `realsense` 或 `opencv`；
- `device_id`：RealSense 序列号，或 OpenCV 数字设备索引（使用字符串书写）；
- `required`：是否为启动必需相机。必需相机检测失败会显示醒目警告；可选相机失败只记录
  离线状态，不阻止应用进入就绪状态；
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

真实设备启动后会在后台按配置顺序逐台执行轻量健康检查。未连接设备不重试；已连接但
启动或取帧失败时最多按 `camera_probe_max_attempts` 尝试两次，每次等待时间由
`camera_probe_timeout_seconds` 控制。检查过程只读取单帧，不做深度对齐、JPEG 编码或
多画面拼接，并在每台检查完成后释放管线。实际视觉任务只启动所需相机，任务结束后短期
复用，并在 `camera_idle_timeout_seconds` 的空闲期结束后关闭。GUI 中的“重新检测相机”
执行同一套后台检查并刷新设备状态。

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

`[robot]` 只选择当前实现并保存 Provider 无关的运动默认值；
`[robot_providers.realman]`、`[robot_providers.tianji]` 保存厂商专属型号、连接、
标定和工具参数。所有 Provider 配置同时加载，但运行时只初始化 `[robot].provider`
选中的实现，因此切换机械臂只需修改一个字段。旧的 `[robot_realman]`、
`[robot_tianji]` 表不再接受。

配置入口仍是唯一 include 根；`fragments/robot.toml` 和 `fragments/robots/*.toml`
均不得再次声明 include。初始化命令递归复制所有 `**/*.example.toml`，并保持原有
目录结构。

新增配置字段时必须同步完成：

1. 在对应 Settings dataclass 中增加字段和唯一默认值；
2. 在对应的 `config/fragments/*.example.toml` 中增加示例；
3. 如环境变量名称不能由字段名直接转为大写，在 `environment.py` 中声明映射；
4. 增加解析、覆盖优先级和非法输入测试；
5. 更新使用该字段的专题文档。

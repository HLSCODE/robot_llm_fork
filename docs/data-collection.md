# 数据采集架构与数据格式

> 文档状态：Active
>
> Schema：`robot-llm.data-collection.episode` v2
>
> 最近更新：2026-07-30

## 1. 当前能力

数据采集在共享遥操作会话中记录一台 RealSense 深度相机和一条或两条机械臂的
实际遥测。当前实现具备：

- 应用层 session/episode 状态机和统一资源租约；
- 单臂或双臂采集；
- 使用主机 monotonic clock 计算并限制相机、左臂、右臂样本的最大时间偏差；
- RGB、原始深度、深度比例、内参、畸变参数、设备时间戳、帧号和时间戳域；
- 实际关节位置、推导关节速度、关节电流、夹爪位置/力和末端六维力；
- 可选 `T_reference_camera` 相机外参及标定版本；
- portable NPZ 与 Native RLBench 两种显式格式；
- 容量预检、同目录 staged write、完整性校验、原子发布和残留恢复；
- 默认不反序列化 pickle 的离线校验，以及显式受信 Native smoke test。

系统不提供 schema v1、`DATA_COLLECTION_ARM` 或旧帧字段的兼容读取。需要保留的
历史数据应使用独立迁移工具转换，运行时代码不承担历史格式分支。

## 2. 模块职责

```text
WebSocket handler
       |
DataCollectionService                    application
  |        |                 |
  |   CameraSession       TeleoperationService
  |        |                 |
  +---- DemonstrationRecorder            sampling
            |          |
   DepthCameraSource  ArmTelemetryReader
            |
   DataCollectionEpisodeWriter           persistence
       |          |          |
     schema    validation    filesystem
```

| 模块 | 职责 |
|---|---|
| `src/application/data_collection.py` | 用例状态、资源所有权、错误和结果 |
| `src/data_collection/recorder.py` | 定时采样、跨设备偏差检查、内存帧缓冲 |
| `src/data_collection/episode_writer.py` | 帧校验、编码、manifest、事务写入 |
| `src/data_collection/schema.py` | schema、元数据和字段约束 |
| `src/data_collection/validation.py` | episode/dataset 校验和 CLI |
| `src/data_collection/config.py` | 数据采集强类型配置 |
| `src/devices/runtime/camera_models.py` | 厂商无关的深度相机帧 |
| `src/devices/runtime/arm_models.py` | 厂商无关的机械臂遥测 |

`DataCollectionService` 是 recorder、相机会话和共享遥操作会话的唯一应用层
所有者。WebSocket 只负责协议 DTO 映射。

## 3. 采样与同步语义

每次采样按以下顺序执行：

1. 从相机 source 取得最新的完整 RGB/depth frameset；
2. 依次查询配置中的每条机械臂；
3. 收集相机主机接收时间和机械臂采样时间；
4. 使用同一进程的 monotonic clock 计算 `max(timestamp) - min(timestamp)`；
5. 偏差超过 `DATA_COLLECTION_MAX_SYNC_SKEW_MS` 时丢弃整帧并增加
   `capture_error_count`。

该机制提供有界时间偏差，但不等于硬件触发同步。相机硬件时间戳和机械臂 SDK
没有共享时钟，因此训练或融合流程应同时参考：

- `*_hardware_timestamps_ms` 和 `hardware_timestamp_domain`：相机设备时钟；
- `*_received/sample_at_monotonic_ns`：同一采集进程内的同步依据；
- `*_utc_ns`：跨进程、日志和数据集定位时间。

双臂模式要求同一帧中两臂数据都有效；任一机械臂查询失败或超时均丢弃整帧，
不会写入不完整的双臂 observation。

采集要求 RealSense depth 已对齐到 color；未对齐帧没有可复用的单一内参语义，
因此会被拒绝，而不是把 color 内参错误地用于原始 depth 像素。

## 4. 机械臂遥测语义

`ArmTelemetryReader` 是采集使用的最小设备能力，不依赖 RealMan SDK 类型。
RealMan adapter 当前映射如下：

| 统一字段 | RealMan 来源 | 单位/说明 |
|---|---|---|
| `joint_positions` | 当前机械臂状态 | degree |
| `joint_velocities` | 相邻两次实际关节位置与 monotonic 时间差推导 | degree/s；第一帧可能无效 |
| `joint_currents` | `rm_get_current_joint_current` | SDK mA 转换为 A |
| `gripper_open` | `rm_get_gripper_state.actpos` | 0..1000 归一化为 0..1 |
| `gripper_force_newtons` | `current_force` | 克力转换为 N |
| `gripper_raw_position` | `actpos` | SDK 原始位置 |
| `gripper_pose` | 当前末端位姿 | metre + quaternion xyzw |
| `end_effector_wrench` | `rm_get_force_data.force_data` | Fx/Fy/Fz N，Mx/My/Mz N·m |

关节电流不是关节力矩，末端 wrench 也不是关节力。因此 Native RLBench 的
`Observation.joint_forces` 保持 `None`，不会用语义不一致的数据填充。真实扩展
字段保存在 portable 数组或 Native `Observation.misc` 中。

可选传感器字段允许部分帧缺失。portable 格式为每个可选数组保存对应的
`*_valid` 二值掩码，缺失行使用零占位；消费者必须先检查掩码。

## 5. 相机与标定语义

原始深度 PNG 保持相机 `uint16` 设备单位。转换到米：

```text
depth_metres = front_depth_uint16 * depth_scale_metres
```

如果配置相机外参，16 个数按行展开并表示：

```text
p_reference = T_reference_camera @ p_camera
```

必须同时提供 reference frame 和 calibration ID。外参缺失时仍允许采集，但
校验器输出 `camera_extrinsics_absent` 警告，明确表示数据不能直接投影到机器人
参考坐标系。代码不会从视觉重定位配置中猜测或复用语义不明确的矩阵。

## 6. 格式与目录

| 格式 | 低维数据 | pickle | 机械臂数量 |
|---|---|---:|---:|
| `portable_simplified` | `low_dim_obs.npz` | 否 | 1 或 2 |
| `rlbench_native` | `low_dim_obs.pkl` 等三个文件 | 是 | 必须为 1 |

```text
data/demos/<task>/all_variations/episodes/episode0/
├── episode.json
├── front_rgb/
│   ├── 0.png
│   └── ...
├── front_depth/
│   ├── 0.png
│   └── ...
└── low_dim_obs.npz
```

Native 格式将 NPZ 替换为：

```text
low_dim_obs.pkl
variation_number.pkl
variation_descriptions.pkl
```

Native 使用真实 RLBench `Demo`/`Observation` 类型，缺少 RLBench 依赖时 session
预检直接返回 `format_unavailable`，不会回退为 portable 或生成伪 pickle。

## 7. Schema v2

`episode.json` 的关键结构：

```json
{
  "schema_name": "robot-llm.data-collection.episode",
  "schema_version": 2,
  "format_variant": "portable_simplified",
  "format_version": 2,
  "task": "pick_bottle",
  "source_arms": ["left", "right"],
  "episode_id": 0,
  "frame_count": 300,
  "camera": {
    "name": "monitor1",
    "serial": "419522071147",
    "distortion_model": "brown_conrady",
    "hardware_timestamp_domain": "hardware_clock",
    "depth_aligned_to_color": true
  },
  "synchronization": {
    "maximum_skew_ms": 100.0,
    "observed_maximum_skew_ms": 12.4
  },
  "calibration": {
    "camera_extrinsics": [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1]
    ],
    "reference_frame": "robot_base",
    "calibration_id": "front-camera-2026-07"
  }
}
```

`fields` 为每个字段声明 `required`、`present` 或 `absent`；`units` 和
`dimensions` 使用完整字段名，例如 `left_joint_currents`。
`files` 记录每个 payload 的相对路径、角色、字节数和 SHA-256。

Portable 全局数组包括：

- `timestamps_utc_ns`、`camera_received_at_monotonic_ns`；
- `color_hardware_timestamps_ms`、`depth_hardware_timestamps_ms`；
- `color_frame_numbers`、`depth_frame_numbers`；
- `camera_intrinsics`、`camera_distortion_coefficients`；
- `depth_scale_metres`、`sample_sync_skew_ms`；
- 可选的 `camera_extrinsics`。

每条机械臂使用 `left_` 或 `right_` 前缀，包括采样时间、关节位置/速度/电流、
夹爪位置/力、末端位姿和末端 wrench。

## 8. 事务写入

保存 episode 时：

1. 校验所有帧、字段 shape、单位范围和同步上限；
2. 执行容量预检；
3. 在目标 `episodes` 目录创建 `.episodeN.tmp-<hex>`；
4. 写入并 `fsync` 每个 payload；
5. 生成包含哈希的 manifest；
6. 对 staged episode 运行完整性校验；
7. 使用同文件系统 `os.replace` 原子发布为 `episodeN`。

已存在的 `episodeN` 永不覆盖。session 预检只清理当前任务目录内、名称严格匹配
且超过 `DATA_COLLECTION_STALE_WRITE_SECONDS` 的临时目录。

## 9. 配置

```env
DATA_COLLECTION_FPS=30
DATA_COLLECTION_CAMERA_INDEX=0
DATA_COLLECTION_ARMS=left,right
DATA_COLLECTION_SAVE_PATH=data/demos
DATA_COLLECTION_FORMAT_VARIANT=portable_simplified
DATA_COLLECTION_MAX_SYNC_SKEW_MS=100
DATA_COLLECTION_CAMERA_EXTRINSICS=
DATA_COLLECTION_CAMERA_EXTRINSICS_REFERENCE_FRAME=
DATA_COLLECTION_CALIBRATION_ID=
DATA_COLLECTION_MIN_FREE_BYTES=1073741824
DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR=1.25
DATA_COLLECTION_STALE_WRITE_SECONDS=3600
DATA_COLLECTION_RANDOM_SEED=42
DATA_COLLECTION_STOP_TIMEOUT_SECONDS=5
```

`DATA_COLLECTION_ARMS` 只接受逗号分隔的 `left`/`right` 且不能重复。Native
格式必须只配置一条机械臂。外参是 4×4 齐次矩阵，按行填写 16 个有限数值。
这些值在应用启动边界统一解析为不可变 `DataCollectionSettings`，采集用例通过
显式注入获得配置，不会自行加载 `config.env` 或读取进程环境。

## 10. 校验和受信 Native smoke test

默认校验不会反序列化 pickle：

```powershell
robot-data-validate data/demos
robot-data-validate data/demos --task pick_bottle
robot-data-validate data/demos --json
```

在已安装 RLBench 且数据来源完全可信的隔离环境中，可执行：

```powershell
robot-data-validate data/demos --trusted-native
```

`--trusted-native` 会加载 pickle，检查 `Demo`/`Observation` 类型、帧数、
variation number 和 descriptions。pickle 可执行任意代码，禁止对下载、外部
提交或来源不明的数据使用此开关。

## 11. 已知限制

- 当前是主机 monotonic clock 上的有界软件同步，不是硬件触发同步；
- RealMan 关节速度由轮询样本推导，不是控制器原生速度流；
- 末端六维力只在对应机械臂型号/传感器支持时出现；
- 尚未采集点云、mask、触觉、任务低维状态和标注；
- Native smoke test 已具备工具和自动化类型测试，但仍需在实际安装 RLBench 的
  受信训练环境以及真实硬件数据上执行验收。

# 数据采集架构与数据格式

> 文档状态：Active
>
> Schema：`robot-llm.data-collection.episode` v1
>
> 最近更新：2026-07-30

## 1. 当前能力与边界

数据采集在遥操作期间同步记录机械臂状态、前置 RGB 图像和深度图像。当前实现
已经具备：

- 应用层 session/episode 状态机；
- 相机与遥操作资源的统一租约和清理；
- 显式、版本化的数据格式；
- 容量预检、临时目录写入、完整性校验和原子发布；
- 中断残留恢复、SHA-256 manifest 和离线验证 CLI；
- 可配置采集机械臂，不依赖 RealMan 原生 SDK 数据结构。

系统不再把“缺少 RLBench 依赖时生成的自定义 pickle”称为 RLBench 数据，也
不存在隐式格式回退。格式必须通过配置明确选择。

## 2. 架构职责

```text
WebSocket handler
       |
DataCollectionService                 application
  |        |             |
  |   CameraSession   TeleoperationService
  |        |             |
  +---- DemonstrationRecorder         infrastructure
                |
       DataCollectionEpisodeWriter
          |        |          |
       schema   validation   filesystem
```

| 模块 | 职责 |
|---|---|
| `src/application/data_collection.py` | 用例状态机、资源所有权、稳定错误和结果；不处理文件格式 |
| `src/data_collection/recorder.py` | 定时采样、帧缓存和调用持久化端口 |
| `src/data_collection/episode_writer.py` | 容量预检、帧校验、格式编码、manifest、原子发布和残留清理 |
| `src/data_collection/schema.py` | schema、格式枚举、强类型元数据和路径/字段约束 |
| `src/data_collection/validation.py` | episode/dataset 完整性验证及 CLI |
| `src/data_collection/config.py` | 数据采集强类型配置 |

`DataCollectionService` 是 recorder、相机会话、采集状态和共享遥操作会话的唯一
应用层所有者。WebSocket 只负责协议 DTO 与应用结果之间的映射。

## 3. 会话与 Episode 生命周期

状态主路径：

```text
idle
  -> starting_session -> session_ready
  -> starting_episode -> recording
  -> stopping_episode -> session_ready
  -> ending_session -> idle
```

协议 action：

| action | 作用 |
|---|---|
| `demo_session_start` | 获取相机资源、初始化机械臂查询能力、执行存储预检并创建会话 |
| `demo_record_start` | 共享遥操作控制会话并启动采集线程 |
| `demo_record_stop` | 停止采集，校验并原子发布一个 episode |
| `demo_session_end` | 结束会话并释放 recorder、遥操作和相机资源 |

业务保存失败后采集线程已经停止，状态返回 `session_ready`，调用方可以修复容量
或配置问题后重新采集。recorder 协议损坏或无法确定线程状态时进入 `faulted`，
必须结束会话进行统一清理。

## 4. 数据格式

### 4.1 格式选择

| 配置值 | 默认 | 低维数据 | pickle | 适用范围 |
|---|---:|---|---:|---|
| `portable_simplified` | 是 | `low_dim_obs.npz` | 否 | 本项目采集、检查、转换和后续训练预处理 |
| `rlbench_native` | 否 | `low_dim_obs.pkl` 等三个文件 | 是 | 已安装 RLBench、明确需要原生 `Demo`/`Observation` 序列化的受信环境 |

两种格式共同使用 PNG 图像、`episode.json` 和同一目录层级。一个 episode 只能
包含一种低维格式；混合文件会被验证器拒绝。

`portable_simplified` 是默认和推荐格式。NPZ 使用 `allow_pickle=False` 即可
读取，不依赖 RLBench。

`rlbench_native` 使用真实 RLBench Python 类型序列化。启动采集 session 时会
检查依赖，缺失时以 `format_unavailable` 失败，不会回退到 portable 格式。
它表示“原生对象序列化”，不表示数据等价于 RLBench 仿真环境的完整 observation：
当前真实硬件采集没有 mask、point cloud、任务低维状态、相机外参等仿真字段。
pickle 只应在可信数据和受控环境中反序列化。

### 4.2 目录结构

Portable：

```text
data/demos/
└─ pick_bottle/
   └─ all_variations/
      └─ episodes/
         └─ episode0/
            ├─ episode.json
            ├─ front_rgb/
            │  ├─ 0.png
            │  └─ ...
            ├─ front_depth/
            │  ├─ 0.png
            │  └─ ...
            └─ low_dim_obs.npz
```

Native RLBench serialization：

```text
episode0/
├─ episode.json
├─ front_rgb/
├─ front_depth/
├─ low_dim_obs.pkl
├─ variation_number.pkl
└─ variation_descriptions.pkl
```

任务名只能包含 ASCII 字母、数字、点、下划线和连字符，且必须以字母或数字开头。
绝对路径、`..` 和其他目录穿越形式会在创建任何 episode 前被拒绝。

### 4.3 `episode.json`

每个成功发布的 episode 都包含独立 manifest，主要字段如下：

```json
{
  "schema_name": "robot-llm.data-collection.episode",
  "schema_version": 1,
  "format_variant": "portable_simplified",
  "format_version": 1,
  "task": "pick_bottle",
  "source_arm": "left",
  "episode_id": 0,
  "variation_id": 0,
  "descriptions": ["pick bottle"],
  "frame_count": 300,
  "capture_error_count": 0,
  "created_at_utc": "2026-07-30T12:00:00+00:00",
  "fields": {
    "front_rgb": "required",
    "joint_velocities": "absent"
  },
  "units": {
    "joint_positions": "degrees",
    "gripper_pose": "xyz_metres_quaternion_xyzw"
  },
  "dimensions": {
    "front_rgb": [480, 640, 3],
    "front_depth": [480, 640]
  },
  "files": [
    {
      "path": "front_rgb/0.png",
      "role": "front_rgb",
      "size_bytes": 12345,
      "sha256": "..."
    }
  ]
}
```

manifest 记录每个 payload 文件的相对路径、角色、字节数和 SHA-256。未知
schema/format 版本会被显式拒绝；当前没有静默兼容或猜测迁移逻辑。将来修改
字段语义时必须提升版本，并提供独立迁移工具或对应版本 reader。

### 4.4 字段和单位

| 字段 | Portable 表示 | Native 表示 | 当前状态 |
|---|---|---|---|
| `timestamps` | Unix UTC 秒，`float64` | 不进入 RLBench observation | 已采集 |
| `front_rgb` | H×W×3 BGR `uint8` PNG | 同左 | 已采集 |
| `front_depth` | H×W 原始设备单位 `uint16` PNG | 同左 | 已采集 |
| `camera_intrinsics` | 3×3 像素内参 | `Observation.misc` | 已采集 |
| `joint_positions` | 角度，degree | 写入前转换为 radian | 已采集 |
| `gripper_pose` | 米 + `xyzw` 四元数 | 同左 | 已采集 |
| `gripper_open` | 0 关闭、1 打开 | 同左 | 当前状态端口未提供真实值，暂为 0 |
| `joint_velocities` | degree/s | 写入前转换为 radian/s | 当前缺失 |
| `joint_forces` | provider 原始单位 | provider 原始单位 | 当前缺失 |
| `gripper_matrix` | 米制 4×4 齐次矩阵 | 同左 | 当前缺失 |
| `gripper_joint_positions` | degree | 写入前转换为 radian | 当前缺失 |

写入前会拒绝空 episode、非有限数值、时间倒序、帧间 shape 不一致、不规范
RGB/depth dtype、无效相机内参、非归一化四元数和部分帧才出现的可选字段。

当前深度图仍是 RealSense 的原始 `uint16` 设备单位，manifest 不宣称其为毫米；
在用于三维训练前需要补充并应用相机 depth scale。当前也没有保存相机外参。

## 5. 事务写入、容量与恢复

保存一个 episode 时按以下顺序执行：

1. 校验任务、episode 编号、所有帧及字段一致性。
2. 根据原始数组大小和 overhead factor 估算空间。
3. 要求可用空间不少于“保留空间 + 本 episode 估算空间”。
4. 在同一 `episodes` 目录创建 `.episodeN.tmp-<hex>` 临时目录。
5. 对每个文件写入、flush 并 `fsync`。
6. 生成 manifest，并在临时目录上运行完整性验证。
7. 使用同文件系统的 `os.replace` 将临时目录发布为 `episodeN`。

已存在的 `episodeN` 永不覆盖。写入或验证失败时不会出现可见的半成品
episode，并会删除本次临时目录。

启动 session 时只清理当前任务 `episodes` 目录中、名称严格匹配且超过
`DATA_COLLECTION_STALE_WRITE_SECONDS` 的临时目录；不会递归扫描或删除其他
路径。未过期临时目录会保留并由验证器报告警告。

## 6. 配置

配置来自项目根目录 `config.env`：

```env
DATA_COLLECTION_FPS=30
DATA_COLLECTION_CAMERA_INDEX=0
DATA_COLLECTION_ARM=left
DATA_COLLECTION_SAVE_PATH=data/demos
DATA_COLLECTION_FORMAT_VARIANT=portable_simplified
DATA_COLLECTION_MIN_FREE_BYTES=1073741824
DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR=1.25
DATA_COLLECTION_STALE_WRITE_SECONDS=3600
DATA_COLLECTION_RANDOM_SEED=42
DATA_COLLECTION_STOP_TIMEOUT_SECONDS=5
```

| 配置 | 约束与说明 |
|---|---|
| `DATA_COLLECTION_FPS` | 1..240 Hz |
| `DATA_COLLECTION_CAMERA_INDEX` | 在线相机列表中的非负索引；超过列表长度时选择最后一台 |
| `DATA_COLLECTION_ARM` | `left` 或 `right` |
| `DATA_COLLECTION_SAVE_PATH` | 数据集根目录；空值和当前目录被拒绝 |
| `DATA_COLLECTION_FORMAT_VARIANT` | 只能是表中两个显式值 |
| `DATA_COLLECTION_MIN_FREE_BYTES` | 发布前必须保留的空间；默认 1 GiB |
| `DATA_COLLECTION_STORAGE_OVERHEAD_FACTOR` | 估算放大系数，必须不小于 1 |
| `DATA_COLLECTION_STALE_WRITE_SECONDS` | 临时目录被视为残留前的最小年龄 |
| `DATA_COLLECTION_RANDOM_SEED` | Native RLBench `Demo` 的 seed |
| `DATA_COLLECTION_STOP_TIMEOUT_SECONDS` | 等待采集线程退出的上限 |

## 7. 完整性验证

安装项目后：

```powershell
robot-data-validate data/demos
robot-data-validate data/demos --task pick_bottle
robot-data-validate data/demos --json
```

也可直接运行：

```powershell
python -m src.data_collection.validation data/demos
```

空数据集或通过 `--task` 指定不存在的任务也视为验证失败。退出码：

- `0`：所有 episode 通过；
- `1`：存在完整性错误。

验证内容包括 schema/format 版本、目录名与 episode ID、manifest 安全相对路径、
文件存在性、大小、SHA-256、未登记文件、PNG 解码和 shape、NPZ 必填数组、帧数、
时间戳及格式混用。Native pickle 默认不会反序列化，只验证 manifest 完整性并
输出 `native_pickle_not_inspected` 警告。

## 8. 稳定错误

应用层保存相关 `detail_code`：

| code | 含义 |
|---|---|
| `insufficient_storage` | 可用空间不足 |
| `episode_conflict` | 目标 episode 已存在 |
| `data_integrity_failed` | 输入帧或 staged episode 未通过校验 |
| `format_unavailable` | 所选格式的依赖不可用 |
| `persistence_failed` | 其他确定的持久化失败 |

其他生命周期错误包括 `invalid_state`、`session_start_failed`、
`episode_start_failed`、`episode_stop_failed`、`session_end_failed`、
`recorder_protocol_error` 和 `cleanup_failed`。

## 9. 已知限制与后续方向

- 一次 session 只采集配置指定的一条机械臂，尚不支持双臂严格时间同步。
- 机械臂查询端口尚未提供真实夹爪开合、关节速度、力矩和夹爪关节状态。
- 尚未记录 depth scale、相机外参、点云、mask 和硬件/标定版本。
- 相机帧与机械臂状态当前为采样时读取的最近值，没有硬件时间戳对齐。
- Native 格式的语义完整性需要在受信 RLBench 环境中另做训练读取 smoke test。
- 数据清洗、episode 回放、关键帧标注和训练集导出尚未实现。

这些限制不会通过伪造字段或隐式格式兼容掩盖；新增语义必须进入新 schema/
format 版本和对应验证规则。

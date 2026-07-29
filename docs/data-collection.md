# 数据采集功能文档

## 功能概述

数据采集功能用于在WebSocket遥操作框架下采集机械臂操作数据，并保存为RLBench标准格式。该功能支持通过WebSocket协议控制数据采集流程，自动记录遥操作过程中的相机图像和机械臂状态数据。

**核心特性**：
- **30Hz采集频率**：定时采集相机RGB/Depth图像和机械臂关节状态
- **自动编号**：Episode自动编号，跳过已存在的编号
- **RLBench标准格式**：保存为RLBench训练数据格式，便于后续强化学习训练
- **按任务分类**：不同任务的数据独立保存，便于数据管理

**适用场景**：
- 遥操作数据采集（通过VR/键盘控制机械臂）
- 强化学习训练数据准备
- 操作轨迹分析与回放

---

## WebSocket协议

数据采集功能通过WebSocket协议控制，支持以下4个action：

### 1. 开始采集会话

**请求格式**：
```json
{
  "action": "demo_session_start",
  "task": "pick_bottle",
  "description": "抓取瓶子放到桌上"
}
```

**参数说明**：
- `task`（必填）：任务名称，用于数据分类（如"pick_bottle"）
- `description`（必填）：任务描述，用于后续数据标注

**响应格式**：
```json
{
  "event": "demo_session_started",
  "task": "pick_bottle",
  "next_episode_id": 0,
  "message": "会话已启动，下一个episode编号为0"
}
```

**功能说明**：
- 初始化数据采集会话，指定任务名称和描述
- 自动计算下一个episode编号（跳过已存在的编号）
- 创建数据保存目录结构

---

### 2. 开始记录单条Episode

**请求格式**：
```json
{
  "action": "demo_record_start"
}
```

**响应格式**：
```json
{
  "event": "demo_record_started",
  "episode_id": 0,
  "message": "episode 0 开始记录"
}
```

**功能说明**：
- 自动启动遥操作模式（无需客户端单独发送`teleop_start`）
- 启动30Hz数据采集线程
- 实时记录相机帧（RGB/Depth）和机械臂状态（关节角度、末端位姿）
- 数据缓存到内存，等待保存

**重要提示**：服务端在收到此消息后会自动启动遥操作模式，客户端应立即开始发送关节指令流（50Hz）

---

### 3. 结束记录单条Episode

**请求格式**：
```json
{
  "action": "demo_record_stop"
}
```

**响应格式**：
```json
{
  "event": "demo_record_stopped",
  "episode_id": 0,
  "frames": 1500,
  "message": "episode 0 已保存，共1500帧"
}
```

**功能说明**：
- 停止30Hz采集线程
- 将采集的数据保存为RLBench格式（PNG文件+pkl文件）
- 自动递增episode编号

---

### 4. 结束采集会话

**请求格式**：
```json
{
  "action": "demo_session_end"
}
```

**响应格式**：
```json
{
  "event": "demo_session_ended",
  "message": "会话已结束（已自动停止遥操作模式）"
}
```

**功能说明**：
- 自动停止遥操作模式（无需客户端单独发送`teleop_stop`）
- 清空会话状态
- 释放数据采集器资源

**重要提示**：服务端在收到此消息后会自动停止遥操作模式，客户端应停止发送关节指令流

---

## 资源所有权

- `demo_session_start` 通过 `CameraAccessService.open_depth()` 取得独占相机会话，
  并在整个数据采集 session 期间持有；相机预览、语音视觉、相机测试和视觉动作
  会在资源冲突时被明确拒绝。
- `demo_record_start` 必须先取得 `TeleoperationService` 的机械臂会话租约，
  成功后才启动 30Hz recorder，避免采集线程已经运行但机器人控制权申请失败。
- `demo_session_end`、WebSocket 服务停止以及异常清理都会停止 recorder 和
  teleoperation，并在 `finally` 路径释放相机会话。
- 当前资源所有权已收敛，但 session/episode 状态仍位于
  `RobotWebSocketServer`；提取独立 `DataCollectionService` 是下一阶段工作。

---

### 错误响应

当请求失败时，返回错误消息：

```json
{
  "event": "demo_record_error",
  "message": "会话未启动，请先发送demo_session_start"
}
```

**常见错误场景**：
- 缺少必填参数（task、description）
- 会话未启动时尝试记录
- 机械臂未连接或相机数据获取失败

---

## 客户端实现说明

### 触发方式

数据采集通过客户端键盘事件触发，客户端需要监听键盘按键并映射到对应的WebSocket action。

**键盘按键映射表**：

| 键盘按键 | WebSocket Action | 功能说明 |
|---------|------------------|---------|
| `'s'` | `demo_session_start` | 进入采集会话（需要指定task和description） |
| `'r'` | `demo_record_start` | 开始记录单条episode（自动启动遥操作模式） |
| `'e'` | `demo_record_stop` | 结束记录并保存当前episode |
| `'q'` | `demo_session_end` | 退出整个采集会话 |

---

### 交互流程

客户端实现数据采集的完整交互流程如下：

1. **启动WebSocket连接**：客户端连接到WebSocket服务端
2. **进入采集会话**：按下`'s'`键，手动指定任务名称和描述，发送`demo_session_start`消息
3. **开始记录并自动启动遥操作**：按下`'r'`键，发送`demo_record_start`消息，服务端自动启动遥操作模式并开始30Hz数据采集
4. **执行遥操作**：客户端开始发送关节指令流，专注于数据采集
5. **结束记录**：按下`'e'`键，发送`demo_record_stop`消息，服务端保存数据并返回episode信息
6. **重复采集**：可重复步骤3-5采集多条数据（episode自动编号）
7. **退出会话**：采集完成后按下`'q'`键，发送`demo_session_end`消息，退出整个会话

---

### 实现要点

客户端实现数据采集需要注意以下几点：

#### 1. 键盘监听方式

键盘监听应采用非阻塞方式，避免影响遥操作指令的实时发送。推荐使用以下方式：

- **独立线程**：在独立线程中监听键盘事件，不影响主遥操作线程
- **事件回调**：使用事件回调机制，按键触发时发送WebSocket消息
- **异步处理**：键盘事件处理采用异步方式，避免阻塞遥操作指令流

---

#### 2. 消息发送与响应处理

客户端需要正确处理WebSocket消息的发送和响应：

- **消息发送**：按键触发时立即发送对应action消息
- **响应处理**：接收服务端响应消息，提取episode_id、frames等信息并显示给用户
- **状态显示**：实时显示当前采集状态（如"正在记录episode 0"、"已保存1500帧"）

---

#### 3. 错误处理

客户端需要处理可能的错误场景：

- **会话未启动**：按下`'r'`或`'e'`键时，如果会话未启动，服务端返回错误消息，客户端应提示用户先启动会话
- **采集失败**：如果保存失败，客户端应显示错误信息并提示用户重试
- **机械臂状态**：建议在采集前检查机械臂和相机状态，避免采集失败

---

#### 4. 并发处理

数据采集与遥操作并发进行，需要注意：

- **遥操作优先**：遥操作指令发送不应被键盘监听阻塞
- **独立通道**：数据采集消息与遥操作指令消息使用同一WebSocket连接，但处理逻辑独立
- **状态同步**：客户端需要维护当前采集状态（是否正在记录），避免重复触发

---

### 使用建议

客户端实现时建议遵循以下原则：

1. **按键提示**：在客户端界面显示按键映射（如"按s进入会话，按r开始记录，按e结束记录，按q退出会话"）
2. **状态反馈**：实时显示服务端响应信息（如episode编号、帧数、遥操作状态）
3. **错误提示**：错误消息应清晰提示用户问题原因和解决方法
4. **操作确认**：关键操作（如退出会话）可增加确认提示，避免误操作
5. **自动遥操作**：服务端在收到`demo_record_start`后会自动启动遥操作模式，客户端无需单独发送`teleop_start`

---

## 数据格式说明

### RLBench目录结构

采集的数据按任务分类保存，目录结构如下：

```
data/demos/
  ├── pick_bottle/                    # 任务名称（按任务分类）
  │   └── all_variations/
  │       └── episodes/
  │           ├── episode0/           # 自动编号（跳过已存在的）
  │           │   ├── front_rgb/      # RGB图像目录
  │           │   │   ├── 0.png       # 第0帧RGB图像
  │           │   │   ├── 1.png
  │           │   │   └── ...
  │           │   ├── front_depth/    # Depth图像目录
  │           │   │   ├── 0.png       # 第0帧Depth图像
  │           │   │   ├── 1.png
  │           │   │   └── ...
  │           │   ├── low_dim_obs.pkl         # 低维状态数据（Demo对象）
  │           │   ├── variation_number.pkl    # Variation编号
  │           │   └── variation_descriptions.pkl  # 任务描述
  │           ├── episode1/           # 下一条episode
  │           ├── episode2/
  │           └── ...
  ├── place_bottle/                   # 其他任务
  │   └── all_variations/
  │       └── episodes/
  │           └── ...
```

---

### 数据文件说明

#### 1. RGB图像（front_rgb/）

**格式**：PNG文件（`{frame_id}.png`）
**内容**：
- 前置相机采集的RGB图像
- 采集频率：30Hz
- 图像分辨率：640×480（默认）
- 颜色空间：BGR（OpenCV格式）

**用途**：
- 视觉观察数据
- 强化学习训练输入

---

#### 2. Depth图像（front_depth/）

**格式**：PNG文件（`{frame_id}.png`）
**内容**：
- 前置相机采集的Depth图像
- 采集频率：30Hz
- 数据格式：16位整数（单位：毫米）

**用途**：
- 深度信息
- 点云重建

---

#### 3. 低维状态数据（low_dim_obs.pkl）

**格式**：Python pickle文件
**内容**：
- `Demo`对象（包含Observation序列）
- 每个Observation包含机械臂状态数据

**Observation字段**（已实现）：
- `joint_positions`：7个关节角度（numpy数组）
- `gripper_open`：夹爪状态（0.0或1.0）
- `gripper_pose`：末端位姿（numpy数组）
- `misc`：相机参数（内参矩阵、外参矩阵）

**Observation字段**（待完善）：
- `joint_velocities`：关节速度
- `joint_forces`：关节力矩
- `gripper_matrix`：末端变换矩阵（4×4）
- `gripper_joint_positions`：夹爪关节角度

---

#### 4. 元数据文件

**variation_number.pkl**：
- Variation编号（默认为0）

**variation_descriptions.pkl**：
- 任务描述列表（从`description`参数解析）
- 支持多个描述（逗号分隔）

---

### 数据采集字段概览

| 数据类型 | 字段名称 | 采集来源 | 采集频率 |
|---------|---------|---------|---------|
| **视觉数据** | `front_rgb` | RealSense相机 | 30Hz |
| | `front_depth` | RealSense相机 | 30Hz |
| | `camera_intrinsics` | RealSense相机内参 | 首帧 |
| **机械臂状态** | `joint_positions` | 机械臂SDK（7个关节） | 30Hz |
| | `gripper_open` | 夹爪状态 | 30Hz |
| | `gripper_pose` | 末端位姿 | 30Hz |
| **待完善字段** | `joint_velocities` | 机械臂SDK | 待实现 |
| | `joint_forces` | 机械臂SDK | 待实现 |
| | `gripper_matrix` | 位姿计算 | 待实现 |

---

## 配置说明

数据采集功能通过`config.env`配置文件设置参数：

### 配置项说明

```env
# 数据采集频率（Hz）- 低于遥操作频率（50Hz）
DATA_COLLECTION_FPS=30

# 使用的相机索引（如果有多个相机）
DATA_COLLECTION_CAMERA_INDEX=0

# 数据保存路径
DATA_COLLECTION_SAVE_PATH=data/demos
```

---

### 参数详解

#### DATA_COLLECTION_FPS

**默认值**：30
**说明**：数据采集频率（Hz）
**建议值**：
- 30Hz：与相机帧率一致，数据完整
- 15Hz：降低数据量，适合长时间采集

**注意事项**：
- 采集频率必须低于遥操作频率（50Hz）
- 过高的频率可能导致数据丢失

---

#### DATA_COLLECTION_CAMERA_INDEX

**默认值**：0
**说明**：使用的相机索引
**适用场景**：
- 单相机：固定为0
- 多相机：选择指定的相机（如0、1、2...）

---

#### DATA_COLLECTION_SAVE_PATH

**默认值**：data/demos
**说明**：数据保存基础路径
**建议值**：
- `data/demos`：项目内路径（推荐）
- `/path/to/external/storage`：外部存储路径

**注意事项**：
- 路径需要有写入权限
- 建议使用绝对路径（避免路径混淆）

---

### 配置修改方法

1. 打开`config.env`文件
2. 修改对应配置项的值
3. 保存文件
4. 重启WebSocket服务（配置生效）

---

## 技术架构

### 核心模块

数据采集功能由以下模块组成：

#### 1. RLBenchRecorder（数据采集器）

**位置**：`src/data_collection/rlbench_recorder.py`

**核心功能**：
- 30Hz定时采集线程
- 实时获取相机帧和机械臂状态
- Episode编号管理（自动递增、跳过已存在）
- 数据缓存管理（内存缓存）

**数据采集流程**：
```
WebSocket请求 → RLBenchRecorder → 启动采集线程
                   ↓
             定时循环（30Hz）
                   ↓
            获取相机帧（RGB/Depth）
                   ↓
            获取机械臂状态（关节/位姿）
                   ↓
            缓存到内存（FrameData）
                   ↓
        WebSocket停止请求 → 停止采集线程
                   ↓
            调用RLBenchFormatter保存
```

---

#### 2. RLBenchFormatter（格式转换器）

**位置**：`src/data_collection/rlbench_formatter.py`

**核心功能**：
- RLBench格式保存（PNG分离）
- Observation对象构建
- 元数据文件生成

**保存流程**：
```
FrameData列表 → RLBenchFormatter
                   ↓
            创建episode目录结构
                   ↓
            保存RGB图像（PNG）
                   ↓
            保存Depth图像（PNG）
                   ↓
            构建Observation列表
                   ↓
            保存Demo对象（pkl）
                   ↓
            保存元数据（pkl）
```

---

#### 3. FrameData（数据容器）

**位置**：`src/data_collection/rlbench_recorder.py`

**核心字段**：
- `timestamp`：时间戳
- `front_rgb`：RGB图像（numpy数组）
- `front_depth`：Depth图像（numpy数组）
- `joint_positions`：关节角度（numpy数组）
- `gripper_pose`：末端位姿（numpy数组）

---

### 与现有框架的集成

数据采集功能集成在WebSocket服务端：

**集成点**：`src/robot_server/ws_server.py`

**新增内容**：
- 4个WebSocket handler（demo_session_start、demo_record_start、demo_record_stop、demo_session_end）
- 数据采集器初始化（延迟加载）
- 会话状态管理

**依赖关系**：
```
WebSocket服务端 → RobotController（机械臂状态）
                → RealSenseManager（相机数据）
                → RLBenchRecorder（数据采集）
                → RLBenchFormatter（数据保存）
```

---

## 已知限制

当前版本为简化实现，部分功能存在限制：

### 1. Observation字段简化

**已实现字段**：
- ✅ `joint_positions`：7个关节角度
- ✅ `gripper_open`：夹爪状态（简化版，固定0.0）
- ✅ `gripper_pose`：末端位姿（简化版，欧拉角表示）
- ✅ `misc`：相机参数（内参矩阵）

**待完善字段**：
- ⚠️ `joint_velocities`：关节速度（当前为None）
- ⚠️ `joint_forces`：关节力矩（当前为None）
- ⚠️ `gripper_matrix`：末端变换矩阵（当前为None）
- ⚠️ `gripper_joint_positions`：夹爪关节角度（当前为None）

**影响说明**：
- 当前简化版仍可用于基本数据采集
- 待完善字段不影响RLBench格式兼容性
- 后续可通过迭代完善字段内容

---

### 2. 末端位姿表示简化

**当前实现**：欧拉角表示（`[x, y, z, rx, ry, rz, 0.0]`）
**标准格式**：四元数表示（`[x, y, z, qx, qy, qz, qw]`）

**待完善**：实现欧拉角转四元数转换

---

### 3. 夹爪状态简化

**当前实现**：固定值（`0.0`）
**实际需求**：从SDK获取真实夹爪状态（`0.0`或`1.0`）

**待完善**：从机械臂SDK获取夹爪真实状态

---

### 4. 相机外参简化

**当前实现**：单位矩阵（`np.eye(4)`）
**实际需求**：从手眼标定获取真实外参矩阵

**待完善**：集成手眼标定参数（从`config.env`读取）

---

### 5. 数据同步精度

**当前实现**：宽松同步（±10ms误差）
**说明**：使用最近可用相机帧，不严格等待帧就绪

**影响说明**：
- 对30Hz采集频率影响较小
- 时间戳误差在可接受范围内

---

## 后续工作

以下功能计划在后续版本中实现：

### 1. 数据清洗工具

**目标功能**：
- 删除前N帧（去除启动等待帧）
- 删除后N帧（去除结束停留帧）
- 手动标记关键帧（关键动作点）
- 可视化界面（查看帧内容）

**实现位置**：`src/data_collection/data_cleaner.py`（待创建）

---

### 2. Observation字段完善

**待完善字段**：
- `joint_velocities`：从SDK获取关节速度
- `joint_forces`：从SDK获取关节力矩
- `gripper_matrix`：实现位姿转变换矩阵
- `gripper_joint_positions`：从SDK获取夹爪关节角度

**优先级**：高（影响数据完整性）

---

### 3. 位姿表示转换

**待实现**：
- 欧拉角转四元数算法
- 位姿转变换矩阵（4×4）
- 与RLBench标准格式完全兼容

---

### 4. 相机参数集成

**待实现**：
- 从手眼标定读取外参矩阵
- 相机参数动态更新
- 多相机支持（选择指定相机）

---

### 5. 可视化工具

**目标功能**：
- Episode回放界面
- 帧查看器（RGB/Depth显示）
- 关键帧标记界面

**实现位置**：独立可视化工具（待规划）

---

## 使用建议

### 数据采集建议

1. **采集频率**：建议使用30Hz（与相机帧率一致）
2. **Episode长度**：建议单条Episode控制在30-60秒（避免数据量过大）
3. **任务命名**：使用清晰的任务名称（如"pick_bottle"、"place_bottle"）
4. **描述格式**：提供明确的任务描述（便于后续标注）

---

### 数据管理建议

1. **定期备份**：采集的数据定期备份（避免数据丢失）
2. **分类保存**：按任务分类保存（便于后续训练）
3. **数据清洗**：采集后进行数据清洗（去除无用帧）
4. **版本管理**：对数据集进行版本管理（便于实验对比）

---

### 后续训练建议

1. **数据格式**：当前数据格式兼容RLBench训练框架
2. **字段完整性**：待完善字段不影响基本训练流程
3. **数据量**：建议每个任务采集10-20条Episode（便于训练）
4. **多样性**：采集不同场景的数据（提高训练鲁棒性）

---

## 参考文档

- [RLBench官方文档](https://github.com/stepjam/RLBench)
- [WebSocket API文档](./websocket-api.md)
- [遥操作控制文档](./teleop.md)
- [config.env配置说明](../config.env)

---

**文档版本**：v1.0
**最后更新**：2026-06-30
**维护者**：Robot LLM Project Team

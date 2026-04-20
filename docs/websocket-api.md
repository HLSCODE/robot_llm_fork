# WebSocket 接口手册

本文档对应当前 `main` 分支上的服务端实现，统一入口为 `python run.py`。

目标读者：

- 前端开发
- 联调测试人员
- 需要基于 WebSocket 协议接入本系统的其他客户端

本文档尽量从“接口手册”角度组织内容，不只列出接口名，而是完整说明：

- 服务如何启动
- 连接模型是什么
- 请求消息和事件消息如何组织
- 每个接口的用途、参数、返回值、错误场景
- 前端页面应该按什么顺序接入

---

## 1. 服务总览

当前服务将以下能力统一收敛到一条主 WebSocket 连接中：

- 机器人执行控制
- 动作库管理
- 序列编排
- 任务保存与加载
- AI 规划
- 设备状态管理
- RealSense 相机状态查询与帧订阅
- MiniCPM 聊天代理

主连接地址：

- `ws://{host}:{port}/`

默认监听配置：

- `host = 0.0.0.0`
- `port = 8765`

协议基本约定：

- 客户端发给服务端的 JSON 必须包含 `action`
- 服务端返回给客户端的 JSON 必须包含 `event`
- 同一条 WebSocket 连接中，既会收到“接口直接响应”，也会收到“后台异步推送”

你可以把它理解为：

- `action`：客户端发起的命令
- `event`：服务端反馈的结果或状态变化

---

## 2. 启动与配置

### 2.1 安装依赖

```bash
pip install -r requirements.txt
```

### 2.2 准备配置文件

仓库当前不再提交 `config.env`，请从示例复制：

```bash
cp config.env.example config.env
```

也可以完全不创建 `config.env`，改用环境变量覆盖。

### 2.3 启动命令

模拟模式：

```bash
python run.py --simulation
```

显式指定 server 模式：

```bash
RUN_MODE=server python run.py --simulation
```

Windows PowerShell：

```powershell
$env:RUN_MODE='server'
.\.venv\Scripts\python.exe run.py --simulation
```

连接真实硬件：

```bash
python run.py
```

指定端口：

```bash
python run.py --port 9000
```

### 2.4 推荐启动方式

如果你只是做前端联调，推荐使用：

```bash
python run.py --simulation
```

原因：

- 不依赖真实机械臂
- 不依赖串口设备
- 适合先打通页面和协议

### 2.5 关键配置项

与接口能力直接相关的配置项如下：

```env
RUN_MODE=server
SIMULATION_MODE=false

WEBSOCKET_HOST=0.0.0.0
WEBSOCKET_PORT=8765

REALSENSE_DEVICE_SN=153122077516
REALSENSE_DEVICE_NAMES=

MINICPM_GATEWAY_HOST=10.10.17.13
MINICPM_GATEWAY_PORT=8006
MINICPM_GATEWAY_SCHEME=https

MINICPM_ASK_ENABLED=true
MINICPM_ASK_API_KEY=
MINICPM_ASK_BASE_URL=
MINICPM_ASK_MODEL=qwen-turbo
```

配置项说明：

| 配置项 | 含义 | 备注 |
|---|---|---|
| `RUN_MODE` | 运行模式 | `server` 或 `gui` |
| `SIMULATION_MODE` | 是否模拟模式 | `true` 时不连接真实硬件 |
| `WEBSOCKET_HOST` | WebSocket 监听地址 | 默认 `0.0.0.0` |
| `WEBSOCKET_PORT` | WebSocket 监听端口 | 默认 `8765` |
| `REALSENSE_DEVICE_SN` | RealSense 序列号 | 支持逗号分隔多台 |
| `REALSENSE_DEVICE_NAMES` | RealSense 名称 | 与序列号一一对应 |
| `MINICPM_GATEWAY_HOST` | MiniCPM 网关主机 | 聊天代理使用 |
| `MINICPM_GATEWAY_PORT` | MiniCPM 网关端口 | 聊天代理使用 |
| `MINICPM_GATEWAY_SCHEME` | 网关协议 | `http` 或 `https` |
| `MINICPM_ASK_ENABLED` | 是否启用指令分类 | 仅影响是否触发 `minicpm_instruction` / AI 规划，不影响 MiniCPM 聊天回复 |
| `MINICPM_ASK_API_KEY` | 指令分类模型的 API Key | 留空时回退到 `OPENAI_API_KEY`；若两者都为空，则跳过分类，不自动规划 |
| `MINICPM_ASK_BASE_URL` | 指令分类模型 Base URL | 留空时回退到 `OPENAI_BASE_URL` |
| `MINICPM_ASK_MODEL` | 指令分类模型名 | 如 `qwen-turbo` |

---

## 3. 连接模型与消息规范

### 3.1 最小连接示例

```javascript
const ws = new WebSocket("ws://localhost:8765");

ws.onopen = () => {
  console.log("连接成功");
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("收到消息", data);
};

ws.onclose = () => {
  console.log("连接关闭");
};

ws.onerror = (err) => {
  console.error("连接异常", err);
};
```

### 3.2 请求消息格式

客户端请求统一为 JSON 对象：

```json
{
  "action": "status"
}
```

通用规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 接口动作名 |
| 其他字段 | 任意 | 否 | 由具体接口决定 |

### 3.3 响应/事件消息格式

服务端消息统一为 JSON 对象：

```json
{
  "event": "status"
}
```

通用规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `event` | `string` | 是 | 事件名 |
| 其他字段 | 任意 | 否 | 由具体事件决定 |

### 3.4 前端一定要注意的协议特点

这套协议不是严格的一问一答 RPC，而是“命令 + 事件流”模型。

比如：

- 你发 `execute`
- 先收到 `accepted`
- 后续再收到 `step_started`
- 然后收到 `step_completed`
- 最终收到 `execution_finished`

因此前端不要写成“发一次请求，只等一个返回”的模式。

推荐在前端统一做事件分发：

```javascript
const handlers = {
  status(data) {
    console.log("状态", data);
  },
  log(data) {
    // level: "info" | "warn" | "error"
    const fn = data.level === "error" ? console.error
             : data.level === "warn"  ? console.warn
             : console.log;
    fn(`[${data.level}] ${data.message}`);
  },
  error(data) {
    // 请求参数校验错误
    console.error("请求错误", data.message);
  },
  step_started(data) {
    console.log("步骤开始", data);
  },
  step_completed(data) {
    console.log("步骤完成", data);
  }
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  const fn = handlers[data.event];
  if (fn) fn(data);
};
```

---

## 4. action 总表

### 4.1 执行控制

| action | 含义 |
|---|---|
| `execute` | 执行动作序列 |
| `execute_task` | 直接执行已保存任务 |
| `stop` | 停止当前执行 |
| `pause` | 暂停当前执行 |
| `resume` | 恢复当前执行 |

### 4.2 动作库管理

| action | 含义 |
|---|---|
| `list_actions` | 获取动作库 |
| `get_action_schema` | 获取动作参数结构定义 |
| `create_action` | 创建动作 |
| `delete_action` | 删除动作 |
| `update_action` | 更新动作 |

### 4.3 序列编排

| action | 含义 |
|---|---|
| `get_sequence` | 获取当前编排序列 |
| `add_to_sequence` | 向序列追加动作 |
| `remove_from_sequence` | 删除序列中的某一步 |
| `move_in_sequence` | 调整序列顺序 |
| `clear_sequence` | 清空序列 |

### 4.4 任务管理

| action | 含义 |
|---|---|
| `list_tasks` | 获取任务列表 |
| `save_task` | 保存当前序列为任务 |
| `load_task` | 加载任务到当前序列 |
| `delete_task` | 删除任务文件 |
| `get_task_detail` | 读取任务文件内容但不影响当前序列 |
| `rename_task` | 重命名任务文件 |
| `add_to_task` | 直接向任务文件中新增动作 |
| `remove_from_task` | 直接删除任务文件中的动作 |
| `move_in_task` | 直接调整任务文件内部顺序 |

### 4.5 AI 规划

| action | 含义 |
|---|---|
| `ai_chat` | 发送自然语言，触发 AI 规划 |
| `ai_confirm` | 确认执行 AI 规划结果 |
| `ai_cancel` | 取消 AI 规划结果 |
| `ai_status` | 查询 AI 状态 |
| `list_skills` | 获取技能列表 |

### 4.6 设备与相机

| action | 含义 |
|---|---|
| `status` | 查询全局状态 |
| `init_robots` | 初始化机械臂 |
| `init_body` | 初始化升降平台 |
| `disconnect` | 断开所有硬件 |
| `test_camera` | 测试相机 |
| `camera_status` | 查询相机状态 |
| `subscribe_camera_frames` | 订阅相机帧 |
| `unsubscribe_camera_frames` | 取消订阅相机帧 |

### 4.7 MiniCPM 代理

| action | 含义 |
|---|---|
| `minicpm_status` | 查询 MiniCPM 代理状态 |
| `chat_connect` | 建立聊天会话 |
| `chat` | 发送聊天消息 |
| `chat_disconnect` | 断开聊天会话 |

---

## 5. event 总表

### 5.1 通用事件

| event | 含义 |
|---|---|
| `error` | 请求参数校验错误（同步返回给请求方） |
| `log` | 执行日志（含 `level` 字段） |

`log` 事件结构：

```json
{
  "event": "log",
  "level": "info",
  "message": "..."
}
```

`level` 取值：

| level | 含义 | 前端建议样式 |
|---|---|---|
| `info` | 常规执行日志（默认） | 默认色 |
| `warn` | 可恢复异常，如重试中 | 橙色 |
| `error` | 执行失败或硬件异常 | 红色加粗 |

注意：`error` **事件**（`event: "error"`）与 `log` 事件中 `level: "error"` 的区别：

- `event: "error"` — 针对当前请求的同步校验错误，如"动作 id 不能为空"
- `event: "log", level: "error"` — 执行过程中发生的运行时错误，如"机械臂控制器未初始化"

### 5.2 执行事件

| event | 含义 |
|---|---|
| `accepted` | 服务端已接受执行请求 |
| `step_started` | 某一步开始执行 |
| `step_completed` | 某一步执行完成 |
| `step_failed` | 某一步执行失败 |
| `execution_finished` | 整个执行结束 |
| `stopped` | 已发送停止指令 |
| `paused` | 已暂停 |
| `resumed` | 已恢复 |

### 5.3 动作库与序列事件

| event | 含义 |
|---|---|
| `actions_list` | 动作库返回 |
| `action_schema` | 动作参数 schema 返回 |
| `action_created` | 动作已创建 |
| `action_updated` | 动作已更新 |
| `action_deleted` | 动作已删除 |
| `sequence` | 当前序列返回 |
| `sequence_updated` | 当前序列更新 |

### 5.4 任务事件

| event | 含义 |
|---|---|
| `tasks_list` | 任务列表返回 |
| `task_saved` | 任务保存成功 |
| `task_loaded` | 任务加载成功 |
| `task_deleted` | 任务删除成功 |
| `task_detail` | 任务文件详情返回 |
| `task_updated` | 任务文件内容已更新 |
| `task_renamed` | 任务文件已重命名 |

### 5.5 AI 事件

| event | 含义 |
|---|---|
| `ai_status` | AI 当前状态 |
| `ai_status_changed` | AI 状态变化 |
| `ai_skill_matched` | 匹配到技能 |
| `ai_skill_not_matched` | 未能匹配到可执行技能 |
| `ai_preview_ready` | AI 已生成可执行预览 |
| `ai_execution_finished` | AI 执行相关流程结束 |
| `ai_cancelled` | AI 规划已取消 |
| `skills_list` | 技能列表 |

### 5.6 设备与相机事件

| event | 含义 |
|---|---|
| `status` | 全局状态返回 |
| `device_status_changed` | 设备状态变化 |
| `camera_test_result` | 相机测试结果 |
| `camera_status` | 相机状态返回 |
| `camera_subscribed` | 已订阅相机帧 |
| `camera_unsubscribed` | 已取消订阅相机帧 |
| `camera_frames` | 相机帧推送 |
| `camera_error` | 相机错误 |

### 5.7 MiniCPM 事件

| event | 含义 |
|---|---|
| `minicpm_status` | MiniCPM 代理状态 |
| `chat_connected` | 聊天会话已建立 |
| `chat_disconnected` | 聊天会话已关闭 |
| `chat_data` | MiniCPM 聊天响应（每条上游帧推送一次，服务端已做规范化） |
| `minicpm_instruction` | 检测到机器人可执行指令 |

`chat_data` 事件结构：

```json
{
  "event": "chat_data",
  "type": "chunk",
  "text_delta": "你好",
  "audio_data": "<base64,24kHz>",
  "packet": {
    "type": "chunk",
    "text_delta": "你好",
    "audio_data": "<base64,24kHz>"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | `string` | 规范化后的包类型，如 `prefill_done` / `chunk` / `done` / `error` / `unknown` |
| `text_delta` | `string` | 流式增量文本，仅 `chunk` 时出现 |
| `audio_data` | `string \| null` | Base64 编码的音频片段或完整音频，仅语音输出时可能出现 |
| `packet` | `object \| array \| string \| number \| boolean \| null` | 上游 MiniCPM 网关返回的完整已解析 JSON 包，前端如需兼容新增字段，优先从这里读取 |
| `raw` | `string` | 可选调试字段，仅在 `error` / `unknown` / 非 JSON 兜底场景返回；正常业务逻辑不要依赖它 |

注意：每条上游帧对应一次 `chat_data` 推送，流式响应时会收到多条。正常业务应优先消费顶层稳定字段，`packet` 作为完整透传补充，`raw` 只用于联调排查。

---

## 6. 状态与设备管理接口

### 6.1 查询服务状态 `status`

用途：

- 页面初始化时探测服务是否正常
- 获取执行状态
- 获取设备连接状态
- 获取相机和 MiniCPM 可用性

请求：

```json
{
  "action": "status"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `status` |

成功响应示例：

```json
{
  "event": "status",
  "devices": {
    "robot1": false,
    "robot2": false,
    "body": false
  },
  "executor": {
    "running": false,
    "paused": false
  },
  "sequence_length": 0,
  "ai_processing": false,
  "camera": {
    "available": false,
    "camera_count": 0,
    "cameras": []
  },
  "minicpm": {
    "configured": true,
    "gateway": "https://10.10.17.13:8006"
  }
}
```

响应字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `devices.robot1` | `boolean` | 左机械臂是否已连接 |
| `devices.robot2` | `boolean` | 右机械臂是否已连接 |
| `devices.body` | `boolean` | 升降平台是否已连接 |
| `executor.running` | `boolean` | 是否正在执行 |
| `executor.paused` | `boolean` | 是否处于暂停状态 |
| `sequence_length` | `number` | 当前服务端维护的序列长度 |
| `ai_processing` | `boolean` | AI 是否正在处理中 |
| `camera.available` | `boolean` | 是否有可用相机 |
| `camera.camera_count` | `number` | 在线相机数量 |
| `camera.cameras` | `array` | 相机状态列表 |
| `minicpm.configured` | `boolean` | 是否已配置 MiniCPM |
| `minicpm.gateway` | `string \| null` | MiniCPM 网关地址 |

### 6.2 初始化机械臂 `init_robots`

用途：

- 按需初始化机械臂
- 服务启动后再连接硬件

请求：

```json
{
  "action": "init_robots"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `init_robots` |

行为说明：

- 该接口是异步的
- 服务端通常会先发一条 `log`
- 初始化成功后会推送 `device_status_changed`
- 初始化失败会推送 `error`

可能的推送：

```json
{
  "event": "log",
  "level": "info",
  "message": "开始初始化机械臂..."
}
```

```json
{
  "event": "device_status_changed",
  "devices": {
    "robot1": true,
    "robot2": true,
    "body": false
  }
}
```

失败示例：

```json
{
  "event": "error",
  "message": "机械臂模块导入失败: RobotController SDK unavailable"
}
```

### 6.3 初始化升降平台 `init_body`

请求：

```json
{
  "action": "init_body"
}
```

成功时可能收到：

```json
{
  "event": "log",
  "level": "info",
  "message": "身体控制器初始化成功"
}
```

随后：

```json
{
  "event": "device_status_changed",
  "devices": {
    "robot1": false,
    "robot2": false,
    "body": true
  }
}
```

### 6.4 断开所有硬件 `disconnect`

请求：

```json
{
  "action": "disconnect"
}
```

成功响应示例：

```json
{
  "event": "disconnected",
  "messages": [
    "已停止当前执行",
    "机械臂已断开",
    "身体控制器已断开"
  ],
  "devices": {
    "robot1": false,
    "robot2": false,
    "body": false
  }
}
```

### 6.5 测试相机 `test_camera`

请求：

```json
{
  "action": "test_camera"
}
```

过程：

1. 先收到 `log`（`level: "info"`）
2. 再收到 `camera_test_result`

成功示例：

```json
{
  "event": "camera_test_result",
  "success": true,
  "message": "相机测试成功: color=640x480 depth=0.532m (SN=153122077516)"
}
```

失败示例：

```json
{
  "event": "camera_test_result",
  "success": false,
  "message": "未检测到 RealSense 设备"
}
```

---

## 7. 执行控制接口

### 7.1 执行序列 `execute`

用途：

- 执行前端直接传入的序列
- 或执行服务端当前维护的序列

请求方式一：直接传序列

```json
{
  "action": "execute",
  "sequence": [
    {
      "name": "移动到A点",
      "type": "MOVE_TO_POINT",
      "parameters": {
        "目标": "机械臂",
        "臂": "左",
        "模式": "move_j",
        "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
      }
    }
  ]
}
```

请求方式二：执行当前序列

```json
{
  "action": "execute"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `execute` |
| `sequence` | `array` | 否 | 要执行的序列；省略时执行当前服务端序列 |

序列元素支持两种格式：

#### 格式 A：简化格式

```json
{
  "name": "移动到A点",
  "type": "MOVE_TO_POINT",
  "parameters": {
    "目标": "机械臂",
    "臂": "左",
    "模式": "move_j",
    "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
  }
}
```

#### 格式 B：完整格式

```json
{
  "uuid": "seq-item-id",
  "definition": {
    "id": "action-id",
    "name": "移动到A点",
    "type": "MOVE_TO_POINT",
    "parameters": {}
  },
  "status": "PENDING"
}
```

典型事件流：

1. `accepted`
2. `step_started`
3. `step_completed` 或 `step_failed`
4. `execution_finished`

接受示例：

```json
{
  "event": "accepted",
  "message": "开始执行",
  "steps": 1
}
```

步骤开始：

```json
{
  "event": "step_started",
  "index": 0,
  "name": "移动到A点",
  "status": "RUNNING"
}
```

步骤完成：

```json
{
  "event": "step_completed",
  "index": 0,
  "name": "移动到A点"
}
```

步骤失败：

```json
{
  "event": "step_failed",
  "index": 0,
  "name": "移动到A点",
  "error": "动作执行失败"
}
```

执行结束：

```json
{
  "event": "execution_finished"
}
```

常见失败场景：

- 当前已有序列在执行
- 序列为空
- 参数格式错误

### 7.2 执行任务文件 `execute_task`

请求：

```json
{
  "action": "execute_task",
  "name": "demo.task"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `execute_task` |
| `name` | `string` | 是 | 任务名 |

说明：

- 该接口会先加载任务，再立刻执行
- 后续事件与 `execute` 基本一致

### 7.3 停止执行 `stop`

请求：

```json
{
  "action": "stop"
}
```

成功响应：

```json
{
  "event": "stopped",
  "message": "已发送停止指令"
}
```

### 7.4 暂停执行 `pause`

请求：

```json
{
  "action": "pause"
}
```

成功响应：

```json
{
  "event": "paused",
  "message": "执行已暂停"
}
```

### 7.5 恢复执行 `resume`

请求：

```json
{
  "action": "resume"
}
```

成功响应：

```json
{
  "event": "resumed",
  "message": "执行已恢复"
}
```

---

## 8. 动作库接口

### 8.1 查询动作库 `list_actions`

请求：

```json
{
  "action": "list_actions"
}
```

响应示例：

```json
{
  "event": "actions_list",
  "actions": {
    "MOVE_TO_POINT": [],
    "ARM_ACTION": [],
    "INSPECT_AND_OUTPUT": [],
    "CHANGE_GUN": [],
    "VISION_CAPTURE": []
  },
  "total": 0
}
```

响应字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `actions` | `object` | 按动作类型分组的动作列表 |
| `total` | `number` | 动作总数 |

### 8.2 获取动作参数结构 `get_action_schema`

用途：

- 前端动态生成创建/编辑动作表单
- 避免前端写死字段结构

请求：

```json
{
  "action": "get_action_schema"
}
```

响应示例：

```json
{
  "event": "action_schema",
  "types": {
    "MOVE_TO_POINT": {
      "label": "移动类",
      "description": "机械臂移动 / 升降平台移动"
    }
  }
}
```

当前动作类型：

| 类型值 | 中文含义 |
|---|---|
| `MOVE_TO_POINT` | 移动类 |
| `ARM_ACTION` | 执行器类 |
| `INSPECT_AND_OUTPUT` | 检测类 |
| `CHANGE_GUN` | 换工具头类 |
| `VISION_CAPTURE` | 视觉抓取类 |

#### `MOVE_TO_POINT` 的结构特点

- 存在 `variant_key = 目标`
- 根据 `目标` 的不同，表单字段不同

变体一：`目标 = 机械臂`

| 字段 | 类型 | 说明 |
|---|---|---|
| `目标` | `select` | 固定选 `机械臂` |
| `臂` | `select` | `左` / `右` |
| `模式` | `select` | `move_j` / `move_l` |
| `点位` | `text` | 6 维位姿数组字符串 |

变体二：`目标 = 身体`

| 字段 | 类型 | 说明 |
|---|---|---|
| `目标` | `select` | 固定选 `身体` |
| `位置` | `number` | 升降平台目标位置 |

#### `ARM_ACTION` 的结构特点

- 存在 `variant_key = 执行器`
- 根据 `执行器` 的不同，参数不同

常见执行器：

| 执行器 | 常见字段 |
|---|---|
| `快换手` | `编号`、`操作` |
| `继电器` | `编号`、`操作` |
| `夹爪` | `编号`、`操作` |
| `吸液枪` | `操作`、`容量` |

### 8.3 创建动作 `create_action`

请求示例：

```json
{
  "action": "create_action",
  "name": "移动到A点",
  "type": "MOVE_TO_POINT",
  "parameters": {
    "目标": "机械臂",
    "臂": "左",
    "模式": "move_j",
    "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
  }
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `create_action` |
| `name` | `string` | 是 | 动作名称 |
| `type` | `string` | 是 | 动作类型 |
| `parameters` | `object` | 否 | 动作参数 |

成功响应：

```json
{
  "event": "action_created",
  "action": {
    "id": "uuid",
    "name": "移动到A点",
    "type": "MOVE_TO_POINT",
    "parameters": {}
  }
}
```

### 8.4 更新动作 `update_action`

请求示例：

```json
{
  "action": "update_action",
  "id": "action-id",
  "name": "新的动作名",
  "type": "ARM_ACTION",
  "parameters": {
    "执行器": "夹爪",
    "编号": 1,
    "操作": "开"
  }
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `update_action` |
| `id` | `string` | 是 | 动作 ID |
| `name` | `string` | 否 | 新名称 |
| `type` | `string` | 否 | 新类型 |
| `parameters` | `object` | 否 | 新参数 |

成功响应：

```json
{
  "event": "action_updated",
  "action": {
    "id": "action-id",
    "name": "新的动作名",
    "type": "ARM_ACTION",
    "parameters": {
      "执行器": "夹爪",
      "编号": 1,
      "操作": "开"
    }
  }
}
```

### 8.5 删除动作 `delete_action`

请求：

```json
{
  "action": "delete_action",
  "id": "action-id"
}
```

成功响应：

```json
{
  "event": "action_deleted",
  "id": "action-id"
}
```

---

## 9. 序列编排接口

服务端维护一份“当前编排序列”。下面这些接口都围绕它工作。

### 9.1 获取当前序列 `get_sequence`

请求：

```json
{
  "action": "get_sequence"
}
```

响应：

```json
{
  "event": "sequence",
  "sequence": []
}
```

### 9.2 向序列追加动作 `add_to_sequence`

该接口支持两种方式：

- 直接传动作定义 `items`
- 通过动作库 ID 引用 `action_ids`

#### 方式 A：直接传 `items`

```json
{
  "action": "add_to_sequence",
  "items": [
    {
      "name": "移动到A点",
      "type": "MOVE_TO_POINT",
      "parameters": {
        "目标": "机械臂",
        "臂": "左",
        "模式": "move_j",
        "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
      }
    }
  ]
}
```

#### 方式 B：传 `action_ids`

```json
{
  "action": "add_to_sequence",
  "action_ids": ["id1", "id2"]
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `add_to_sequence` |
| `items` | `array` | 否 | 要直接添加的动作列表 |
| `action_ids` | `array` | 否 | 要从动作库引用的动作 ID 列表 |

说明：

- `items` 和 `action_ids` 至少要有一个
- 成功后统一返回 `sequence_updated`

成功响应：

```json
{
  "event": "sequence_updated",
  "sequence": []
}
```

### 9.3 删除序列中的某一步 `remove_from_sequence`

请求：

```json
{
  "action": "remove_from_sequence",
  "index": 0
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `remove_from_sequence` |
| `index` | `number` | 是 | 序列下标 |

成功响应示例：

```json
{
  "event": "sequence_updated",
  "removed": {},
  "sequence": []
}
```

### 9.4 调整顺序 `move_in_sequence`

请求：

```json
{
  "action": "move_in_sequence",
  "from": 0,
  "to": 1
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `move_in_sequence` |
| `from` | `number` | 是 | 原索引 |
| `to` | `number` | 是 | 目标索引 |

### 9.5 清空序列 `clear_sequence`

请求：

```json
{
  "action": "clear_sequence"
}
```

成功响应：

```json
{
  "event": "sequence_updated",
  "sequence": []
}
```

---

## 10. 任务管理接口

任务本质上是把“当前序列”保存为 `.task` 文件。

### 10.1 获取任务列表 `list_tasks`

请求：

```json
{
  "action": "list_tasks"
}
```

响应：

```json
{
  "event": "tasks_list",
  "tasks": ["demo.task", "pick.task"]
}
```

### 10.2 保存任务 `save_task`

请求：

```json
{
  "action": "save_task",
  "name": "demo.task"
}
```

成功响应：

```json
{
  "event": "task_saved",
  "name": "demo.task",
  "steps": 3
}
```

### 10.3 加载任务 `load_task`

请求：

```json
{
  "action": "load_task",
  "name": "demo.task"
}
```

成功响应：

```json
{
  "event": "task_loaded",
  "name": "demo.task",
  "sequence": []
}
```

说明：

- `load_task` 只加载，不执行
- 如果希望“加载后立即执行”，请改用 `execute_task`

### 10.4 读取任务文件内容 `get_task_detail`

用途：

- 读取某个任务文件的完整序列内容
- 不影响当前服务端维护的“当前序列”
- 适合前端做“任务编辑器”

请求：

```json
{
  "action": "get_task_detail",
  "name": "demo.task"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `get_task_detail` |
| `name` | `string` | 是 | 任务文件名 |

成功响应：

```json
{
  "event": "task_detail",
  "name": "demo.task",
  "sequence": [
    {
      "uuid": "seq-item-id",
      "definition": {
        "id": "action-id",
        "name": "移动到A点",
        "type": "MOVE_TO_POINT",
        "parameters": {
          "目标": "机械臂",
          "臂": "左",
          "模式": "move_j",
          "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
        }
      },
      "status": "PENDING"
    }
  ]
}
```

### 10.5 重命名任务文件 `rename_task`

请求：

```json
{
  "action": "rename_task",
  "name": "demo.task",
  "new_name": "demo-v2.task"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `rename_task` |
| `name` | `string` | 是 | 原任务名 |
| `new_name` | `string` | 是 | 新任务名 |

成功响应：

```json
{
  "event": "task_renamed",
  "name": "demo.task",
  "new_name": "demo-v2.task"
}
```

说明：

- 如果 `new_name` 已存在，服务端会返回 `error`
- 该操作不会修改任务内部动作，只改文件名

### 10.6 直接向任务文件新增动作 `add_to_task`

用途：

- 不需要先 `load_task`
- 直接对某个 `.task` 文件追加或插入动作

支持两种方式：

- 直接传 `items`
- 通过 `action_ids` 引用动作库

#### 方式 A：直接传 `items`

```json
{
  "action": "add_to_task",
  "name": "demo.task",
  "items": [
    {
      "name": "移动到A点",
      "type": "MOVE_TO_POINT",
      "parameters": {
        "目标": "机械臂",
        "臂": "左",
        "模式": "move_j",
        "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
      }
    }
  ]
}
```

#### 方式 B：引用动作库并插入到指定位置

```json
{
  "action": "add_to_task",
  "name": "demo.task",
  "action_ids": ["action-id-1", "action-id-2"],
  "index": 0
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `add_to_task` |
| `name` | `string` | 是 | 任务文件名 |
| `items` | `array` | 否 | 直接插入的动作序列项 |
| `action_ids` | `array` | 否 | 从动作库引用的动作 ID |
| `index` | `number` | 否 | 插入位置；省略则追加到末尾 |

成功响应：

```json
{
  "event": "task_updated",
  "name": "demo.task",
  "sequence": []
}
```

### 10.7 直接删除任务文件中的动作 `remove_from_task`

请求：

```json
{
  "action": "remove_from_task",
  "name": "demo.task",
  "index": 0
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `remove_from_task` |
| `name` | `string` | 是 | 任务文件名 |
| `index` | `number` | 是 | 要删除的动作下标 |

成功响应：

```json
{
  "event": "task_updated",
  "name": "demo.task",
  "removed": {},
  "sequence": []
}
```

### 10.8 直接调整任务文件内部顺序 `move_in_task`

请求：

```json
{
  "action": "move_in_task",
  "name": "demo.task",
  "from": 0,
  "to": 2
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `move_in_task` |
| `name` | `string` | 是 | 任务文件名 |
| `from` | `number` | 是 | 原索引 |
| `to` | `number` | 是 | 新索引 |

成功响应：

```json
{
  "event": "task_updated",
  "name": "demo.task",
  "sequence": []
}
```

### 10.9 删除任务 `delete_task`

请求：

```json
{
  "action": "delete_task",
  "name": "demo.task"
}
```

成功响应：

```json
{
  "event": "task_deleted",
  "name": "demo.task"
}
```

---

## 11. AI 规划接口

这部分能力用于：

- 将自然语言解析为技能与动作规划
- 向前端返回预览序列
- 由用户决定是否执行

重要说明：

- AI 规划和 `minicpm_instruction` 不是同一个概念。
- `minicpm_instruction` 只表示“这句话被判定为机器人指令”，不代表任务序列已经生成。
- 真正的任务序列只会出现在 `ai_preview_ready.sequence` 中。
- AI 规划依赖 `OPENAI_API_KEY` 对应的 LLM 配置；如果未配置，`ai_chat` 会直接返回 `error`，聊天链路触发时则可能只看到 `minicpm_instruction`，看不到后续规划事件。

### 11.0 AI 规划完整事件流

当前系统存在两条触发入口：

1. 前端主动调用 `ai_chat`
2. MiniCPM 聊天链路中，服务端在识别到机器人指令后，内部自动调用 AI 规划

这两条入口最终都会走同一套规划核心逻辑，区别只在“入口事件”不同。

#### 11.0.1 前端主动触发路径

前端发送：

```json
{
  "action": "ai_chat",
  "text": "帮我抓一个瓶子"
}
```

成功路径的典型事件顺序如下：

1. `ai_status_changed`
2. `ai_skill_matched`
3. `ai_preview_ready`
4. `ai_status_changed`

也就是：

```json
{
  "event": "ai_status_changed",
  "status": "分析中..."
}
```

```json
{
  "event": "ai_skill_matched",
  "skill_id": "grab_bottle",
  "skill_name": "抓取瓶子",
  "confidence": 0.91,
  "params": {},
  "reasoning": "用户表达的是抓取瓶子的动作意图。"
}
```

```json
{
  "event": "ai_preview_ready",
  "sequence": [
    {
      "uuid": "seq-item-id",
      "definition": {
        "id": "action-id",
        "name": "pingzishang",
        "type": "MOVE",
        "parameters": {
          "臂": "左",
          "模式": "move_l",
          "点位": "[0.068791,-0.011241,-0.423676,-3.107000,0.000000,1.603000]"
        }
      },
      "status": "PENDING"
    }
  ],
  "skill_info": {
    "id": "grab_bottle",
    "name": "抓取瓶子"
  }
}
```

```json
{
  "event": "ai_status_changed",
  "status": "预览就绪"
}
```

说明：

- `ai_chat` 没有单独的“提交成功”响应包；前端要把后续收到的事件流当作这次请求的结果。
- `ai_preview_ready.sequence` 才是最终给前端展示、确认、执行的任务序列。
- `ai_preview_ready` 到来前，前端不应认为规划已经成功。

#### 11.0.2 MiniCPM 聊天触发路径

当用户在聊天中输入一句话后，可能先收到：

```json
{
  "event": "minicpm_instruction",
  "instruction": "帮我抓一个瓶子"
}
```

这一步只表示 Ask 分类器认定该输入属于机器人指令。随后如果 AI 规划组件可用，服务端会继续广播与 `ai_chat` 相同的规划事件流：

1. `ai_status_changed`
2. `ai_skill_matched`
3. `ai_preview_ready`
4. `ai_status_changed`

重要区别：

- `minicpm_instruction.instruction` 只是规范化后的指令文本，不是任务序列。
- 前端不要把 `instruction` 当成 `sequence` 使用。
- 前端要等待 `ai_preview_ready.sequence`，而不是看到 `minicpm_instruction` 就直接执行。
- 如果聊天链路只收到了 `minicpm_instruction`，但一直没有后续 `ai_status_changed` / `ai_preview_ready`，通常表示 AI 规划当前不可用、正在忙、或未满足启动条件。

#### 11.0.3 匹配失败路径

如果模型无法将输入匹配到当前技能库中的某个技能，通常会收到：

1. `ai_status_changed(status = "分析中...")`
2. `ai_skill_not_matched`
3. `ai_status_changed(status = "匹配失败")`

示例：

```json
{
  "event": "ai_skill_not_matched",
  "error": "无法理解您的意图（置信度过低）"
}
```

此时前端应：

- 停止 loading 状态
- 向用户展示未匹配原因
- 不要展示“确认执行”按钮

#### 11.0.4 硬错误路径

若请求参数非法、LLM 不可用、或规划内部发生异常，服务端会发送：

```json
{
  "event": "error",
  "message": "LLM 不可用，请检查 config.env 中的 API Key 配置"
}
```

常见触发场景：

- `ai_chat.text` 为空
- 当前已有一轮 AI 规划正在处理中
- `OPENAI_API_KEY` 未配置，导致 LLM 客户端不可用
- 技能引擎未初始化
- 规划展开或校验失败

#### 11.0.5 前端状态机建议

建议前端把 AI 规划分成 5 个本地状态：

- `idle`：空闲，尚未发起规划
- `planning`：已发起规划，等待模型分析
- `matched`：已匹配技能，但尚未拿到可执行预览
- `preview_ready`：已拿到 `ai_preview_ready.sequence`，等待用户确认
- `executing`：用户已确认，正在执行动作序列

推荐状态迁移：

- 发送 `ai_chat` 后进入 `planning`
- 收到 `ai_skill_matched` 后进入 `matched`
- 收到 `ai_preview_ready` 后进入 `preview_ready`
- 收到 `ai_confirm` 对应的 `accepted` 后进入 `executing`
- 收到 `ai_skill_not_matched`、`error`、`ai_cancelled`、`ai_execution_finished` 后，根据场景退回 `idle`

前端务必区分三类数据：

- `minicpm_instruction`：指令识别通知
- `ai_preview_ready.sequence`：待确认的任务序列
- `step_started` / `step_completed` / `step_failed`：执行阶段的进度事件

### 11.1 发起 AI 规划 `ai_chat`

请求：

```json
{
  "action": "ai_chat",
  "text": "帮我抓一个瓶子"
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `ai_chat` |
| `text` | `string` | 是 | 自然语言输入 |

前置条件：

- 服务端已正确初始化 LLM 客户端
- `OPENAI_API_KEY` 已配置且可用
- 当前没有另一轮 AI 规划正在处理中
- 技能引擎已成功加载

典型事件流：

#### 1. 进入处理中

```json
{
  "event": "ai_status_changed",
  "status": "分析中..."
}
```

#### 2. 匹配到技能

```json
{
  "event": "ai_skill_matched",
  "skill_id": "skill-id",
  "skill_name": "抓取瓶子",
  "confidence": 0.91,
  "params": {},
  "reasoning": "用户表达的是抓取瓶子的动作意图。"
}
```

#### 3. 生成预览序列

```json
{
  "event": "ai_preview_ready",
  "sequence": [],
  "skill_info": {}
}
```

#### 4. 预览已就绪

```json
{
  "event": "ai_status_changed",
  "status": "预览就绪"
}
```

说明：

- `ai_chat` 只负责规划，不直接执行
- 真正执行要靠 `ai_confirm`
- `sequence` 会写入服务端的“AI 待确认预览区”，但此时还不会覆盖当前执行序列
- 前端应以 `ai_preview_ready.sequence` 作为唯一权威预览数据源
- 如果收到 `ai_skill_not_matched` 或 `error`，则视为本轮规划失败

### 11.2 确认执行 AI 规划 `ai_confirm`

请求：

```json
{
  "action": "ai_confirm"
}
```

效果：

- 将最近一次 AI 预览结果写入当前序列
- 立即开始执行

成功后典型事件流：

1. `accepted`
2. `step_started`
3. `step_completed` 或 `step_failed`
4. `ai_execution_finished`
5. `execution_finished`

接受示例：

```json
{
  "event": "accepted",
  "message": "AI 序列开始执行",
  "steps": 4
}
```

AI 执行流程结束示例：

```json
{
  "event": "ai_execution_finished",
  "success": true,
  "message": "AI 序列执行完成"
}
```

说明：

- `ai_confirm` 只能确认“最近一次尚未取消的 AI 预览序列”。
- 如果当前没有待确认预览，服务端会返回 `error`。
- `ai_confirm` 成功后，预览缓存会被清空。
- 执行进度事件与普通 `execute` 共用同一套 `step_started` / `step_completed` / `step_failed` / `execution_finished`。

### 11.3 取消 AI 规划 `ai_cancel`

请求：

```json
{
  "action": "ai_cancel"
}
```

成功响应：

```json
{
  "event": "ai_cancelled",
  "message": "AI 规划已取消"
}
```

说明：

- `ai_cancel` 只会清空最近一次待确认的 AI 预览结果。
- 它不会终止已经开始执行的动作序列。
- 取消后，前端应清空本地的 AI 预览面板和“确认执行”按钮状态。

### 11.4 查询 AI 状态 `ai_status`

请求：

```json
{
  "action": "ai_status"
}
```

响应示例：

```json
{
  "event": "ai_status",
  "llm_available": true,
  "api_key_set": true,
  "model": "gpt-4o",
  "provider": "OPENAI",
  "processing": false,
  "has_preview": true
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `llm_available` | `boolean` | LLM 是否可用 |
| `api_key_set` | `boolean` | 是否已配置 API Key |
| `model` | `string` | 当前模型名 |
| `provider` | `string` | 提供商 |
| `processing` | `boolean` | 是否正在处理中 |
| `has_preview` | `boolean` | 是否存在待确认预览 |

推荐用途：

- 页面初始化时先调用一次，判断当前 AI 功能是否可用
- 若 `processing = true`，前端应避免重复发起 `ai_chat`
- 若 `has_preview = true`，前端可恢复上一次未确认的 AI 预览面板
- 若 `llm_available = false` 或 `api_key_set = false`，前端应明确提示“当前只支持聊天/普通控制，不支持 AI 规划”

### 11.5 查询技能列表 `list_skills`

请求：

```json
{
  "action": "list_skills"
}
```

成功响应：

```json
{
  "event": "skills_list",
  "skills": []
}
```

---

## 12. 相机接口

当前相机能力统一集成在主控制连接中。

### 12.1 查询相机状态 `camera_status`

请求：

```json
{
  "action": "camera_status"
}
```

响应示例：

```json
{
  "event": "camera_status",
  "available": true,
  "camera_count": 2,
  "cameras": [
    {
      "serial": "153122077516",
      "name": "cam-1",
      "online": true
    },
    {
      "serial": "153122077517",
      "name": "cam-2",
      "online": false,
      "error": "设备不可用"
    }
  ],
  "stream_url": "ws://localhost:8765/camera/stream",
  "frames_url": "ws://localhost:8765/camera/frames"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | `boolean` | 是否至少存在一台可用相机 |
| `camera_count` | `number` | 在线相机数 |
| `cameras` | `array` | 已配置相机状态列表 |
| `stream_url` | `string` | 兼容字段 |
| `frames_url` | `string` | 兼容字段 |

### 12.2 订阅相机帧 `subscribe_camera_frames`

请求：

```json
{
  "action": "subscribe_camera_frames"
}
```

成功响应：

```json
{
  "event": "camera_subscribed"
}
```

之后会持续收到：

```json
{
  "event": "camera_frames",
  "frames": [
    {
      "serial": "153122077516",
      "name": "cam-1",
      "index": 0,
      "data": "<base64-jpeg>"
    }
  ]
}
```

帧字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `serial` | `string` | 相机序列号 |
| `name` | `string` | 相机名称 |
| `index` | `number` | 当前帧在推送列表中的索引 |
| `data` | `string` | Base64 编码 JPEG |

前端渲染示例：

```javascript
function toImageSrc(frame) {
  return `data:image/jpeg;base64,${frame.data}`;
}
```

### 12.3 取消订阅相机帧 `unsubscribe_camera_frames`

请求：

```json
{
  "action": "unsubscribe_camera_frames"
}
```

成功响应：

```json
{
  "event": "camera_unsubscribed"
}
```

### 12.4 相机错误事件 `camera_error`

在未配置相机或相机不可用时，可能收到：

```json
{
  "event": "camera_error",
  "message": "未配置任何相机",
  "cameras": []
}
```

---

## 13. MiniCPM 聊天代理接口

该部分能力是“当前服务作为代理，转发前端消息到 MiniCPM 网关”。

### 13.1 查询 MiniCPM 状态 `minicpm_status`

请求：

```json
{
  "action": "minicpm_status"
}
```

响应示例：

```json
{
  "event": "minicpm_status",
  "configured": true,
  "gateway": "https://10.10.17.13:8006",
  "ask_enabled": true,
  "chat_action": "chat_connect / chat / chat_disconnect"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `configured` | `boolean` | 是否已完成配置 |
| `gateway` | `string` | 目标网关地址 |
| `ask_enabled` | `boolean` | 是否启用指令分类 |
| `chat_action` | `string` | 推荐的聊天流程 |

### 13.2 建立聊天会话 `chat_connect`

请求：

```json
{
  "action": "chat_connect"
}
```

成功响应：

```json
{
  "event": "chat_connected"
}
```

说明：

- 表示前端连接已进入聊天模式
- 不是和网关建立永久长连接

### 13.3 发送聊天消息 `chat`

请求示例：

```json
{
  "action": "chat",
  "messages": [
    {
      "role": "user",
      "content": "帮我规划一个抓瓶子的动作"
    }
  ],
  "streaming": true
}
```

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `chat` |
| `messages` | `array` | 是 | 聊天消息列表 |
| `streaming` | `boolean` | 否 | 是否流式 |
| 其他字段 | 任意 | 否 | 会被透传给上游网关 |

说明：

- 调用前必须先 `chat_connect`
- 每次 `chat` 时服务端会临时连接 MiniCPM 网关
- 返回完成后网关连接会关闭
- 但前端与本服务的聊天会话状态仍保持

上游返回内容会被包装成：

```json
{
  "event": "chat_data",
  "type": "prefill_done",
  "input_tokens": 151,
  "packet": {
    "type": "prefill_done",
    "input_tokens": 151
  }
}
```

```json
{
  "event": "chat_data",
  "type": "chunk",
  "text_delta": "这是摄像头的视角范围。",
  "audio_data": "<base64,24kHz>",
  "packet": {
    "type": "chunk",
    "text_delta": "这是摄像头的视角范围。",
    "audio_data": "<base64,24kHz>"
  }
}
```

```json
{
  "event": "chat_data",
  "type": "done",
  "text": "这是摄像头的视角范围。",
  "generated_tokens": 1,
  "input_tokens": 151,
  "audio_data": "<base64,24kHz>",
  "recording_session_id": "chat_xxx",
  "packet": {
    "type": "done",
    "text": "这是摄像头的视角范围。",
    "generated_tokens": 1,
    "input_tokens": 151,
    "audio_data": "<base64,24kHz>",
    "recording_session_id": "chat_xxx"
  }
}
```

```json
{
  "event": "chat_data",
  "type": "error",
  "error": "tts engine unavailable",
  "packet": {
    "type": "error",
    "error": "tts engine unavailable"
  },
  "raw": "{\"type\":\"error\",\"error\":\"tts engine unavailable\"}"
}
```

```json
{
  "event": "chat_data",
  "type": "unknown",
  "text": "{\"type\":\"vendor_extra\",\"foo\":\"bar\"}",
  "packet": {
    "type": "vendor_extra",
    "foo": "bar"
  },
  "raw": "{\"type\":\"vendor_extra\",\"foo\":\"bar\"}"
}
```

字段说明：

- `type = prefill_done`：上游已完成预填充，通常可忽略；如需展示统计信息，可读取 `input_tokens`
- `type = chunk`：流式增量文本，前端应持续拼接 `text_delta`；若存在 `audio_data`，表示当前 chunk 对应的语音片段
- `type = done`：单轮回复结束，`text` 为最终完整文本；若存在 `audio_data`，通常表示完整音频或最后一段音频
- `type = error`：上游聊天链路返回错误信息，前端应停止当前流式渲染并提示用户；此时会额外带 `raw` 便于排障
- `type = unknown`：后端无法识别的上游包；`text` 是原始文本，`packet` 是已解析成功的完整 JSON（如果能解析），并保留 `raw` 便于联调
- `packet`：服务端对上游完整 JSON 包的透传。顶层字段用于稳定消费，`packet` 用于读取新增字段、厂商扩展字段或未来版本兼容字段
- `raw`：仅用于调试和兜底，不应再作为前端主业务协议入口

语音输出请求写法：

```json
{
  "action": "chat",
  "messages": [
    {
      "role": "user",
      "content": "请介绍一下你看到的画面"
    }
  ],
  "streaming": true,
  "tts": {
    "enabled": true
  }
}
```

联调结论（基于当前部署的 MiniCPM 网关实测）：

- 若希望上游返回 `audio_data`，应优先使用 `tts: { "enabled": true }`
- `tts: true` 这种布尔写法，当前网关会直接返回 `type = error`
- `tts_config` 不保证在当前网关实现中生效；如果前端传了它却收不到 `audio_data`，先改回 `tts` 对象格式再排查

前端接收后的推荐处理流程：

1. 收到用户发送动作后，先在本地聊天列表插入一条用户消息。
2. 同时创建一条“助手占位消息”，初始内容为空，状态建议标记为 `streaming`。
3. 发送 `chat` 请求后，前端开始等待 `event = chat_data` 的消息流。
4. 收到 `type = prefill_done` 时，不需要渲染正文；如需展示调试信息，可记录 `input_tokens`。
5. 收到 `type = chunk` 时，将 `text_delta` 追加到当前这条助手占位消息中，并立即刷新界面，形成打字机效果。
6. 如果 `chunk.audio_data` 有值，前端应将其视为 Base64 音频片段，交给音频播放缓冲区、解码器或播放器队列处理。
7. 如果业务还需要使用上游新增字段、音频元信息、厂商扩展参数等，不要再从 `raw` 里手搓 `JSON.parse`，而是直接读取 `data.packet.xxx`。
8. 收到 `type = done` 时，将当前这条助手消息状态改为 `done`，并用 `text` 作为最终权威文本；如果前面已经累积过若干 `chunk`，仍建议以 `done.text` 为最终落库内容。
9. 如果 `done.audio_data` 有值，前端应将其作为完整音频或最后一段音频处理；不要假设语音一定只会出现在 `done` 或一定只会出现在 `chunk`。
10. 收到 `type = error` 时，应停止本轮流式输出，将错误信息展示给用户；如需排障，可记录 `raw` 和 `packet`。
11. 收到 `type = unknown` 时，不要把它当成正式业务包处理；应把 `text` / `packet` 记录到调试面板，作为未来兼容的观察入口。
12. 收到 `event = error` 时，应把当前助手占位消息标记为失败，并将错误信息展示给用户。
13. 页面关闭、切换会话、或确认不再继续聊天时，再调用 `chat_disconnect` 释放聊天会话。

前端状态管理建议：

- 当前协议的 `chat_data` 不带 `request_id`，因此同一个连接上，建议一轮回复结束前不要并发发送多条 `chat` 请求。
- 最稳妥的做法是：上一轮收到 `type = done` 或 `event = error` 之前，发送按钮置灰，或由前端本地排队串行发送。
- 前端主业务逻辑应优先消费顶层稳定字段；如需兼容上游新增字段，再读取 `data.packet`。
- 前端不应只依赖 `data.raw` 做主业务逻辑；当前版本中，`raw` 默认只在 `error` / `unknown` / 非 JSON 兜底场景出现。
- 如果需要统计本轮 token 或会话编号，可在 `done` 事件中读取 `generated_tokens`、`input_tokens`、`recording_session_id`。
- 如果要支持语音播放，前端应同时处理 `chunk.audio_data` 和 `done.audio_data`，因为不同上游实现可能只在其中一种包型中携带音频。
- 若当前请求未开启 TTS、网关未启用语音能力、或模型未返回语音，则 `audio_data` 可能始终缺失或为 `null`，这属于正常情况。

推荐的前端分发伪代码：

```javascript
let currentAssistantMessageId = null;
let currentStreamText = "";
let currentAudioChunks = [];

function handleChatData(data) {
  const packet = data.packet || {};

  switch (data.type) {
    case "prefill_done":
      updateChatMeta({ inputTokens: data.input_tokens });
      break;

    case "chunk":
      if (!currentAssistantMessageId) {
        currentAssistantMessageId = createAssistantMessage({ text: "", status: "streaming" });
        currentStreamText = "";
        currentAudioChunks = [];
      }
      currentStreamText += data.text_delta || "";
      if (data.audio_data) {
        currentAudioChunks.push(data.audio_data);
        appendAudioChunk(data.audio_data);
      }
      // 如果后续有厂商扩展字段，例如 packet.audio_format，可在这里继续读取。
      updateMessage(currentAssistantMessageId, {
        text: currentStreamText,
        status: "streaming"
      });
      break;

    case "done":
      if (!currentAssistantMessageId) {
        currentAssistantMessageId = createAssistantMessage({ text: "", status: "streaming" });
      }
      updateMessage(currentAssistantMessageId, {
        text: data.text || currentStreamText,
        status: "done",
        generatedTokens: data.generated_tokens,
        inputTokens: data.input_tokens,
        recordingSessionId: data.recording_session_id
      });
      if (data.audio_data) {
        currentAudioChunks.push(data.audio_data);
      }
      finalizeAudio(currentAudioChunks);
      currentAssistantMessageId = null;
      currentStreamText = "";
      currentAudioChunks = [];
      break;

    case "error":
      showChatError(data.error || "MiniCPM 返回错误");
      appendDebugLog({
        packet,
        raw: data.raw || null
      });
      currentAssistantMessageId = null;
      currentStreamText = "";
      currentAudioChunks = [];
      break;

    case "unknown":
      appendDebugLog({
        text: data.text,
        packet,
        raw: data.raw || null
      });
      break;
  }
}
```

补充说明：

- `chunk` 负责流式展示，`done` 负责最终收口，两者不是二选一，而是一前一后配合使用。
- `audio_data` 是可选字段，不是每一轮响应都一定返回。
- 如果你打开了 TTS，但始终没有收到 `audio_data`，先检查请求体是否使用了 `tts: { "enabled": true }`，再确认上游网关当前模型/模式是否支持语音输出。
- 如果本轮完全没有收到 `chunk`，前端仍应能只依赖 `done.text` 完成展示。
- 如果收到了若干 `chunk`，但 `done.text` 与累计文本有差异，应优先信任 `done.text`，因为它代表后端确认后的完整结果。
- `packet` 是完整透传的上游 JSON 包，作用是“字段不丢失、扩展字段可用”；顶层稳定字段的作用是“前端主流程不用再自己猜协议”。
- `raw` 主要用于兼容未来上游协议变化和联调排查；如果响应中包含大段 Base64 音频，`raw` 体积会很大，因此默认不在正常 `chunk` / `done` / `prefill_done` 包里重复返回。
- `unknown` 主要用于兼容未来上游协议变化，正常页面可以不展示给普通用户，但建议保留调试入口。

### 13.4 指令识别事件 `minicpm_instruction`

当服务端判断用户输入属于机器人可执行指令时，可能广播：

```json
{
  "event": "minicpm_instruction",
  "instruction": "帮我抓一个瓶子"
}
```

补充说明：

- 该事件依赖 `MINICPM_ASK_ENABLED=true` 且存在可用的 Ask 分类 API Key
- 若 `MINICPM_ASK_API_KEY` 和 `OPENAI_API_KEY` 都未配置，则不会自动触发该事件
- 普通聊天回复仍会通过 `chat_data` 返回，与是否触发指令规划无关
- `minicpm_instruction` 不是聊天正文，它只是“该输入被判定为机器人指令”的附加通知事件
- 前端不要把 `minicpm_instruction.instruction` 当成机器人回复文本渲染到聊天气泡中

适合的前端处理方式：

- 高亮提示“检测到可执行指令”
- 自动弹出 AI 规划确认面板

### 13.5 断开聊天会话 `chat_disconnect`

请求：

```json
{
  "action": "chat_disconnect"
}
```

成功响应：

```json
{
  "event": "chat_disconnected"
}
```

---

## 14. 推荐的前端接入流程

### 14.1 通用后台管理页面

推荐流程：

1. 建立连接
2. 调用 `status`
3. 调用 `list_actions`
4. 调用 `get_action_schema`
5. 调用 `get_sequence`
6. 调用 `list_tasks`

### 14.2 执行控制页面

推荐流程：

1. 建立连接
2. 调用 `status`
3. 按需调用 `init_robots`、`init_body`
4. 通过 `add_to_sequence` 组装序列，或 `load_task`
5. 调用 `execute`
6. 监听 `step_started`、`step_completed`、`step_failed`、`execution_finished`

### 14.3 AI 规划页面

推荐流程：

1. 页面初始化时先调用 `ai_status`，确认 `llm_available`、`api_key_set`、`processing`、`has_preview`
2. 用户输入自然语言后调用 `ai_chat`
3. 收到 `ai_status_changed(status = "分析中...")` 后进入 loading 状态
4. 收到 `ai_skill_matched` 后展示“已匹配技能”和参数摘要
5. 收到 `ai_preview_ready` 后展示任务序列预览，并启用“确认执行”按钮
6. 若收到 `ai_skill_not_matched` 或 `error`，则结束本轮规划并给出失败提示
7. 用户确认后调用 `ai_confirm`
8. 执行阶段继续监听 `accepted`、`step_started`、`step_completed`、`step_failed`、`ai_execution_finished`、`execution_finished`
9. 用户取消预览则调用 `ai_cancel`

### 14.4 相机预览页面

推荐流程：

1. 调用 `camera_status`
2. 若 `available = true`，调用 `subscribe_camera_frames`
3. 将 `camera_frames` 渲染为图片
4. 页面销毁时调用 `unsubscribe_camera_frames`

### 14.5 MiniCPM 聊天页面

推荐流程：

1. 调用 `minicpm_status`
2. 调用 `chat_connect`
3. 用户发送消息时，先在本地插入用户气泡，再创建一条空的助手占位气泡
4. 调用 `chat`
5. 监听 `chat_data`
6. 对 `prefill_done`、`chunk`、`done`、`error`、`unknown` 按协议分别处理
7. 只有收到 `done` 或 `error` 后，才允许开始下一轮发送
8. 若同时收到 `minicpm_instruction`，应将其视为“指令识别提示”或“规划入口”，不要当聊天正文显示
9. 不再使用时调用 `chat_disconnect`

---

## 15. 错误处理手册

服务端统一错误格式：

```json
{
  "event": "error",
  "message": "..."
}
```

前端建议：

- 全局监听 `event === "error"`
- 直接展示 `message`
- 将错误按模块分类显示：
  - 执行错误
  - AI 错误
  - 设备错误
  - 相机错误
  - 聊天代理错误

常见错误场景：

| 场景 | 可能错误 |
|---|---|
| 重复执行 | 已有序列正在执行 |
| 空序列执行 | 序列为空 |
| 动作参数错误 | 参数解析失败 |
| 硬件未就绪 | 机械臂控制器未初始化 |
| AI 不可用 | API Key 未配置或 LLM 不可用 |
| 相机不可用 | 未配置序列号或未检测到设备 |
| MiniCPM 不可用 | 网关连接失败 |

---

## 16. 兼容性与注意事项

- 旧文档中的 `python run_server.py` 已不适用，当前统一入口是 `python run.py`
- 仓库不再默认提交 `config.env`
- 相机和 MiniCPM 功能现在优先走主控制 WebSocket 的 `action` 路由
- 前端不要假设硬件一定在线
- 前端不要写死动作编辑表单，应该优先使用 `get_action_schema`

---

## 17. 最小可用前端示例

```javascript
const ws = new WebSocket("ws://localhost:8765");

ws.onopen = () => {
  ws.send(JSON.stringify({ action: "status" }));
  ws.send(JSON.stringify({ action: "list_actions" }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  switch (data.event) {
    case "status":
      console.log("服务状态", data);
      break;
    case "actions_list":
      console.log("动作库", data.actions);
      break;
    case "log": {
      // level: "info" | "warn" | "error"
      const fn = data.level === "error" ? console.error
               : data.level === "warn"  ? console.warn
               : console.log;
      fn(`[${data.level}] ${data.message}`);
      break;
    }
    case "error":
      console.error("错误", data.message);
      break;
    default:
      console.log("其他事件", data);
  }
};

function executeDemo() {
  ws.send(JSON.stringify({
    action: "execute",
    sequence: [
      {
        name: "移动到A点",
        type: "MOVE_TO_POINT",
        parameters: {
          "目标": "机械臂",
          "臂": "左",
          "模式": "move_j",
          "点位": "[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"
        }
      }
    ]
  }));
}
```

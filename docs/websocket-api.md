# WebSocket 接口手册

本文档对应当前 `main` 分支上的服务端实现，统一入口为
`uv run robot-llm`。WebSocket 由 GUI 主应用进程托管。

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
- LLM 聊天 / MiniCPM Realtime

主连接地址：

- `ws://{host}:{port}/`

默认监听配置：

- `host = 127.0.0.1`
- `port = 8765`

WebSocket 作为 GUI 应用的可选附加服务运行，与 GUI 共用同一套
`ApplicationServices`、执行运行时和设备运行时。

服务端内部按领域拆分：

```text
ws_server.py                 连接生命周期、鉴权、限流、顶层路由和消息投递
protocol.py                  WebSocketRequest / WebSocketResponse 与 action schema
routing.py                   唯一 action → handler 注册表
handlers/execution.py        执行控制与执行事件映射
handlers/composition.py      动作库、序列和任务编排
handlers/interaction.py      AI、LLM 聊天和交互会话
handlers/device.py           设备状态、初始化和相机订阅
handlers/teleoperation.py    遥操作与数据采集
```

所有请求先转换为 `WebSocketRequest` 并按 action schema 完整校验，再进入
鉴权和领域 handler；所有出站消息经 `WebSocketResponse` 统一序列化。新增
action 时必须同时注册路由和 schema，否则服务初始化失败。

协议基本约定：

- 客户端发给服务端的 JSON 必须包含 `action`
- 服务端返回给客户端的 JSON 必须包含 `event`
- 同一条 WebSocket 连接中，既会收到“接口直接响应”，也会收到“后台异步推送”

你可以把它理解为：

- `action`：客户端发起的命令
- `event`：服务端反馈的结果或状态变化

GUI 或其他客户端修改动作库、任务库或当前序列后，所有已连接客户端会收到：

```json
{
  "event": "composition_changed",
  "change": "actions",
  "revision": 12,
  "change_revision": 4,
  "origin": "gui",
  "actions": []
}
```

`change` 取值为 `actions`、`tasks` 或 `sequence`。`revision` 是全局版本，
`change_revision` 是该类状态的独立版本，消息中携带对应的最新快照。

---

## 2. 启动与配置

### 2.1 安装依赖

```bash
uv sync --frozen
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
uv run robot-llm --simulation
```

连接真实硬件：

```bash
uv run robot-llm
```

指定端口：

```bash
uv run robot-llm --websocket-port 9000
```

本次启动禁用 WebSocket：

```bash
uv run robot-llm --disable-websocket
```

### 2.4 推荐启动方式

如果你只是做前端联调，推荐使用：

```bash
uv run robot-llm --simulation
```

原因：

- 不依赖真实机械臂
- 不依赖串口设备
- 适合先打通页面和协议

### 2.5 关键配置项

与接口能力直接相关的配置项如下：

```env
SIMULATION_MODE=false

WEBSOCKET_ENABLED=true
WEBSOCKET_HOST=127.0.0.1
WEBSOCKET_PORT=8765
WEBSOCKET_AUTH_TOKEN=
WEBSOCKET_ALLOWED_ORIGINS=
WEBSOCKET_TLS_CERTIFICATE_PATH=
WEBSOCKET_TLS_PRIVATE_KEY_PATH=
WEBSOCKET_REVERSE_PROXY_MODE=false
WEBSOCKET_CONTROL_LEASE_SECONDS=30.0
WEBSOCKET_MAX_MESSAGE_SIZE_BYTES=1048576
WEBSOCKET_MAX_REQUESTS_PER_SECOND=120
WEBSOCKET_MAX_CONCURRENT_REQUESTS=16
WEBSOCKET_MAX_QUEUED_MESSAGES=16
WEBSOCKET_SEND_TIMEOUT_SECONDS=2.0
WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS=0.5
AUXILIARY_SERVICE_START_TIMEOUT_SECONDS=5.0
AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS=10.0

LLM_DEFAULT_PROVIDER=dashscope
OPENAI_API_KEY=
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-turbo

REALSENSE_DEVICE_SN=153122077516
REALSENSE_DEVICE_NAMES=

MINICPM_GATEWAY_HOST=10.10.17.13
MINICPM_GATEWAY_PORT=8006
MINICPM_WS_SCHEME=wss
MINICPM_REALTIME_PATH=/v1/realtime

MINICPM_ASK_ENABLED=true
MINICPM_ASK_API_KEY=
MINICPM_ASK_BASE_URL=
MINICPM_ASK_MODEL=qwen-turbo
```

配置项说明：

| 配置项 | 含义 | 备注 |
|---|---|---|
| `SIMULATION_MODE` | 是否模拟模式 | `true` 时不连接真实硬件 |
| `WEBSOCKET_ENABLED` | 是否随 GUI 启动 WebSocket | 默认 `true` |
| `WEBSOCKET_HOST` | WebSocket 监听地址 | 默认 `127.0.0.1`；非 loopback 监听必须由服务端直接提供 TLS |
| `WEBSOCKET_PORT` | WebSocket 监听端口 | 默认 `8765` |
| `WEBSOCKET_AUTH_TOKEN` | 写操作共享认证密钥 | 留空时服务保持可读，但所有写操作均被拒绝；远程/TLS/代理部署时必填 |
| `WEBSOCKET_ALLOWED_ORIGINS` | 浏览器 Origin 白名单 | 逗号分隔、精确匹配；远程/TLS/代理部署时必填；不发送 Origin 的非浏览器客户端不受影响 |
| `WEBSOCKET_TLS_CERTIFICATE_PATH` | 服务端 TLS 证书链 | 与私钥同时配置后直接提供 `wss://` |
| `WEBSOCKET_TLS_PRIVATE_KEY_PATH` | 服务端 TLS 私钥 | 不得提交私钥文件；与证书同时配置 |
| `WEBSOCKET_REVERSE_PROXY_MODE` | 同机可信反向代理模式 | 启用后必须绑定 loopback，由代理终止 TLS；不能同时启用服务端 TLS |
| `WEBSOCKET_CONTROL_LEASE_SECONDS` | 单一控制客户端租约时长 | 默认 30 秒；控制指令或心跳会续期 |
| `WEBSOCKET_MAX_MESSAGE_SIZE_BYTES` | 单条入站消息上限 | 默认 1048576 字节；超限连接由 WebSocket 层以 1009 关闭 |
| `WEBSOCKET_MAX_REQUESTS_PER_SECOND` | 每客户端每秒请求上限 | 默认 120，包含高频遥操作余量 |
| `WEBSOCKET_MAX_CONCURRENT_REQUESTS` | 全服务同时处理的请求上限 | 默认 16；超限返回 `server_busy` |
| `WEBSOCKET_MAX_QUEUED_MESSAGES` | 每连接入站排队上限 | 默认 16，由 WebSocket 库施加背压 |
| `WEBSOCKET_SEND_TIMEOUT_SECONDS` | 单次出站发送超时 | 默认 2 秒；慢客户端会被断开并释放其会话资源 |
| `WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS` | 慢发送监控阈值 | 默认 0.5 秒；达到阈值计入 `slow_sends_total` |
| `AUXILIARY_SERVICE_START_TIMEOUT_SECONDS` | 单个附加服务启动超时 | 默认 5 秒 |
| `AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS` | 单个附加服务停止超时 | 默认 10 秒 |
| `LLM_DEFAULT_PROVIDER` | 默认 LLM provider | `openai` / `deepseek` / `dashscope` / `minicpm`；`TaskProfile.default_provider` 或请求里的 `provider` 可以覆盖 |
| `LLM_FALLBACK_PROVIDERS` | 未显式指定 provider 时的降级顺序 | 逗号分隔；默认留空，不跨厂商转发 |
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | provider 连续失败熔断阈值 | 默认 3 |
| `LLM_CIRCUIT_RECOVERY_SECONDS` | 熔断后的半开探测等待时间 | 默认 30 秒 |
| `OPENAI_API_KEY` | OpenAI-compatible API Key | `openai` / `deepseek` / `dashscope` 使用 |
| `OPENAI_BASE_URL` | OpenAI-compatible Base URL | 留空时使用 provider 默认值 |
| `OPENAI_MODEL` | OpenAI-compatible 模型名 | 如 `qwen-turbo` / `gpt-4o` |
| `REALSENSE_DEVICE_SN` | RealSense 序列号 | 支持逗号分隔多台 |
| `REALSENSE_DEVICE_NAMES` | RealSense 名称 | 与序列号一一对应 |
| `MINICPM_GATEWAY_HOST` | MiniCPM 网关主机 | `LLM_DEFAULT_PROVIDER=minicpm`、task 默认 provider 为 `minicpm` 或请求指定 `provider=minicpm` 时使用 |
| `MINICPM_GATEWAY_PORT` | MiniCPM 网关端口 | 同上 |
| `MINICPM_WS_SCHEME` | MiniCPM WebSocket 协议 | `wss` 或 `ws`，默认 `wss` |
| `MINICPM_REALTIME_PATH` | Realtime Chat 路径 | 最终连接为 `{MINICPM_WS_SCHEME}://HOST:PORT{PATH_PREFIX}{REALTIME_PATH}?mode=chat` |
| `MINICPM_ASK_ENABLED` | 是否启用指令分类 | 仅影响是否触发 `minicpm_instruction` / AI 规划，不影响 MiniCPM 聊天回复 |
| `MINICPM_ASK_API_KEY` | 指令分类模型的 API Key | 留空时回退到 `OPENAI_API_KEY`；若两者都为空，则跳过分类，不自动规划 |
| `MINICPM_ASK_BASE_URL` | 指令分类模型 Base URL | 留空时回退到 `OPENAI_BASE_URL` |
| `MINICPM_ASK_MODEL` | 指令分类模型名 | 如 `qwen-turbo` |

### 2.6 TLS 与可信反向代理部署

服务只支持两种远程部署方式，配置不完整时启动直接失败：

1. 服务端直接 TLS：监听远程地址，同时配置认证密钥、Origin 白名单、证书链和
   私钥，客户端连接 `wss://host:port/`。
2. 同机可信反向代理：服务保持 `127.0.0.1`/`::1`，设置
   `WEBSOCKET_REVERSE_PROXY_MODE=true`，由 Nginx/Caddy 等同机代理提供
   `wss://`。代理必须保留 `Origin` 和 WebSocket Upgrade 头。

代理模式不读取 `X-Forwarded-For`、`X-Forwarded-Proto` 等转发头参与认证或授权，
可信边界由 loopback socket、WebSocket token 和 Origin 白名单共同构成。禁止把
代理模式后端端口直接暴露到外网。

Nginx 同机代理的最小 WebSocket location：

```nginx
location /robot/ws {
    proxy_pass http://127.0.0.1:8765/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Origin $http_origin;
    proxy_read_timeout 60s;
}
```

部署验收必须确认：

- 白名单内 Origin 可以完成握手，非白名单浏览器 Origin 被握手层拒绝。
- 未认证连接不能读取 `server_metrics`，也不能取得控制权。
- 代理模式的后端端口只能从 loopback 访问，外部只暴露代理的 `wss://` 端点。
- 服务端 TLS 模式使用 TLS 1.2 及以上，证书域名、有效期和完整链均通过客户端校验。
- 人为阻塞一个客户端发送时，其他连接仍可接收广播；超时客户端被关闭并增加
  `send_timeouts_total` 与 `slow_client_disconnects_total`。

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

连接建立后，服务端首先发送：

```json
{
  "event": "connected",
  "api_version": "2.0",
  "api_version_required": true,
  "client_id": "6cbd...",
  "authentication_configured": true,
  "control_lease_seconds": 30.0
}
```

### 3.2 写操作认证与控制权

公开查询不要求认证。执行、设备、编排、AI 规划、遥操作和数据采集等写操作
必须依次完成认证和控制权申请：

```json
{"api_version": "2.0", "action": "authenticate", "token": "<运行时注入的密钥>", "request_id": "auth-1"}
{"api_version": "2.0", "action": "acquire_control", "request_id": "control-1"}
```

认证成功返回 `authenticated`，取得控制权返回 `control_acquired`。同一时刻只有
一个客户端能够持有控制权；另一个客户端申请时收到
`access_denied / control_busy`，不会抢占现有控制者。

控制客户端应在租约过期前发送心跳：

```json
{"api_version": "2.0", "action": "control_heartbeat", "request_id": "heartbeat-1"}
```

主动释放：

```json
{"api_version": "2.0", "action": "release_control", "request_id": "release-1"}
```

租约到期、持有者断线或发送失败都会释放控制权，并停止该控制者持有的遥操作或
数据采集会话。观察者断线不会停止其他客户端的控制会话。已经提交到统一执行
运行时的普通序列不会因为网络控制租约释放而被隐式中断；需要停止时应在租约
有效期间显式调用 `stop`、`quick_stop` 或 `emergency_stop`。

权限分级：

| 级别 | action |
|---|---|
| 公开只读 | `status`、动作/序列/任务查询、`ai_status`、`list_skills`、`camera_status`、`minicpm_status`、`control_status` |
| 仅需认证 | `server_metrics`、相机帧订阅、LLM 聊天会话 |
| 认证并持有控制权 | 其余执行、设备、编排、AI 规划、遥操作和数据采集 action |

`WEBSOCKET_AUTH_TOKEN` 未配置时，`authenticate` 返回
`authentication_not_configured`，所有非公开操作保持锁定。密钥不会写入安全
审计，但在远程 `ws://` 连接中仍会以明文传输，因此远程部署必须使用 `wss://`。

### 3.3 请求消息格式

客户端请求统一为 JSON 对象：

```json
{
  "api_version": "2.0",
  "action": "status",
  "request_id": "status-1"
}
```

通用规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `api_version` | `string` | 是 | 当前只接受 `2.0`；缺失或不支持的版本会被直接拒绝 |
| `action` | `string` | 是 | 接口动作名 |
| `request_id` | `string` | 是 | 1..128 位字母、数字、`.`、`_`、`:`、`-` |
| action payload | 见具体接口 | 由接口决定 | 字段名和类型必须符合对应 action schema；未知字段会被拒绝 |

请求采用严格 schema，不接受未声明字段，也不做字符串到数字/布尔值的隐式
转换。payload 校验发生在鉴权之前；校验失败返回 `invalid_payload`，领域
handler 不会被调用。

### 3.4 响应/事件消息格式

服务端消息统一为 JSON 对象：

```json
{
  "event": "status",
  "api_version": "2.0",
  "request_id": "status-1",
  "action": "status"
}
```

通用规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `event` | `string` | 是 | 事件名 |
| `api_version` | `string` | 是 | 产生该消息的服务端协议版本 |
| `request_id` | `string` | 请求直接响应是 | 与发起请求相同 |
| `action` | `string` | 请求直接响应是 | 产生当前响应的请求 action |
| `run_id` | `string` | 执行事件是 | 一次统一执行的唯一 ID；从 `accepted` 贯通到步骤、日志和终态 |
| 其他字段 | 任意 | 否 | 由具体事件决定 |

控制租约超时、其他客户端发起的广播等非请求型事件可以没有
`request_id/action`。客户端应优先用 `request_id` 关联直接响应，用 `run_id`
关联完整执行事件流，不能依赖消息到达顺序猜测归属。

版本策略：

- 当前协议版本是 `2.0`，所有请求必须显式声明完全相同的版本。
- 2.0 将 AI 预览确认改为强制 `preview_id + version`，并增加高风险确认；
  不提供 1.0 兼容适配。
- 本项目不维护旧协议兼容适配器；协议字段、事件或语义发生破坏性变化时，
  服务端和客户端必须同步升级到新的版本值。
- 仅增加可选字段且不改变已有语义时可以保持当前版本。
- 本文后续较长的业务请求片段可能省略公共字段；实际客户端必须通过统一发送
  函数补齐 `api_version` 和 `request_id`。

### 3.5 前端一定要注意的协议特点

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
    console.error(
      `请求 ${data.request_id} 失败 [${data.code}]`,
      data.message
    );
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

### 3.6 消息投递与流量边界

| 类型 | 接收者 | 典型事件 |
|---|---|---|
| 请求单播 | 仅请求发起连接 | `status`、CRUD 结果、`error`、`access_denied` |
| 系统广播 | 当前全部连接 | `composition_changed`、执行 accepted/step/log/terminal、控制权释放 |
| 显式订阅 | 仅已订阅连接 | `camera_frames` |

广播和订阅投递并发发送，一个慢客户端不会串行阻塞其他客户端；超过
`WEBSOCKET_SEND_TIMEOUT_SECONDS` 的连接会被清理。请求频率超过限制时返回
`rate_limited` 和 `retry_after_seconds`；全服务并发达到上限时返回
`server_busy`。客户端遇到这两类错误不得立即无界重试。

已认证客户端可调用 `server_metrics` 读取进程生命周期内的聚合指标，包括当前/
峰值/累计连接数、当前/峰值/累计请求数、请求耗时、非法请求、限流、繁忙、
权限拒绝、内部错误、发送耗时、慢发送、发送失败和超时断连。指标不包含 token、
请求 payload、客户端地址或其他高基数个人数据。

---

## 4. action 总表

### 4.0 安全会话

| action | 权限 | 含义 |
|---|---|---|
| `authenticate` | 公开 | 使用 `WEBSOCKET_AUTH_TOKEN` 认证当前连接 |
| `control_status` | 公开 | 查询当前连接认证状态、控制租约和应用层遥操作 owner/活动臂/指令计数快照 |
| `server_metrics` | 已认证 | 查询 WebSocket API、连接和慢客户端聚合指标 |
| `acquire_control` | 已认证 | 申请唯一控制权 |
| `control_heartbeat` | 控制者 | 续期控制租约 |
| `release_control` | 控制者 | 主动释放控制权及其会话资源 |

### 4.1 执行控制

| action | 含义 |
|---|---|
| `execute` | 执行动作序列 |
| `execute_task` | 直接执行已保存任务 |
| `stop` | 停止当前执行 |
| `quick_stop` | 向支持的设备发送软件快停 |
| `emergency_stop` | 向支持的设备发送软件急停 |
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
| `error` | 统一请求错误；包含稳定 `code`、`message`、`request_id` 和 `action` |
| `access_denied` | 认证失败、未持有控制权、租约过期或控制权冲突 |
| `authenticated` | 当前连接认证成功 |
| `control_acquired` | 当前连接取得控制权 |
| `control_heartbeat` | 控制租约续期成功 |
| `control_released` | 控制权因主动释放、超时、断线或发送失败而释放 |
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

- `event: "error"` — 针对当前请求的同步错误，如校验、相机、遥操作或内部请求处理失败
- `event: "log", level: "error"` — 执行过程中发生的运行时错误，如"机械臂控制器未初始化"

### 5.2 执行事件

| event | 含义 |
|---|---|
| `accepted` | 服务端已接受执行请求 |
| `step_started` | 某一步开始执行 |
| `step_completed` | 某一步执行完成 |
| `step_failed` | 某一步执行失败 |
| `execution_finished` | 整个执行结束 |
| `stopped` | 已发送任务停止请求（非硬件急停） |
| `safety_stop_completed` | 软件快停/急停编排结束，结果见 `report`；不代表物理急停回路状态 |
| `paused` | 已暂停 |
| `resumed` | 已恢复 |

执行相关事件统一包含 `run_id` 和 `origin`。由 WebSocket 请求发起的执行还包含
同一个 `request_id/action`。终态示例：

```json
{
  "event": "execution_finished",
  "run_id": "run-42",
  "request_id": "execute-42",
  "action": "execute",
  "origin": "websocket",
  "state": "succeeded",
  "success": true,
  "error": null,
  "failure": {
    "code": null,
    "operation": null,
    "device_id": null
  }
}
```

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
| `error` | 相机请求失败时使用统一错误信封，`error_source` 为 `camera_error` |

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
| `provenance` | `object \| null` | 当前 LLM 调用的 Prompt 版本/哈希、实际 provider/model、尝试顺序和是否发生 fallback；不包含 Prompt 原文或密钥 |

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
    "robot-system": {
      "state": "ready",
      "ready": true,
      "capabilities": ["robot_motion", "gripper"],
      "error": "",
      "error_category": "",
      "raw_error_code": ""
    },
    "body-axis": {
      "state": "registered",
      "ready": false,
      "capabilities": ["body_axis"],
      "error": "",
      "error_category": "",
      "raw_error_code": ""
    },
    "camera": {
      "state": "registered",
      "ready": false,
      "capabilities": ["camera"],
      "error": "",
      "error_category": "",
      "raw_error_code": ""
    }
  },
  "executor": {
    "run_id": null,
    "state": "idle",
    "running": false,
    "paused": false,
    "error": "",
    "error_code": "",
    "error_operation": "",
    "error_device_id": "",
    "error_category": "",
    "raw_error_code": ""
  },
  "sequence_length": 0,
  "data_collection": {
    "state": "idle",
    "task": null,
    "next_episode_id": null,
    "episode_id": null,
    "recording": false,
    "teleoperation_shared": false
  },
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
| `devices` | `object` | 以规范设备 ID 为键的完整设备状态表；示例仅展示部分设备 |
| `devices.<device_id>.state` | `string` | 生命周期状态：`registered`、`starting`、`ready`、`stopping`、`stopped` 或 `failed` |
| `devices.<device_id>.ready` | `boolean` | 设备是否可被应用服务调用 |
| `devices.<device_id>.capabilities` | `string[]` | 设备提供的能力集合 |
| `devices.<device_id>.error` | `string` | 最近一次初始化或关闭错误；无错误时为空字符串 |
| `devices.<device_id>.error_category` | `string` | 稳定设备错误分类；无错误时为空字符串 |
| `devices.<device_id>.raw_error_code` | `string` | 可用的供应商或传输原始码；无原始码时为空字符串 |
| `executor.run_id` | `string \| null` | 当前或最近一次执行的唯一 ID |
| `executor.state` | `string` | 统一执行状态 |
| `executor.running` | `boolean` | 是否正在执行 |
| `executor.paused` | `boolean` | 是否处于暂停状态 |
| `executor.error` | `string` | 最近一次执行的用户可见失败消息；无失败时为空 |
| `executor.error_code` | `string` | 稳定执行错误码；无失败时为空 |
| `executor.error_operation` | `string` | 失败的规范操作标识 |
| `executor.error_device_id` | `string` | 失败关联的规范设备 ID |
| `executor.error_category` | `string` | `unavailable`、`connection`、`timeout`、`protocol`、`rejected`、`io` 或 `internal` |
| `executor.raw_error_code` | `string` | 可用的供应商或传输原始码；无原始码时为空字符串 |
| `sequence_length` | `number` | 当前服务端维护的序列长度 |
| `data_collection.state` | `string` | 数据采集状态机当前状态 |
| `data_collection.task` | `string \| null` | 当前采集任务 |
| `data_collection.next_episode_id` | `number \| null` | 下一条 episode 编号 |
| `data_collection.episode_id` | `number \| null` | 当前 episode 编号 |
| `data_collection.recording` | `boolean` | 是否处于 episode 启动、记录或停止阶段 |
| `data_collection.teleoperation_shared` | `boolean` | 当前 session 是否已加入共享遥操作控制 |
| `ai_processing` | `boolean` | AI 是否正在处理中 |
| `camera.available` | `boolean` | 是否有可用相机 |
| `camera.camera_count` | `number` | 在线相机数量 |
| `camera.cameras` | `array` | 相机状态列表 |
| `minicpm.configured` | `boolean` | 是否已配置 MiniCPM |
| `minicpm.gateway` | `string \| null` | MiniCPM 网关地址 |

> 此状态结构是统一设备运行时的直接切换契约。服务端不再返回
> `robot1`、`robot2`、`body` 等旧布尔字段。

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
    "robot-system": {
      "state": "ready",
      "ready": true,
      "capabilities": ["robot_motion", "gripper"],
      "error": ""
    }
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
    "body-axis": {
      "state": "ready",
      "ready": true,
      "capabilities": ["body_axis"],
      "error": ""
    }
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
  "results": {},
  "devices": {
    "robot-system": {
      "state": "stopped",
      "ready": false,
      "capabilities": ["robot_motion", "gripper"],
      "error": ""
    },
    "body-axis": {
      "state": "stopped",
      "ready": false,
      "capabilities": ["body_axis"],
      "error": ""
    }
  }
}
```

`results` 仅记录关闭失败，键为设备 ID，值为错误消息；全部关闭成功时为空对象。

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
  "error": "设备拒绝执行操作（设备=robot-system，操作=robot_system.move_to_pose）",
  "failure": {
    "status": "failed",
    "code": "device_operation_failed",
    "operation": "robot_system.move_to_pose",
    "device_id": "robot-system",
    "error_category": "rejected",
    "raw_error_code": "17"
  }
}
```

设备失败消息只包含稳定的用户语义。串口、异常类型、堆栈和 SDK 诊断不会通过
WebSocket 返回；服务端内部日志仍记录诊断上下文。

`failure.code` 当前稳定取值：

- `invalid_parameters`
- `unsupported_operation`
- `resource_not_found`
- `device_unavailable`
- `device_operation_failed`
- `operation_rejected`
- `action_timeout`
- `internal_error`

取消使用独立的 `cancelled` 终态，不映射成上述普通失败码。

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

该接口请求任务在当前动作的可中断点停止，**不会触发设备硬件急停**。

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
  "message": "已发送任务停止请求（非硬件急停）"
}
```

### 7.4 软件快停与设备急停

两个接口都会先请求取消当前统一执行，再绕过普通资源租约，向所有“已就绪且声明相应能力”的运动设备发送停止命令，并释放遥操作和轨迹教学会话：

```json
{"action": "quick_stop"}
```

```json
{"action": "emergency_stop"}
```

统一响应示例：

```json
{
  "event": "safety_stop_completed",
  "report": {
    "mode": "quick",
    "complete": true,
    "execution": {
      "before": "running",
      "after": "cancelled",
      "run_id": "4d4acbe0..."
    },
    "devices": [
      {
        "device_id": "robot-system",
        "mode": "quick",
        "status": "stopped",
        "error": ""
      },
      {
        "device_id": "mobile-base",
        "mode": "quick",
        "status": "not_ready",
        "error": ""
      }
    ],
    "errors": []
  }
}
```

设备结果状态：

| `status` | 含义 |
|---|---|
| `stopped` | adapter/SDK 停止调用成功返回；不等同于设备已物理停稳 |
| `not_ready` | 运行时没有该设备的就绪实例，没有可由运行时停止的活动命令 |
| `unsupported` | 设备已就绪，但未声明所请求的停止能力 |
| `failed` | 设备声明了能力，但契约校验或停止调用失败 |

只要仍有 active execution、会话错误、已就绪设备不支持或停止失败，
`complete` 就是 `false`。当前只有 RealMan 双臂机械臂实现了这两个软件停止能力，
且真实硬件验收尚未完成。等待统一执行退出的上限由
`SAFETY_STOP_WAIT_TIMEOUT_SECONDS` 配置，默认 2 秒；该值不是设备 SDK 调用超时。

> `emergency_stop` 是软件命令入口，不能替代安全等级合规的独立物理急停按钮、
> 断电回路或控制器安全功能。客户端不得把 `stopped` 展示为“已确认物理停稳”。

### 7.5 暂停执行 `pause`

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

### 7.6 恢复执行 `resume`

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

响应内容来自服务端唯一 Action Schema，并覆盖当前全部 `ActionType`。前端不得
自行维护另一份字段、默认值、单位、选项或范围定义。
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
| `BASE_MOVE` | 底盘移动类 |
| `ARM_ACTION` | 执行器类 |
| `INSPECT_AND_OUTPUT` | 检测类 |
| `WAIT` | 等待类 |
| `CHANGE_GUN` | 换工具头类 |
| `VISION_CAPTURE` | 视觉抓取类 |
| `VISION_RELOCALIZE` | 视觉重定位类 |
| `TRAJECTORY` | 轨迹执行类 |

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
| `吸液枪` | `操作`、`容量`、`吸液速度`、`吐液速度`、`吐液容量模式`（`指定容量`/`全吐`） |

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
- AI 规划依赖 `TaskProfile.default_provider` 或 `LLM_DEFAULT_PROVIDER` 解析出的 provider；如果 provider 不可用，`ai_chat` 会直接返回 `error`，聊天链路触发时则可能只看到 `minicpm_instruction`，看不到后续规划事件。

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
  "preview_id": "5d66bc2f-23e4-44f5-9159-49235fc8b5d4",
  "version": 12,
  "source": "websocket-ai",
  "created_at": "2026-07-29T03:20:00Z",
  "expires_at": "2026-07-29T03:22:00Z",
  "state": "pending",
  "sequence": [
    {
      "uuid": "seq-item-id",
      "definition": {
        "id": "action-id",
        "name": "pingzishang",
        "type": "MOVE_TO_POINT",
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
  },
  "validation": {
    "is_valid": true,
    "code": "valid",
    "message": "校验通过",
    "warnings": []
  },
  "risk": {
    "level": "high",
    "reasons": ["physical_action:MOVE"],
    "requires_acknowledgement": true
  },
  "requires_confirmation": true,
  "requires_risk_acknowledgement": true
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
- 前端必须同时保存 `preview_id` 和 `version`，不得只凭“最近一次预览”确认。
- `expires_at` 到期、版本被替换、预览已取消或已确认后，旧确认请求都会失败。
- `requires_risk_acknowledgement=true` 时必须展示风险原因并要求用户独立确认。
- 服务端只发布 `validation.code=valid` 且 `requires_confirmation=true`
  的预览；未知 action type 会返回 `unsupported_action_type`，不会生成或缓存预览。
- Skill 输入参数会先检查声明类型、必填项和未知参数，再按显式字段绑定检查单位；
  绑定后的动作参数继续按同一 Action Schema 检查字段、选项和范围。失败时可能返回
  `invalid_skill_parameters`、`invalid_parameter_binding` 或
  `invalid_action_parameters`，且不会生成或缓存预览。
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
  "message": "LLM 不可用，请检查 config.env 中的模型配置"
}
```

常见触发场景：

- `ai_chat.text` 为空
- 当前已有一轮 AI 规划正在处理中
- 当前规划 provider 所需配置未完成，导致 LLM 客户端不可用
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
- 当前规划 provider 已配置且可用
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
  "preview_id": "5d66bc2f-23e4-44f5-9159-49235fc8b5d4",
  "version": 12,
  "expires_at": "2026-07-29T03:22:00Z",
  "sequence": [],
  "skill_info": {},
  "validation": {"is_valid": true, "code": "valid"},
  "risk": {
    "level": "low",
    "reasons": [],
    "requires_acknowledgement": false
  },
  "requires_confirmation": true,
  "requires_risk_acknowledgement": false
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
- 项目不存在自动执行配置，前端不得将预览事件本身视为执行授权
- `sequence` 会注册到进程级 `CommandRuntime`，但此时还不会覆盖当前执行序列
- 前端应以 `ai_preview_ready.sequence` 作为唯一权威预览数据源
- 如果收到 `ai_skill_not_matched` 或 `error`，则视为本轮规划失败

### 11.2 确认执行 AI 规划 `ai_confirm`

请求：

```json
{
  "action": "ai_confirm",
  "preview_id": "5d66bc2f-23e4-44f5-9159-49235fc8b5d4",
  "version": 12,
  "risk_acknowledged": true
}
```

效果：

- 精确消费 `preview_id + version` 对应的预览并写入当前序列
- 立即开始执行

请求参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | `string` | 是 | 固定值 `ai_confirm` |
| `preview_id` | `string` | 是 | `ai_preview_ready.preview_id` |
| `version` | `integer` | 是 | `ai_preview_ready.version` |
| `risk_acknowledged` | `boolean` | 高风险时是 | 高风险动作必须严格为 `true` |

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

- `ai_confirm` 只确认 ID、版本、来源均匹配且仍处于 `pending` 的 WebSocket 预览。
- 过期返回 `preview_expired`；版本错误返回 `preview_version_conflict`；重复确认返回
  `preview_state_error`；缺少高风险确认返回 `risk_acknowledgement_required`。
- 预览确认采用单次消费；成功后相同引用不能再次执行。
- 执行进度事件与普通 `execute` 共用同一套 `step_started` / `step_completed` / `step_failed` / `execution_finished`。

### 11.3 取消 AI 规划 `ai_cancel`

请求：

```json
{
  "action": "ai_cancel",
  "preview_id": "5d66bc2f-23e4-44f5-9159-49235fc8b5d4",
  "version": 12
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

- `preview_id` 和 `version` 可省略；提供时必须精确匹配当前 WebSocket 预览。
- `ai_cancel` 只取消 WebSocket 来源且处于 `pending` 的预览，不会取消 GUI 来源的预览。
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
  "model": "qwen-turbo",
  "provider": "DASHSCOPE",
  "default_provider": "dashscope",
  "providers": ["openai", "deepseek", "dashscope", "minicpm"],
  "loaded_providers": ["dashscope"],
  "provider_health": {
    "dashscope": {
      "provider": "dashscope",
      "status": "healthy",
      "successful_calls": 12,
      "failed_calls": 1,
      "consecutive_failures": 0,
      "circuit_retry_after_s": 0.0,
      "last_failure_type": null
    }
  },
  "capabilities": ["chat", "stream_chat", "planning"],
  "chat_available": true,
  "chat_provider": "minicpm",
  "chat_model": "minicpm-o",
  "planner_available": true,
  "planner_provider": "dashscope",
  "planner_model": "qwen-turbo",
  "processing": false,
  "command_runtime": {
    "preview": {
      "preview_id": "5d66bc2f-23e4-44f5-9159-49235fc8b5d4",
      "version": 12,
      "state": "pending",
      "expires_at": "2026-07-29T03:22:00Z"
    },
    "execution": {
      "run_id": null,
      "state": "idle",
      "active": false,
      "origin": ""
    }
  },
  "has_preview": true
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `llm_available` | `boolean` | AI 规划 LLM 是否可用 |
| `api_key_set` | `boolean` | 默认 provider 所需配置是否已配置 |
| `model` | `string` | 当前规划模型名 |
| `provider` | `string` | 默认 provider |
| `default_provider` | `string` | `LLM_DEFAULT_PROVIDER` 解析后的 provider |
| `providers` | `array` | 当前 registry 已注册的 provider 名称 |
| `loaded_providers` | `array` | 当前进程已经懒加载实例化的 provider 名称 |
| `provider_health` | `object` | 各 provider 的运行时健康、成功/失败计数和熔断恢复等待；未加载 provider 为 `unknown` |
| `capabilities` | `array` | 聊天 provider 支持的能力 |
| `chat_available` | `boolean` | 聊天 provider 是否可用 |
| `chat_provider` | `string` | 当前聊天 profile 解析到的 provider |
| `chat_model` | `string` | 聊天模型 |
| `planner_available` | `boolean` | 规划 provider 是否可用 |
| `planner_provider` | `string` | 当前规划 profile 解析到的 provider |
| `planner_model` | `string` | 规划模型 |
| `processing` | `boolean` | 是否正在处理中 |
| `command_runtime` | `object` | WebSocket 来源的预览与统一执行状态；不会暴露 GUI 来源预览 |
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

### 12.4 相机错误

在未配置相机或相机不可用时，可能收到：

```json
{
  "event": "error",
  "code": "camera_failed",
  "error_source": "camera_error",
  "request_id": "camera-subscribe-1",
  "action": "subscribe_camera_frames",
  "message": "未配置任何相机",
  "cameras": []
}
```

---

## 13. LLM 聊天接口

该部分能力通过主控 WebSocket 暴露统一聊天接口。服务端调用 `src/llm/` 能力层，底层 provider 可以是 OpenAI-compatible HTTP，也可以是 MiniCPM Realtime WebSocket。前端不需要关心上游协议。

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
- 不是和具体模型 provider 建立永久长连接

### 13.3 发送聊天消息 `chat`

请求示例：

```json
{
  "action": "chat",
  "provider": "minicpm",
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
| `provider` | `string` | 否 | 覆盖本次调用使用的 provider，支持 `openai` / `deepseek` / `dashscope` / `minicpm` |
| `messages` | `array` | 与 `role/content` 二选一 | 聊天消息列表 |
| `role` / `content` | `string` / `string \| array` | 与 `messages` 二选一 | 单条消息简写 |
| `streaming` | `boolean` | 否 | 是否流式 |
| `route_to_interaction` / `robot_interaction` | `boolean` | 否 | 显式把用户文本路由到机器人交互入口 |
| `temperature` / `length_penalty` | `number` | 否 | 已声明的采样参数 |
| `max_tokens` / `max_new_tokens` / `image_max_slice_nums` | `integer` | 否 | 已声明的长度/图像参数 |
| `omni_mode` / `tts_enabled` / `tts` / `use_tts_template` / `enable_thinking` | `boolean` | 否 | 已声明的 provider 选项 |

未列出的 provider 扩展字段不会透传，而是返回 `invalid_payload`。需要新增
选项时，应先扩展服务端 action schema 和本文档。

说明：

- 调用前必须先 `chat_connect`
- 每次 `chat` 时服务端调用解析后的 provider 的 `stream_chat`：请求 `provider` > `GENERAL_CHAT_PROFILE.default_provider` > `LLM_DEFAULT_PROVIDER`
- 如果聊天 provider 是 MiniCPM，MiniCPM 上游 WebSocket 由 `src/llm/providers/minicpm_realtime.py` 内部维护
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

- `chat_data` 会携带对应 `chat` 请求的 `request_id/action`；当前上游聊天会话
  仍按单轮串行处理，在收到 `done` 或 `error` 前不要发送下一轮。
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
2. 调用 `authenticate`，成功后调用 `acquire_control`
3. 启动间隔小于控制租约的 `control_heartbeat`
4. 调用 `status`
5. 按需调用 `init_robots`、`init_body`
6. 通过 `add_to_sequence` 组装序列，或 `load_task`
7. 调用 `execute`
8. 监听 `step_started`、`step_completed`、`step_failed`、`execution_finished`
9. 页面退出时调用 `release_control`

### 14.3 AI 规划页面

推荐流程：

1. 先完成 `authenticate` 和 `acquire_control`
2. 页面初始化时调用 `ai_status`，确认 `llm_available`、`api_key_set`、`processing`、`has_preview`
3. 用户输入自然语言后调用 `ai_chat`
4. 收到 `ai_status_changed(status = "分析中...")` 后进入 loading 状态
5. 收到 `ai_skill_matched` 后展示“已匹配技能”和参数摘要
6. 收到 `ai_preview_ready` 后展示任务序列预览，并启用“确认执行”按钮
7. 若收到 `ai_skill_not_matched` 或 `error`，则结束本轮规划并给出失败提示
8. 用户确认后调用 `ai_confirm`
9. 执行阶段继续监听 `accepted`、`step_started`、`step_completed`、`step_failed`、`ai_execution_finished`、`execution_finished`
10. 用户取消预览则调用 `ai_cancel`

### 14.4 相机预览页面

推荐流程：

1. 调用 `authenticate`（相机帧订阅不要求控制权）
2. 调用 `camera_status`
3. 若 `available = true`，调用 `subscribe_camera_frames`
4. 将 `camera_frames` 渲染为图片
5. 页面销毁时调用 `unsubscribe_camera_frames`

### 14.5 MiniCPM 聊天页面

推荐流程：

1. 调用 `authenticate`（聊天会话不要求控制权）
2. 调用 `minicpm_status`
3. 调用 `chat_connect`
4. 用户发送消息时，先在本地插入用户气泡，再创建一条空的助手占位气泡
5. 调用 `chat`
6. 监听 `chat_data`
7. 对 `prefill_done`、`chunk`、`done`、`error`、`unknown` 按协议分别处理
8. 只有收到 `done` 或 `error` 后，才允许开始下一轮发送
9. 若同时收到 `minicpm_instruction`，应将其视为“指令识别提示”或“规划入口”，不要当聊天正文显示
10. 不再使用时调用 `chat_disconnect`

---

## 15. 错误处理手册

统一请求错误格式：

```json
{
  "event": "error",
  "api_version": "2.0",
  "code": "request_failed",
  "message": "...",
  "request_id": "execute-1",
  "action": "execute"
}
```

相机、遥操作和数据采集原有的专用错误事件会在网络协议边界统一为
`event: "error"`，同时通过 `error_source` 保留来源，例如
`camera_error`、`teleop_error`、`demo_record_error`。

稳定通用错误码：

| code | 含义 |
|---|---|
| `invalid_request` | 消息不是合法请求对象或 JSON |
| `invalid_request_id` | 请求 ID 格式或长度不合法 |
| `invalid_payload` | action payload 缺少必填字段、类型错误或包含未知字段 |
| `api_version_required` | 请求未声明 `api_version` |
| `unsupported_api_version` | 请求版本不在服务端支持列表中 |
| `unknown_action` | action 不存在 |
| `rate_limited` | 当前客户端请求频率超过配置上限 |
| `server_busy` | 全服务并发请求达到配置上限 |
| `request_failed` | 通用校验或业务前置条件失败 |
| `teleoperation_failed` | 遥操作请求失败 |
| `camera_failed` | 相机请求失败 |
| `data_collection_failed` | 数据采集请求失败 |
| `internal_error` | 未预期的服务端异常；响应不会包含内部异常详情 |

数据采集错误还会通过 `detail_code` 提供应用层稳定原因，例如
`invalid_state`、`session_start_failed`、`episode_start_failed`、
`episode_stop_failed`、`session_end_failed`、`recorder_protocol_error`
或 `cleanup_failed`。持久化阶段还可能返回 `insufficient_storage`、
`episode_conflict`、`data_integrity_failed`、`format_unavailable` 或
`persistence_failed`；保存失败时可能同时携带 `episode_id` 和 `frames`。

权限拒绝使用独立事件：

```json
{
  "event": "access_denied",
  "api_version": "2.0",
  "code": "authentication_required",
  "action": "execute",
  "request_id": "execute-1",
  "message": "此操作需要先完成认证"
}
```

常见权限错误码为 `authentication_not_configured`、
`invalid_credentials`、`authentication_required`、`control_required`、
`control_busy` 和 `control_lease_expired`。前端不得在日志或错误上报中附带
认证 token。

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
| AI 不可用 | 规划模型未配置或 LLM 不可用 |
| 相机不可用 | 未配置序列号或未检测到设备 |
| MiniCPM 不可用 | 网关连接失败 |

---

## 16. 运行约束与注意事项

- 当前只使用 `uv run robot-llm`；WebSocket 由 GUI 应用宿主管理，不再维护独立 Server 启动入口
- 仓库不再默认提交 `config.env`
- 相机和 MiniCPM 功能现在优先走主控制 WebSocket 的 `action` 路由
- 前端不要假设硬件一定在线
- 前端不要写死动作编辑表单，应该优先使用 `get_action_schema`

---

## 17. 最小可用前端示例

```javascript
const ws = new WebSocket("ws://localhost:8765");
const websocketToken = window.runtimeConfig.websocketToken;
const API_VERSION = "2.0";
let hasControl = false;
let requestSequence = 0;

function sendRequest(action, payload = {}) {
  requestSequence += 1;
  ws.send(JSON.stringify({
    api_version: API_VERSION,
    action,
    request_id: `${action}-${requestSequence}`,
    ...payload
  }));
}

ws.onopen = () => console.log("WebSocket 已连接");

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  switch (data.event) {
    case "connected":
      sendRequest("authenticate", { token: websocketToken });
      break;
    case "authenticated":
      sendRequest("acquire_control");
      break;
    case "control_acquired":
      hasControl = true;
      sendRequest("status");
      sendRequest("list_actions");
      break;
    case "control_released":
      hasControl = false;
      break;
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
    case "access_denied":
      console.error(`权限错误 ${data.code}`, data.message);
      break;
    default:
      console.log("其他事件", data);
  }
};

function executeDemo() {
  if (!hasControl) throw new Error("当前客户端没有控制权");
  sendRequest("execute", {
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
  });
}
```

---

## 13. 遥操作控制

遥操作（Teleoperation）模式允许通过 WebSocket 实时发送关节角度指令，直接控制机械臂运动。适用于主从遥操作场景，支持 50Hz 的高频指令流。

详细说明请参考：[遥操作说明文档](teleop.md)

### 13.1 teleop_init - 遥操作初始化

在启动遥操作前，将机械臂移动到指定的初始关节姿态。通常用于将从臂移动到与主臂相同的起始位置。

**请求**

```json
{
  "action": "teleop_init",
  "arm": "左",
  "joints": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3]
}
```

**参数说明**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `arm` | string | 是 | 机械臂选择，"左" 或 "右" |
| `joints` | array | 是 | 6个关节角度（度），[j1, j2, j3, j4, j5, j6] |

**响应**

```json
{
  "event": "teleop_init_completed",
  "arm": "左",
  "message": "初始化完成"
}
```

**错误响应**

```json
{
  "event": "error",
  "message": "关节角度数量错误：需要6个，实际5个"
}
```

**使用示例**

```javascript
// 从主臂采集当前关节角度
const masterJoints = [45.23, -30.15, 60.78, 0.0, 90.5, -45.3];

// 发送初始化指令，将从臂移动到主臂位置
ws.send(JSON.stringify({
  action: 'teleop_init',
  arm: '左',
  joints: masterJoints
}));

// 等待初始化完成
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.event === 'teleop_init_completed') {
    console.log('初始化完成，可以启动遥操作');
    // 启动遥操作模式
    ws.send(JSON.stringify({
      action: 'teleop_start',
      arm: '左'
    }));
  }
};
```

---

### 13.2 teleop_start - 启动遥操作模式

启动遥操作模式，准备接收实时关节指令。

**请求**

```json
{
  "action": "teleop_start",
  "arm": "左"
}
```

**参数说明**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `arm` | string | 是 | 机械臂选择，"左" 或 "右" |

**响应**

```json
{
  "event": "teleop_started",
  "arms": ["左"],
  "message": "遥操作模式已启动"
}
```

**双臂启动**

启动双臂遥操作（一次启动两个臂）：

```json
{
  "action": "teleop_start"
}
```

或指定臂列表：

```json
{
  "action": "teleop_start",
  "arms": ["左", "右"]
}
```

响应：

```json
{
  "event": "teleop_started",
  "arms": ["左", "右"],
  "message": "遥操作模式已启动"
}
```

**错误响应**

```json
{
  "event": "error",
  "message": "有任务正在执行，无法启动遥操作"
}
```

```json
{
  "event": "error",
  "message": "机械臂控制器未初始化"
}
```

**注意事项**

- 遥操作模式与任务执行模式互斥
- 启动前需要确保机械臂已连接
- 启动后需要持续发送 `teleop_joint` 指令
- 支持单臂启动、多臂启动、双臂启动

---

### 13.3 teleop_joint - 发送关节指令

发送关节角度指令，立即执行。支持 50Hz 高频发送。

**单臂请求**

```json
{
  "action": "teleop_joint",
  "arm": "左",
  "joints": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3],
  "grip": 856,
  "follow": false,
  "trajectory_mode": 0
}
```

**双臂请求**

一次发送双臂关节角度（推荐）：

```json
{
  "action": "teleop_joint",
  "joints": {
    "左": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3],
    "右": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3]
  },
  "grip": {"左": 856, "右": 0},
  "follow": false,
  "trajectory_mode": 0
}
```

**参数说明**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `arm` | string | 单臂时必填 | 机械臂选择，"左" 或 "右" |
| `joints` | array[float] 或 dict | 是 | 单臂时为6个关节角度数组，双臂时为字典 `{"左": [...], "右": [...]}` |
| `grip` | int 或 dict | 否 | 夹爪位置原始值（`0`=闭合，`1000`=完全张开）。单臂时为 int；双臂时为 `{"左": 值, "右": 值}`。仅在具体数值变化时触发，不阻塞关节指令流 |
| `follow` | boolean | 否 | 跟随模式，默认 `true`（高跟随） |
| `trajectory_mode` | int | 否 | 轨迹模式，默认 `0`（完全透传） |

**grip 参数说明**

- `0~1000` 范围，主臂串口原始值，读多少传多少
- `0`: 夹爪完全闭合
- `1000`: 夹爪完全张开
- 中间值（如 500）表示半开，值越大开口越大
- 仅在数值变化时触发执行，避免 50Hz 高频重复触发
- 夹爪动作异步执行，不阻塞关节指令流
- 支持单臂（int）和双臂（dict）两种传参方式

**joints 数组说明**

```javascript
joints: [j1, j2, j3, j4, j5, j6]
```

- `j1-j6`: 6个关节角度（度）
- 精度：0.001°
- 范围：根据机械臂型号，通常 ±180° 或 ±270°

**follow 模式说明**

- `false`: 普通跟随模式，机械臂平滑过渡（推荐，减少顿挫感）
- `true`: 高跟随模式，机械臂快速响应指令变化

**trajectory_mode 说明**

- `0`: 完全透传，指令立即执行（推荐）
- `1`: 平滑轨迹，机械臂进行轨迹规划

**响应**

正常情况下不返回响应，仅在执行失败时返回：

```json
{
  "event": "teleop_error",
  "message": "关节角度数量错误：需要6个，实际5个"
}
```

```json
{
  "event": "teleop_error",
  "message": "左臂未启动遥操作模式"
}
```

**双臂响应**

双臂模式下的错误响应：

```json
{
  "event": "teleop_error",
  "message": "部分臂执行失败: ['左']"
}
```
```

```json
{
  "event": "teleop_error",
  "message": "关节指令执行失败"
}
```

```json
{
  "event": "teleop_error",
  "message": "执行异常: ..."
}
```

**错误响应（未启动遥操作）**

```json
{
  "event": "error",
  "message": "未启动遥操作模式，请先发送 teleop_start"
}
```

**使用示例**

```javascript
// 启动遥操作
ws.send(JSON.stringify({
  action: 'teleop_start',
  arm: '左'
}));

// 50Hz 发送关节指令
const interval = setInterval(() => {
  // 从主臂采集关节角度
  const joints = getMasterArmJoints(); // [j1, j2, j3, j4, j5, j6]
  
  ws.send(JSON.stringify({
    action: 'teleop_joint',
    arm: '左',
    joints: joints,
    follow: true,
    trajectory_mode: 0
  }));
}, 20); // 50Hz = 20ms

// 停止遥操作
setTimeout(() => {
  clearInterval(interval);
  ws.send(JSON.stringify({
    action: 'teleop_stop'
  }));
}, 10000);
```

---

### 13.4 teleop_stop - 停止遥操作

停止遥操作模式，机械臂停止响应关节指令。

**单臂停止**

```json
{
  "action": "teleop_stop",
  "arm": "左"
}
```

**双臂停止**

停止所有臂（默认）：

```json
{
  "action": "teleop_stop"
}
```

或指定臂列表：

```json
{
  "action": "teleop_stop",
  "arms": ["左", "右"]
}
```

**响应**

单臂停止响应：

```json
{
  "event": "teleop_stopped",
  "arms": ["左"],
  "total_counts": {"左": 100},
  "message": "遥操作模式已停止"
}
```

双臂停止响应：

```json
{
  "event": "teleop_stopped",
  "arms": ["左", "右"],
  "total_counts": {"左": 100, "右": 100},
  "message": "遥操作模式已停止"
}
```

**注意事项**

- 停止后机械臂保持当前姿态
- 可以重新启动遥操作模式
- 建议在停止前先发送最后一个稳定姿态指令
- 支持单臂停止、多臂停止、双臂停止

---

### 13.5 遥操作完整流程示例

```javascript
class TeleopClient {
  constructor(ws) {
    this.ws = ws;
    this.teleopActive = false;
    this.interval = null;
  }
  
  start(arm = '左') {
    this.ws.send(JSON.stringify({
      action: 'teleop_start',
      arm: arm
    }));
    this.teleopActive = true;
  }
  
  sendJoints(joints) {
    if (!this.teleopActive) {
      console.error('遥操作未启动');
      return;
    }
    
    this.ws.send(JSON.stringify({
      action: 'teleop_joint',
      joints: joints,
      follow: true,
      trajectory_mode: 0
    }));
  }
  
  runLoop(jointStream, frequency = 50) {
    const dt = 1000 / frequency;
    let index = 0;
    
    this.interval = setInterval(() => {
      if (index < jointStream.length) {
        this.sendJoints(jointStream[index]);
        index++;
      } else {
        this.stop();
      }
    }, dt);
  }
  
  stop() {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
    
    this.ws.send(JSON.stringify({
      action: 'teleop_stop'
    }));
    this.teleopActive = false;
  }
}

// 使用示例
const ws = new WebSocket('ws://localhost:8765');
const client = new TeleopClient(ws);

ws.onopen = () => {
  // 启动遥操作
  client.start('左');
  
  // 模拟关节角度流（从主臂采集）
  const jointStream = [
    [45.0, -30.0, 60.0, 0.0, 90.0, -45.0],
    [45.1, -30.1, 60.1, 0.1, 90.1, -45.1],
    // ... 更多关节角度
  ];
  
  // 以 50Hz 运行遥操作循环
  client.runLoop(jointStream, 50);
};
```

---

### 13.5 性能指标

| 指标 | 数值 |
|------|------|
| **指令频率** | 50Hz（推荐） |
| **网络延迟** | <20ms（本地网络） |
| **关节精度** | 0.001° |
| **数据包大小** | ~100 bytes（JSON） |

---

### 13.6 安全注意事项

**当前实现（Phase 1）**

- ✅ 模式互斥：遥操作时禁止执行其他任务
- ✅ 关节数量验证：检查是否为6个关节角度
- ⚠️ **未实现**：关节限位检查
- ⚠️ **未实现**：速度限制检查
- ⚠️ **未实现**：心跳检测和超时停止

**后续增强（Phase 2）**

需要添加以下安全措施：

1. **关节限位检查**：每个关节的角度范围限制
2. **速度限制检查**：相邻指令的变化率限制
3. **心跳检测**：超过一定时间未收到指令则自动停止
4. **停止入口**：遥操作复用全局 `quick_stop` / `emergency_stop`，仍需补充心跳超时自动触发策略

**使用建议**

- 仅在安全环境下使用遥操作
- 确保主臂和从臂的运动空间无障碍物
- 建议先在模拟模式下测试
- 准备好紧急停止机制

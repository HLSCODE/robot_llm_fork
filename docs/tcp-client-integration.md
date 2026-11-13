# TCP 客户端接入指南

本文面向调用已保存流程的客户端开发者，适用于 Python、C#、Java、C++ 等支持 TCP Socket 和 JSON 的客户端。当前协议版本为 `1`，不是 WebSocket，也不是 HTTP。

服务端架构与部署限制见 [多协议任务服务](remote-task-service.md)，流程输出配置见 [流程节点输出](workflow-outputs.md)。

## 1. 启动服务与快速验证

仅在本机联调时，可以在已被主配置 include 的 `config/fragments/server.toml` 中设置：

```toml
[server]
security_enabled = false

[server.websocket]
enabled = false

[server.tcp]
enabled = true
host = "127.0.0.1"
port = 8766
```

主配置 `config/config.toml` 的 `include` 必须包含 `"fragments/server.toml"`；只创建子文件不会自动加载。主配置中的同名设置优先于子配置。

在两个终端分别运行：

```powershell
# 终端一：模拟模式启动，不控制真实机器人
uv run robot-llm --simulation

# 终端二：列出当前 Robot Profile 的已保存任务
uv run --env-file .env python examples/network/tcp-task-client.py

# 使用上一步返回的真实任务名称执行
uv run --env-file .env python examples/network/tcp-task-client.py --task 移液
```

示例客户端源码：[tcp-task-client.py](../examples/network/tcp-task-client.py)。它用于串行联调，不是支持多请求并发、自动心跳的完整客户端库。

非本机访问建议启用认证和 TLS：设置 `security_enabled=true`，在 `.env` 配置 `SERVER_AUTH_TOKEN`，并在 `[server.tcp]` 配置 `tls_certificate_path`、`tls_private_key_path` 和监听地址。客户端使用可信 CA 校验证书及主机名：

```powershell
uv run --env-file .env python examples/network/tcp-task-client.py --host robot.example.com --ca certs/ca.pem --task 移液
```

`127.0.0.1` 只能由服务端本机连接。不要通过关闭证书验证解决 TLS 错误，也不要向他人提供真实 Token。

隔离内网中如需无认证明文接入，可设置 `[server] security_enabled=false`、`[server.tcp] host="0.0.0.0"`，证书和私钥均留空。服务端会警告但不阻止启动。客户端连接服务端实际局域网 IP（不是 `0.0.0.0`），无需认证和 `--ca`，但仍需 `control.acquire` 才能执行任务。该认证开关同时影响 WebSocket；任何可访问端口的客户端都可申请控制权，请通过防火墙限制来源，不要开放公网。

## 2. 分帧与消息分发

- 建立 TCP 长连接，使用 UTF-8；每条消息是一个 JSON 对象，末尾必须追加换行字节 `\n`。
- 不要假设一次 `recv()` 就对应一条消息。持续缓存收到的字节，按换行拆分；一包可能包含多条消息，一条消息也可能跨多个包。拆分完整行之后再解码 UTF-8、解析 JSON。
- 不发送 UTF-8 BOM、长度头、HTTP 头或 WebSocket 握手。
- 默认请求帧上限为 65536 字节（包含末尾换行）；输出可能使响应大于该值，客户端不要把请求上限直接当作响应上限。
- 默认每连接每秒最多 30 条请求、300 秒未收到客户端消息会断开。服务端推送不等于客户端心跳。

每个请求包含以下公共字段，业务参数直接放在顶层，不使用 `params` 包装：

```json
{"version":1,"request_id":"client-001","command":"ping"}
```

`request_id` 必须是 1～128 个 ASCII 字母、数字或 `._:-`，建议使用 UUID。普通新请求使用新 ID；同一次执行在不确定是否受理时重试，必须保留原启动 ID。

同一连接会混合收到两类消息：

| 类型 | 识别方式 | 客户端处理 |
| --- | --- | --- |
| 请求响应 | 含 `request_id` 和 `ok` | 按 `request_id` 分发给对应请求 |
| 运行事件 | 含 `event` 和 `run_id` | 按 `run_id` 更新运行状态；没有请求级 `ok` |

**不能把“发请求后的下一行”始终视为该请求的响应。** 执行期间发送查询、心跳或取消时，下一行也可能是运行推送。正式客户端应使用单一读取循环统一分发消息，并为写入操作加锁，避免多线程写坏 JSON 行。

普通成功与错误响应：

```json
{"version":1,"request_id":"client-001","ok":true,"status":"ok"}
{"version":1,"request_id":"client-002","ok":false,"error":{"code":"authentication_required","message":"此操作需要先完成认证"}}
```

字段错误、未知字段、未知指令会被拒绝。无法识别请求 ID 时错误响应的 `request_id` 可能为 `null`；超长帧或连接级错误也可能直接断开。客户端必须处理 EOF 和超时。

## 3. 调用顺序与指令

标准顺序：连接 → 认证（启用时）→ 列出任务 → 获取控制权 → 启动任务 → 接收最终事件 → 释放控制权。

| command | 额外请求字段 | 说明 |
| --- | --- | --- |
| `ping` | 无 | 公开连通性检查，返回 `status="ok"` |
| `authenticate` | `token` | 对当前连接认证，返回 `authenticated` |
| `task.list` | 无 | 返回 `tasks` 名称数组，需认证或关闭认证 |
| `control.status` | 无 | 公开查询，返回 `lease`，无人持有时为 `null` |
| `control.acquire` | 无 | 获取共享控制租约；其他连接持有时拒绝 |
| `control.heartbeat` | 无 | 当前控制连接续租 |
| `control.release` | 无 | 释放当前连接的租约，返回 `released` |
| `task.run` | `task` | 持有控制权时启动已保存任务 |
| `run.status` | `run_id` | 查询同一操作身份的运行，不需要控制租约 |
| `run.cancel` | `run_id` | 持有控制权时请求取消同一操作身份的运行 |

关闭认证只省略 `authenticate`，**不省略 `control.acquire`**。重新连接必须重新认证、重新获取所需控制权。Token 优先读取 `SERVER_AUTH_TOKEN`，旧的 `WEBSOCKET_AUTH_TOKEN` 为后备；示例客户端在环境中存在 Token 时自动发送认证。

例如逐行发送：

```json
{"version":1,"request_id":"auth-001","command":"authenticate","token":"<实际密钥>"}
{"version":1,"request_id":"list-001","command":"task.list"}
{"version":1,"request_id":"control-001","command":"control.acquire"}
{"version":1,"request_id":"execute-001","command":"task.run","task":"移液"}
```

等待每个操作的成功响应，再进行依赖该操作的下一步。`task.list` 示例：

```json
{"version":1,"request_id":"list-001","ok":true,"tasks":["移液","液面检测"]}
```

只接受当前 Profile 已保存且允许远程执行的任务，不接受流程上传、文件路径或临时参数。服务端忙时不排队；不会隐式连接设备、回零或初始化设备。任务必须先满足服务端执行预检。

在 GUI 的“另存为流程”窗口勾选 **允许远程调用** 并保存，任务才会出现在客户端列表中；未勾选时按名称直接调用也会返回 `task_forbidden`。新任务和未包含该设置的旧任务默认关闭。打开已有任务再保存可修改此设置；取消勾选并覆盖原名即可撤销开放。该设置保存在流程文件顶层的布尔字段 `allow_remote`，不需要迁移旧文件。已受理任务不会因撤销此设置而自动停止，历史查询与同 ID 回执去重仍可使用。本地执行以及作为其他任务的子流程不受此开关限制。

左侧“已保存任务”列表使用网络连接图标表示允许远程调用、显示器图标表示仅本地，悬停可查看完整说明。远程图标表示任务自身已开放，不表示绕过认证、控制权、白名单或设备预检，也不表示网络服务当前已启动。

已有任务可选中后点击列表顶部的编辑图标，或右键选择“编辑任务属性…”，直接修改名称与远程调用开关，无需打开画布。改名不会复制任务或保存当前画布；与其他任务重名时拒绝覆盖。改名后客户端需使用新名称，配置中的名称白名单、欢迎任务等引用需自行核对；已插入其他流程的子流程快照不会被自动改写。

## 4. 受理、进度与最终结果

`task.run` 成功响应表示已受理，不表示动作完成：

```json
{"version":1,"request_id":"execute-001","ok":true,"run_id":"abc123","status":"accepted","duplicate":false,"steps":5}
```

保存 `request_id` 与 `run_id`，用于故障恢复和查询。重复提交可能返回已有运行的状态，不能假设响应状态始终是 `accepted`。

该连接随后自动接收 `run.status`、`run.progress`、`run.finished`。进度推送按约 200ms 检查状态，可能合并快速步骤，不保证逐节点完整回放。每个连接仅自动订阅最近一次 `task.run`；`run.status` 查询不会建立订阅。

最终成功事件示例（`liquid_height_mm` 仅演示未来测量动作配置导出后的业务数据，不代表当前内置液面算法）：

```json
{"version":1,"event":"run.finished","run_id":"abc123","task":"液面检测","status":"succeeded","execution_request_id":"execute-001","created_at":1794550776.94,"updated_at":1794550777.03,"result":{"code":"","operation":"","device_id":"","error_category":"","raw_error_code":"","outputs":{"liquid_height_mm":12.5},"outputs_complete":true,"message":""}}
```

客户端应同时判断：

1. 顶层 `status`：`succeeded`、`failed`、`cancelled`、`unknown` 是终态；`unknown` 需人工核对，不能视为成功。
2. `result.outputs_complete`：是否完整采集本次流程的输出；失败、取消可能保留部分输出。
3. `result.outputs`：仅含流程节点**显式配置的导出**，不是所有节点参数或全部内部结果。未配置导出时 `{}` 是正常结果；`outputs_complete=true` 不保证其中存在特定业务字段。
4. 业务字段是否存在、类型和范围是否符合双方约定。字段名、单位由流程配置和业务能力共同约定，不要仅凭流程成功就假定存在液面高度。

输出支持 JSON 对象、数组、字符串、数字、布尔值和 `null`。循环节点的收集模式可能返回数组，具体结构见 [流程节点输出](workflow-outputs.md)。目前只有已接入输出链路的动作提供数据，配置导出不会自动产生新的视觉测量能力。

例如客户端从终态消息读取：

```python
result = message.get("result", {})
outputs = result.get("outputs", {})
if message["status"] == "succeeded" and result.get("outputs_complete"):
    if "liquid_height_mm" in outputs:
        height = outputs["liquid_height_mm"]
        # 按双方约定继续校验数值类型、单位和合理范围。
```

## 5. 查询、取消和心跳

查询：

```json
{"version":1,"request_id":"query-001","command":"run.status","run_id":"abc123"}
```

查询响应含 `ok=true`、本次查询的 `request_id`，以及 `run_id`、`status`、`task`、`execution_request_id`、时间和 `result`。`execution_request_id` 才是原始启动 ID；不要与查询 ID 混用。可轮询直至终态，避免超过限流。

持有控制权时可取消：

```json
{"version":1,"request_id":"cancel-001","command":"run.cancel","run_id":"abc123"}
```

返回 `status="cancel_requested"` 只是收到取消请求，仍需等待终态。取消不是硬件急停，也不保证机械臂立即停稳。

需要长期保留控制权时发送 `control.heartbeat`，根据租约返回的 `expires_in_seconds` 在到期前续租，并持续处理异步推送。`ping` 不代替控制续租。租约到期、断线或释放控制权不会自动取消已受理任务；需要取消时先确认并取得控制权，再发取消。

## 6. 断线恢复与安全重试

- 断线、超时、未收到受理响应都不代表任务未启动；客户端本地等待超时也不是任务取消。
- 已知 `run_id`：重连、认证后调用 `run.status`。
- 未知 `run_id`：重连、认证、获取控制权后，用**原 `request_id` 和完全相同的任务名称**再次提交 `task.run`，利用去重取得原运行；不要换 ID 自动重跑。
- 同一操作身份下，同 ID 不同任务返回 `request_conflict`。同一 Token 的连接共享操作身份；关闭认证时也共享无认证身份，因此不同客户端的新执行 ID 必须避免冲突。
- 服务端重启会将未完成回执标为 `unknown`，不会自动恢复执行。必须先核对现场设备，不能自动重跑。
- 默认回执保留七天；超出保留期限不保证去重。回执不是对物理动作 exactly-once 的保证。

示例客户端查询已有运行或复用启动 ID：

```powershell
uv run --env-file .env python examples/network/tcp-task-client.py --run-id abc123
uv run --env-file .env python examples/network/tcp-task-client.py --task 移液 --request-id execute-001
```

## 7. 常见问题

| 现象或错误码 | 检查与处理 |
| --- | --- |
| 连接被拒绝 | 应用是否运行、日志是否显示 TCP 服务已启动、include 是否生效、地址端口是否正确、端口是否被占用 |
| 一直无响应 | 是否发送末尾 `\n`、是否误用 WebSocket、是否遗漏 `flush`、是否正确解析完整行 |
| `authentication_required` | 认证启用时先发送有效 Token；本地确需无认证时显式关闭配置 |
| `invalid_credentials` | 核对服务端与客户端环境中的 Token，修改服务端环境后重启应用 |
| `control_required` / `control_busy` | 获取控制权或处理其他连接占用；不能绕过租约 |
| `task_not_found` / `task_forbidden` | 使用 `task.list` 返回名称，核对 Profile、保存状态和 `task_allowlist` |
| `invalid_workflow` / `not_ready` | 按返回消息检查任务参数、Profile、设备依赖与健康状态 |
| `busy` | 当前有执行或资源占用，完成后再由业务方决定是否发起新执行 |
| `run_not_found` | 核对运行 ID、身份、当前 Profile、回执是否已过期 |
| `outputs={}` | 检查节点输出设置是否保存、动作是否实际产生该字段；任务成功不等于配置了输出 |
| 收到事件却没有 `ok` | 这是运行推送，按 `event`/`run_id` 分发，不当作请求失败 |

服务端错误堆栈只写日志，不发送给客户端。排查时记录请求 ID、运行 ID、时间及错误码，避免记录 Token。

# 多协议任务服务

## 架构与范围

TCP、WebSocket（未来可加入 HTTP）共享同一 `ApplicationServices`、设备运行时、执行管理器、远程控制租约及已保存任务服务。不得同时启动两个应用进程控制同一套机器人。

| 模块 | 职责 |
| --- | --- |
| `application/saved_tasks.py` | 协议无关的流程快照加载、编译、预检、提交、去重、运行归属及取消 |
| `persistence/task_run_journal.py` | SQLite 回执、运行历史、重启恢复标记；不启动任务 |
| `robot_server/contracts.py` | 公共请求上下文、认证授权与任务操作；不包含 socket、HTTP 或 JSON 分帧 |
| `robot_server/security/access_control.py` | 跨协议共享的身份及唯一控制租约 |
| `robot_server/tcp/` | UTF-8 JSON 分帧、请求校验、连接/发送限额和状态回报 |
| `robot_server/ws_server.py`、`controllers/`、`protocol/` | 保留现有 WebSocket 接入和 API 2.0；`execute_task` 已复用共享任务服务 |
| `bootstrap/auxiliary_services.py` | 已有的异步服务生命周期接口与后台事件循环，独立启动/关闭每个协议 |

共享应用服务不依赖网络类型。HTTP 实现时可将路由转换为 `ServerRequest`，调用 `TaskApi.dispatch`，用 `202` 表达受理、轮询或 SSE 表达状态，不需新建执行器或复制业务逻辑。当前不预建 HTTP 空目录。现有 WebSocket 文件及导入路径暂保留，避免把不相关的相机、AI、遥操作一起搬迁。

## 配置与启动

直接写在 `config/config.toml`，或将 `config/fragments/server.example.toml` 增量复制为 `server.toml` 后 include。主配置模板已默认引用 `fragments/server.toml`，全部网络服务配置集中在这里；`services.example.toml` 只维护数据采集、定位和执行，不再声明 `[server]`，无需依赖两者的 include 顺序。主配置同名字段仍拥有最高优先级。

已有本机文件不会自动覆盖：若旧 `services.toml` 仍有 `[server]`，请将其中的自定义值归并到 `server.toml` 后删除旧表，切勿直接用示例覆盖；新示例默认启用认证，应同时核对 `.env` 中的 Token。增量初始化只补缺失文件，不会修改现有 `config.toml` 的 include，旧入口需手动补上 `fragments/server.toml`。

```toml
[server]
security_enabled = true
task_allowlist = []

[server.websocket]
enabled = true
host = "127.0.0.1"
port = 8765

[server.tcp]
enabled = true
host = "127.0.0.1"
port = 8766
```

两个 enabled 独立控制：可仅 WS、仅 TCP、两者均启用或全部关闭，全部关闭不影响 GUI 本地使用。仍通过 `uv run robot-llm` 启动，网络服务在已有后台宿主内运行。端口绑定失败会记入对应服务的失败状态，不阻塞另一服务的启动。

`[server.websocket]`、`[server.tcp]` 是按字段展开的配置表，也支持原有 `[server] websocket_*` 和新增的 `tcp_*` 字段。相同文件内同一字段的两种写法冲突会报错；include 仍按字段覆盖。

`.env` 中设置 `SERVER_AUTH_TOKEN`（不要提交真实值）。非空时优先于旧 `WEBSOCKET_AUTH_TOKEN`，两个协议使用同一凭据。`security_enabled` 显式设置后同时控制两个协议；未设置时沿用旧的 `websocket_security_enabled`。因此旧配置默认保持原 WS 行为、TCP 默认关闭；启用 TCP 时建议明确设置 `security_enabled=true`。

非本机 TCP 监听必须启用认证和 TLS，并配置 `tcp.tls_certificate_path` / `tcp.tls_private_key_path`。不允许把不加密的明文 TCP 直接开放到局域网。TLS 最低 1.2。客户端必须校验证书，不要关闭验证。

当前单个 API Key 代表一个操作身份，不是多用户权限系统；持有同一 Key 的客户端重连或切换协议后可以查询该身份的运行。连接级控制租约仍互斥。GUI 执行、示教等通过现有执行/资源仲裁互斥，不经过远程租约。

## TCP 消息协议 v1

每条消息为一个 UTF-8 JSON 对象，以 `\n` 结束，可长连接；服务端处理拆包、粘包。默认消息上限 65536 字节、最多 16 个连接、每连接每秒 30 条请求、空闲 300 秒断开、单次发送超时 2 秒；均可配置。超长、不完整消息或空闲超时允许直接断开连接。

每个请求必须包含 `version=1`、`request_id`（1～128 位 ASCII 字母、数字、`._:-`）与 `command`，不接受未知字段。请求 ID 不应由服务端替客户端生成。业务错误使用 `ok=false` 与 `error.code/message`；不会返回服务端堆栈或凭据。

| command | 额外字段 | 权限 |
| --- | --- | --- |
| `ping` | 无 | 公开 |
| `authenticate` | `token` | 认证 |
| `control.status` | 无 | 公开 |
| `control.acquire` | 无 | 已认证 |
| `control.heartbeat` | 无 | 控制权所有者 |
| `control.release` | 无 | 控制权所有者 |
| `task.list` | 无 | 已认证 |
| `task.run` | `task` | 持有控制权 |
| `run.status` | `run_id` | 已认证，且同一操作身份 |
| `run.cancel` | `run_id` | 持有控制权，且同一操作身份 |

先 authenticate，再 control.acquire，再 task.run。任务名称来自 task.list，只支持当前 Robot Profile 已保存的流程；不接受路径、任意动作参数或上传流程。不隐式连接、回零、初始化设备，不修改编辑器画布，不绕过 Profile、参数、依赖和设备预检。忙时立即拒绝，不排队。

```json
{"version":1,"request_id":"auth-001","command":"authenticate","token":"<环境中配置的密钥>"}
{"version":1,"request_id":"control-001","command":"control.acquire"}
{"version":1,"request_id":"run-001","command":"task.run","task":"移液"}
```

受理响应（不代表执行成功）：

```json
{"version":1,"request_id":"run-001","ok":true,"run_id":"abc123","status":"accepted","duplicate":false,"steps":5}
```

受理响应先发送；其后在该连接自动推送运行状态变化、当前步骤及最终结果。`run.progress` 的 `result.progress` 包含从 0 开始的 step_index、step_name 和步骤事件。订阅按约 200ms 周期查询回执，可能合并短时间的中间步骤，不是完整事件回放；不阻塞机器人等待网络发送，不推送相机帧或每个采样点。每连接仅保留最近一次 task.run 的自动订阅，其他运行可查询；慢接收/断线不停止机器人。

```json
{"version":1,"event":"run.finished","run_id":"abc123","status":"succeeded","execution_request_id":"run-001","task":"移液","created_at":0,"updated_at":0,"result":{"message":""}}
```

`run.status` 响应的 `request_id` 对应本次查询，`execution_request_id` 才是原始启动请求，不能混用。最终状态包括 `succeeded`、`failed`、`cancelled`、`unknown`；失败的 result 包含可用的 code、message、operation、device_id、error_category、raw_error_code。原始堆栈只写服务端日志，不通过协议输出。`run.cancel` 是协作式任务取消，不是实体急停，需等到最终取消状态，不能把 `cancel_requested` 当作已经停止。

## 重试、断线和历史

启动请求以“操作身份 + request_id”去重，同一 ID 与同一任务名称返回原运行，不再次执行；同 ID 不同名称返回 `request_conflict`。请保持名称写法一致（包括是否带 `.workflow.json`）。单次连接丢失、发送超时或没有收到受理回包，不代表任务没有启动，禁止改 ID 自动重跑。

回执存储于 `data/profiles/<profile>/runtime/task-runs.sqlite3`（跟随数据根目录配置），只保存运行元数据与结果，不保存 Key。重启后未完成记录标记 `unknown`，不表示成功、失败或设备已经停稳，不自动恢复/重跑，需要人工核对真实设备。日志仍可按 run_id / request_id 关联。

默认历史上限 10000 条，保留期 604800 秒（七天）；新提交时清理超过期限的非活动记录，上限满时拒绝新请求，不提前丢弃未过期的去重记录。`task_history_max_records` / `task_history_retention_seconds` 可配置。超过保留期后不再保证同一 ID 去重，客户端始终应为新的业务执行生成新 ID。该回执保证不是跨设备的 exactly-once 事务，不能据此断言物理动作完成。

典型错误码：`authentication_required`、`invalid_credentials`、`control_busy`、`control_required`、`invalid_request`、`unsupported_version`、`task_not_found`、`task_forbidden`、`invalid_workflow`、`not_ready`、`busy`、`request_conflict`、`run_not_found`、`run_not_active`、`history_full`、`internal_error`。

`task_allowlist=[]` 表示允许所有当前 Profile 已保存任务；非空时任务列表与远程执行同时受限，不影响 GUI 本地任务编辑。

## 示例客户端

```powershell
uv run --env-file .env python examples/network/tcp-task-client.py
uv run --env-file .env python examples/network/tcp-task-client.py --task 移液
uv run --env-file .env python examples/network/tcp-task-client.py --run-id abc123
```

远程 TLS 增加 `--host <证书主机名> --ca <可信CA证书>`。客户端打印启动 request_id，可通过 `--request-id` 复用原值查询原任务受理结果；不会自动重试物理执行。

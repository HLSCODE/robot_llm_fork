# 运行日志与关联上下文

> 状态：当前实现  
> 最近更新：2026-08-03

## 1. 架构边界

`src/core/logging_config.py` 是进程日志配置的唯一入口。组合根在配置加载后调用一次，业务模块
只使用标准库 `logging.getLogger(__name__)`，不得自行创建文件 handler、决定日志目录或启动
第二套日志系统。

日志同时输出到两个目标：

- 控制台：面向开发和现场操作人员的可读文本。
- `application.jsonl`：每行一个 JSON 对象，供检索、归档和故障关联。

文件 handler 在本地时间午夜轮转，保留天数由配置决定。旧的
`log/application_YYYYMMDD.log` 文本格式不再生成，也不提供双写或读取兼容层；历史文件可按现场
数据保留策略单独归档或删除。

## 2. 配置

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `LOG_LEVEL` | `INFO` | 根日志级别：`DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `LOG_DIRECTORY` | `logs` | 日志目录；相对路径以项目根目录为基准 |
| `LOG_RETENTION_DAYS` | `14` | 每日轮转备份的保留数量，必须为正整数 |

这些值进入不可变 `LoggingSettings`。空目录、未知级别、非正保留天数或无法创建目录都会在启动
阶段显式失败，不回退到其他隐式路径。

## 3. JSON Lines 字段

每条文件日志稳定包含：

| 字段 | 含义 |
|---|---|
| `timestamp` | 带时区的 ISO 8601 时间 |
| `level` | 日志级别 |
| `logger` | Python logger 名称 |
| `message` | 已格式化消息 |
| `run_id` | 统一执行 ID；非执行日志为 `null` |
| `request_id` | WebSocket 请求 ID；非请求日志为 `null` |
| `operation` | 当前执行或请求操作 |
| `process_id` | 进程 ID |
| `thread` | 线程名称 |
| `exception` | 仅异常日志存在的完整堆栈文本 |

不得在消息或附加字段中记录密码、Token、API Key、完整凭据或未经脱敏的配置快照。

## 4. 上下文传播

- WebSocket 在通过请求 schema 校验后绑定 `request_id` 和 action；请求结束时在 `finally` 中恢复
  原上下文，因此并发协程不会串号。
- `ExecutionManager` 创建自己的工作线程，不能依赖调用线程自动传播上下文，因此 worker 入口
  显式绑定 `run_id` 和 `execution.run`，覆盖引擎、handler、设备调用及事件 listener 的同线程日志。
- 嵌套调用可以使用 `log_context(...)` 增加字段；退出作用域后必须恢复外层上下文。

日志关联不会改变 WebSocket 协议中的 `request_id/run_id`，两者使用同一业务标识，可从客户端
响应和执行事件直接定位到服务端文件日志。

## 5. 验证与运维

本地启动配置检查也会初始化日志：

```powershell
uv run --frozen --extra gui robot-llm --check-config
```

查看最新日志：

```powershell
Get-Content logs/application.jsonl -Tail 50
```

普通 CI 验证 JSON 格式、关联上下文的嵌套恢复、轮转 handler/保留数量、配置边界以及执行 worker
的 `run_id` 传播。真实磁盘容量、长期轮转和现场日志采集平台仍属于部署验收。

# 运行日志与关联上下文

> 状态：当前实现  
> 最近更新：2026-08-03

## 1. 架构边界

`src/observability/logging_config.py` 是进程日志配置的唯一入口。组合根在配置加载后调用一次，业务模块
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

## 6. 移液枪协议诊断

ADP ASCII 命令的日志包含 `command`、`request_hex` 和 `response_hex`，以便保留原始收发帧。
发送前记录 `ADP ASCII request`，传输异常记录命令码、错误分类和操作位置，
因此即使没有收到任何回复，也能定位超时的是动作指令还是完成状态查询。
所有 ASCII 命令统一遵守 `adp_command_interval_seconds`（默认 0.2 秒）的交换后间隔，
包括速度设置 `4/B` 后的 `n/p`。这是针对现场出现设置应答后约 25 毫秒就发送动作的
时序保护，不能据此认定漏答的根因已经确认。等待间隔记录为 `ADP command gap`。
串口返回短写时直接报告 `operation=write`，避免将不完整发送误报为设备无应答。
现场已观察到 `>01B6298\r\n` 和 `>01p0233DE\r\n` 格式的回复；驱动按 CRLF 读取完整帧，
并校验 ASCII、CRC、设备地址和命令码。按用户提供的 ADP 参考代码，吸液 `n`、吐液 `p`
（含全吐）只接受数据字段 `01`；`00` 表示忙碌未执行，`02` 表示容积超限未执行，
空字段、未知码和无效报文也会让当前动作失败。
收到 `01` 后，驱动发送 `>01dB819` 查询泵状态：`00` 时继续查询，只有 `01`
（到达预定位置）才返回成功，随后主流程才进入下一步。
单次状态查询发生读取超时时，只重试 `d` 查询，仍须收到完成状态；连续无回复则在
动作完成等待期限到达后失败。动作指令自身的应答超时不会自动重发。
`n/p` 应答超时会标记 `operation=motion_acknowledgement`，表示执行状态未知，
流程停止；不能仅凭随后查到泵空闲或到位就假定本次命令执行成功。
其他状态（包括堵塞、吸空报警）、CRC/格式错误以及非读取超时的通信异常会终止当前动作。
首次查询前及后续查询之间默认轮询等待 0.1 秒，同时满足上述 0.2 秒最小命令间隔，
实际发送间隔取两者较大值。单次动作完成等待上限 60 秒，
可通过 ADP 构造参数 `poll_interval_seconds`、`motion_timeout_seconds` 调整；
在途串口读取受串口超时约束，返回后再次检查完成等待期限。
`adp_timeout`（默认 5 秒）控制一次串口读写，`adp_max_retries` 控制打开串口时的重试次数，
它们不等同于动作完成超时或状态查询重试次数。
上述状态含义来自参考代码，仍需现场验证设备的实际应答及完成状态。
原始回复保留在 `response_hex` 日志中供诊断，完成确认另有 accepted/completed 日志。
不要据此自动重发吸液/吐液命令，以免重复执行。退枪头使用已知的 Modbus 协议，
校验 CRC、地址、功能码和完整寄存器回显。

移液枪在 `G` 命令收到符合上述规则的通信确认后才进入 `READY`；超时或协议错误时关闭串口并进入 `FAILED`。
主界面的“初始化移液枪”会重新打开串口并重新执行初始化，以便恢复失败连接。
程序退出时跳过移液枪的安全状态复位，仅关闭其串口，不发送 `G` 或退枪头命令。

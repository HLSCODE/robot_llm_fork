# 性能基准与回归预算

> 状态：当前实现  
> 最近更新：2026-08-03

## 1. 目标与边界

性能回归用于尽早发现核心纯软件路径的数量级退化，不替代功能测试，也不把共享 CI runner 当作
真实机器人性能验收环境。当前基准不连接机械臂、相机、串口、网络或外部 LLM，因此可以进入
Windows/Linux 普通质量矩阵。

以下项目不属于本门禁：

- 机械臂运动、停止响应、轨迹精度和协议往返时延。
- 相机帧率、视觉模型推理和 GPU 性能。
- GUI 绘制帧率、音频实时性和外部 LLM 首 Token 延迟。
- 真实磁盘、网络及多客户端压力测试。

这些指标必须在明确硬件、固件、模型、数据集和测试条件的专项验收中记录。

## 2. 当前基准

| 名称 | 每个样本的操作数 | Windows 首次门禁中位数 | 预算 |
|---|---:|---:|---:|
| `websocket_request_parse` | 20,000 | 50.004 ms | 500 ms |
| `action_parameter_validation` | 10,000 | 72.424 ms | 500 ms |
| `resource_lease_cycle` | 10,000 | 21.558 ms | 250 ms |
| `action_schema_snapshot` | 250 | 52.029 ms | 500 ms |
| `llm_golden_regression` | 25 | 13.018 ms | 250 ms |

表中的首次结果只用于解释预算量级，不是第二个配置源。唯一可执行预算位于
`data/regression/performance_budgets.json`。

## 3. 测量方法

`scripts/run_performance_benchmarks.py` 对每项基准执行：

1. 一次不计入结果的完整预热。
2. 按预算声明的批量操作数执行 5 个独立样本。
3. 使用 `time.perf_counter()` 测量每个批次。
4. 取中位数与 `max_median_ms` 比较，降低单次调度抖动的影响。
5. 任一项目超预算时进程返回非零状态，统一质量入口立即失败。

每个基准在计时批次结束后校验可观察结果，避免空循环、失败路径或资源泄漏被误认为性能提升。
预算 loader 使用 strict schema v1，拒绝未知/缺失字段、非有限阈值及可能造成无界运行的操作数和
样本数。

## 4. 使用方法

只运行性能回归：

```powershell
uv run --frozen python scripts/run_performance_benchmarks.py
```

同时生成机器可读报告：

```powershell
uv run --frozen python scripts/run_performance_benchmarks.py `
  --output performance-report.json
```

报告包含 Python 版本、平台、每次样本、中位数、预算和预算使用率。该文件是本地/CI 产物，不纳入
版本控制；CI 日志仍会输出同一份 JSON。

## 5. 预算维护规则

- 新增关键热路径时，同时新增 benchmark definition、预算和确定性测试；注册表与预算名称必须
  完全一致。
- 优化后可以在 Windows/Linux 均稳定通过的前提下收紧预算。
- 不得为了让 CI 通过而直接放宽预算。确需调整时，应记录机器差异、样本结果、影响和恢复计划。
- 功能语义变更必须先由 unit/contract/golden test 验证；性能结果不能证明行为正确。
- 发现回退时先用 profiler 定位热点，再决定优化、拆分预算或接受有依据的成本变化。

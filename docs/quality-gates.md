# 工程质量门禁

> 状态：当前实现  
> 最近更新：2026-08-03

## 1. 目标

项目使用同一个本地/CI 入口执行静态检查、自动化测试和离线规划回归，避免本地命令与
CI 配置长期漂移。普通质量门禁不连接机械臂、相机、串口或外部 LLM。

当前质量门禁覆盖：

1. `compileall`：验证 `src/`、`tests/` 和 `scripts/` 的 Python 语法。
2. Ruff：检查第一方 Python 代码中的语法错误、未定义名称、无效导入和基础风格错误。
3. Mypy：严格检查协议、路由、请求流控、运行时模型和 LLM 基础数据模型。
4. Pytest + coverage：运行 unit、contract 和 simulation 级测试，并强制全源码覆盖率阈值。
5. LLM golden regression：离线验证规划输出的 strict schema 和稳定错误分类。
6. Performance regression：对五条无 I/O 关键路径执行多样本中位数预算检查。
7. Wheel smoke：构建 wheel、检查关键模块、隔离安装并调用 `robot-llm --check-config`。

普通质量门禁同时在 Windows/Linux 执行；真实硬件验收仍是独立工作，不能用普通 CI 结果替代。

## 2. 本地使用

安装锁定的开发环境：

```powershell
uv sync --frozen --all-extras --group dev
```

执行与 CI 完全相同的门禁：

```powershell
uv run --frozen --group dev python scripts/run_quality_checks.py
```

开发过程中也可以单独执行：

```powershell
uv run --frozen pytest --cov --cov-report=term
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python -m src.llm.regression
uv run --frozen python scripts/run_performance_benchmarks.py --output performance-report.json
uv run --frozen python scripts/validate_package.py
```

依赖声明发生变化时，先显式更新 `uv.lock`，再使用 `--frozen` 验证：

```powershell
uv lock
uv sync --frozen --all-extras --group dev
```

## 3. 测试分层

| 层级 | 运行环境 | 目标 | 普通 CI |
|---|---|---|---|
| Unit | 无硬件、无网络 | 纯模型、状态机、校验器、算法和服务行为 | 必须 |
| Contract | fake/stub 边界 | WebSocket、设备能力、Provider 和持久化格式契约 | 必须 |
| Simulation integration | simulation runtime | 跨服务主要流程、取消、资源冲突和清理 | 逐步纳入 |
| Hardware acceptance | 受控真实设备 | 时延、限位、急停、恢复、精度和协议兼容 | 独立执行 |

测试名称必须描述可观察行为。协议公开 action、稳定错误码或 schema 发生变化时，应在同一
变更中更新相应的 golden contract；不保留旧协议的隐式兼容分支。

## 4. 静态检查范围

Ruff 当前检查统一入口、测试，以及已进入收敛主线的 action、application、core、
data collection、device runtime、execution、GUI、LLM、WebSocket 和 skill system。
全仓首次基线扫描仍发现供应商 SDK、旧设备/视觉/语音模块和联调脚本存在历史问题，因此这些
目录暂不伪装成已达标；应在对应模块完成问题清零后加入 `pyproject.toml` 的 Ruff `include`。
修改未纳入目录时仍需执行专项测试和硬件验收。

Mypy 先覆盖已完成收敛、类型边界较稳定的核心文件：

- WebSocket protocol、route registry 和 request limiter。
- 版本化 JSON、用户数据路径、启动配置校验和内置数据安装。
- 不可变领域 settings 和敏感配置边界。
- DeviceRuntime 的供应商无关模型。
- 设备错误分类、基础设施错误映射和 action result 错误 DTO。
- ExecutionRuntime 的状态、事件和结果模型。
- LLM 通用类型与确定性指纹。

检查使用显式文件清单，且不递归扩散到尚未纳入的历史导入模块；这保证门禁范围可验证，
不会用范围外错误淹没核心边界，也不代表被跳过模块已经通过类型检查。

新增同类核心模块时，应将其加入 `pyproject.toml` 的 `[tool.mypy].files`，修复类型错误后再
合并，不能通过大范围 `ignore` 或 `Any` 绕开边界设计。

## 5. CI 行为

`.github/workflows/quality.yml` 在 Windows/Linux、Python 3.12 上对 `main`、`master` 的 push
和所有 pull request 执行。CI 使用 `uv.lock` 的冻结版本，依赖声明与锁文件不一致会直接失败。

质量矩阵执行同一个 `scripts/run_quality_checks.py`。可选依赖矩阵则在两个平台分别隔离安装
`gui`、`server`、`hardware`，再运行 `scripts/validate_optional_extra.py`；冒烟过程只验证导入、
默认构造和依赖闭包，不访问串口、相机、机械臂、网络或生产数据。隔离安装可以防止某个 extra
遗漏的依赖被其他 extra 意外补齐。

## 6. 覆盖率策略

`pyproject.toml` 的 `[tool.coverage.*]` 是覆盖率范围和阈值的唯一配置源。当前规则为：

- `src/` 中所有第一方源码默认纳入统计。
- 排除自动生成的 RealMan ctypes 绑定 `src/arm_sdk/rm_ctypes_wrap.py`；该文件应通过供应商 SDK
  兼容性和真实硬件验收验证，不把生成代码行数计入业务单测目标。
- 初始总覆盖率阈值为 44%；建立门禁时的实测基线为 44.42%。任何变更低于阈值都会失败。
- 阈值只能随测试补齐逐步提高；如需降低，必须在主重构计划记录原因、影响和恢复任务。

覆盖率 XML 输出为 `coverage.xml`，仅作为本地或 CI 产物，不纳入版本控制。覆盖率通过只说明
自动化路径满足当前基线，不代表 simulation integration 或 hardware acceptance 已完成。

真实硬件、外部网络、生产密钥和用户数据不进入普通 CI。需要这些资源的验收必须记录设备、
固件、配置、标定版本、测试条件和结果，并单独归档。

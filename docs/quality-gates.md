# 工程质量门禁

> 状态：当前实现  
> 最近更新：2026-08-07

## 1. 目标

项目使用同一个本地/CI 入口执行静态检查、自动化测试和离线规划回归，避免本地命令与
CI 配置长期漂移。普通质量门禁不连接机械臂、相机、串口或外部 LLM。

当前质量门禁覆盖：

1. `compileall`：验证 `src/`、`tests/` 和 `scripts/` 的 Python 语法。
2. Ruff：检查全仓第一方 Python 代码中的语法错误、未定义名称、无效导入和基础风格错误。
3. Mypy：严格检查全部手写 `src/` 与 `scripts/` Python 代码。
4. Pytest + coverage：运行 unit、contract 和 simulation 级测试，并强制全源码覆盖率阈值。
5. LLM golden regression：离线验证规划输出的 strict schema 和稳定错误分类。
6. Performance regression：对九条无 I/O 关键路径执行多样本中位数预算检查。
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
| Simulation integration | simulation runtime | 跨服务主要流程、取消、资源冲突和清理 | GUI/执行核心流程必须，其他领域逐步纳入 |
| Hardware acceptance | 受控真实设备 | 时延、限位、急停、恢复、精度和协议兼容 | 独立执行 |

测试名称必须描述可观察行为。协议公开 action、稳定错误码或 schema 发生变化时，应在同一
变更中更新相应的 golden contract；不保留旧协议的隐式兼容分支。

## 4. 静态检查范围

Ruff 通过 `ruff check .` 检查全仓第一方 Python 代码，包括 `src/`、`scripts/` 和 `tests/`；
不再使用目录或文件白名单隐藏历史问题。

Mypy 的 `[tool.mypy].files` 固定覆盖全部 `src/` 与 `scripts/`。唯一排除项是 Qt 资源编译器生成的
`src/gui/resources_rc.py`；该文件应由资源源文件重新生成，不得手工维护。新增手写模块会自动进入
类型门禁，不需要再维护文件清单。

2026-08-07 的范围扩展验证从 72 个文件的 570 个 Mypy 错误收敛到默认检查
286 个 `src` + `scripts` 文件且 0 错误。文件数是当次验证快照，门禁范围始终由上述目录而非
固定数量定义。

第三方库缺少类型存根时，`ignore_missing_imports` 只容许导入边界缺少声明。动态返回值必须在
对应 adapter、provider 或 SDK wrapper 的窄边界完成校验和类型收窄，不能让 `Any` 扩散到应用层、
领域层、执行运行时或表现层，也不能通过新增目录排除、文件排除或大范围 `ignore` 绕过检查。

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
- `src/` 中不再保存 RealMan 生成绑定或 DLL 副本；`robotic-arm` 依赖是 SDK、
  ctypes 绑定和平台原生库的唯一来源，覆盖率只统计项目第一方代码。
- 初始总覆盖率阈值为 44%；GUI simulation smoke 纳入后实测为 52.35%，门禁已提升到 50%。
  任何变更低于当前阈值都会失败。
- 阈值只能随测试补齐逐步提高；如需降低，必须在主重构计划记录原因、影响和恢复任务。

覆盖率 XML 输出为 `coverage.xml`，仅作为本地或 CI 产物，不纳入版本控制。覆盖率通过只说明
自动化路径满足当前基线，且 GUI/ExecutionRuntime 核心 simulation integration 已纳入；这仍不
代表视觉、语音、外部服务 simulation 或 hardware acceptance 已完成。

真实硬件、外部网络、生产密钥和用户数据不进入普通 CI。需要这些资源的验收必须记录设备、
固件、配置、标定版本、测试条件和结果，并单独归档。

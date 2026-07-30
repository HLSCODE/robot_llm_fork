# 工程质量门禁

> 状态：当前实现  
> 最近更新：2026-07-30

## 1. 目标

项目使用同一个本地/CI 入口执行静态检查、自动化测试和离线规划回归，避免本地命令与
CI 配置长期漂移。普通质量门禁不连接机械臂、相机、串口或外部 LLM。

当前质量门禁覆盖：

1. `compileall`：验证 `src/`、`tests/` 和 `scripts/` 的 Python 语法。
2. Ruff：检查第一方 Python 代码中的语法错误、未定义名称、无效导入和基础风格错误。
3. Mypy：严格检查协议、路由、请求流控、运行时模型和 LLM 基础数据模型。
4. Pytest：运行 unit、contract 和 simulation 级测试。
5. LLM golden regression：离线验证规划输出的 strict schema 和稳定错误分类。

覆盖率阈值、Linux 测试矩阵和真实硬件验收仍是独立后续工作，不能用普通 CI 结果替代。

## 2. 本地使用

安装锁定的开发环境：

```powershell
uv sync --frozen --group dev
```

执行与 CI 完全相同的门禁：

```powershell
uv run --frozen --group dev python scripts/run_quality_checks.py
```

开发过程中也可以单独执行：

```powershell
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python -m src.llm.regression
```

依赖声明发生变化时，先显式更新 `uv.lock`，再使用 `--frozen` 验证：

```powershell
uv lock
uv sync --frozen --group dev
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
- DeviceRuntime 的供应商无关模型。
- ExecutionRuntime 的状态、事件和结果模型。
- LLM 通用类型与确定性指纹。

检查使用显式文件清单，且不递归扩散到尚未纳入的历史导入模块；这保证门禁范围可验证，
不会用范围外错误淹没核心边界，也不代表被跳过模块已经通过类型检查。

新增同类核心模块时，应将其加入 `pyproject.toml` 的 `[tool.mypy].files`，修复类型错误后再
合并，不能通过大范围 `ignore` 或 `Any` 绕开边界设计。

## 5. CI 行为

`.github/workflows/quality.yml` 在 Windows/Python 3.12 上对 `main`、`master` 的 push 和所有
pull request 执行。CI 使用 `uv.lock` 的冻结版本，依赖声明与锁文件不一致会直接失败。

真实硬件、外部网络、生产密钥和用户数据不进入普通 CI。需要这些资源的验收必须记录设备、
固件、配置、标定版本、测试条件和结果，并单独归档。

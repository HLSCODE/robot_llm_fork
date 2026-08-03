# GUI 启动生命周期与 Simulation Smoke

> 状态：当前实现  
> 最近更新：2026-08-03

## 1. 启动状态

`GuiStartupLifecycle` 是 MainWindow 启动初始化的唯一状态源：

```text
not_started
    ↓ begin
waiting_for_speech
    ↓ speech ready / bounded timeout
initializing_hardware
    ├─ success → ready
    └─ exception → failed

任意状态 ── close ──→ closed
```

重复信号和重复初始化不会再次创建设备。非法内部转换显式失败，不再通过
`_startup_initialization_started/_startup_hardware_initialized` 两个布尔值组合推断状态。

启用语音 runtime 时，MainWindow 不再在构造函数中运行嵌套 `QEventLoop`。窗口构造立即返回，
语音 ready signal 或单次超时在 Qt 事件循环中继续硬件初始化。超时必须是正数，并在启动配置
校验阶段检查。

## 2. 自动化 Smoke 范围

`tests/test_gui_simulation_smoke.py` 使用 `QT_QPA_PLATFORM=offscreen`，构造真实
`ApplicationServices(simulation=True)` 和真实 `MainWindow`，不替换 ExecutionBridge 或
MainWindow 为 mock。

当前验证：

- simulation 模式显示正确，GUI 与 AI 面板共享同一模式和服务容器。
- 机械臂、身体轴和移液枪使用 fake adapter 完成启动，不导入或连接真实设备。
- CompositionService 变更通过 Qt bridge 更新动作序列。
- 开始、暂停、恢复和停止按钮驱动唯一 ExecutionRuntime。
- 取消产生唯一 CANCELLED 终态，GUI 收到终态日志，资源租约完全释放。
- speech ready 使用异步 signal 推进 waiting → initializing → ready。
- 窗口关闭停止 GUI 自有 timer/runtime adapter 并进入 closed；设备关闭仍由应用宿主统一负责。

## 3. 本地运行

```powershell
$env:QT_QPA_PLATFORM="offscreen"
uv run --frozen --extra gui --group dev `
  pytest -q tests/test_gui_simulation_smoke.py
```

完整门禁会在 Windows/Linux GUI extra 环境执行同一测试。

## 4. 非覆盖范围

Offscreen smoke 不证明真实硬件可用，也不验证像素级布局、触摸体验、GPU 渲染、摄像头画面、
语音设备或物理急停。上述能力分别进入视觉 fixture、人工 GUI 验收和真实硬件 acceptance；不得
通过扩大 fake 行为把硬件未验证项标记为完成。

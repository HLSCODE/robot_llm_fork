# GUI 闪退诊断

## 已修复的屏幕生命周期问题

在 Windows / PySide6 6.11.1 环境中，原生跟踪确认共享 `QScreen` 曾经先进入
Shiboken 包装对象回收路径，随后 Qt 的窗口坐标换算调用 `QScreen::geometry()`
访问空指针。独立 offscreen 测试也能在没有机器人 SDK 的情况下复现。

弹框、启动卡片和下拉菜单不再调用 `QWidget.screen()`，也不改用同样存在所有权
关联风险的 `QWindow.screen()`。统一通过 `screen_geometry.available_screen_geometry()`
使用应用级 `screenAt()` / `primaryScreen()` 查询，只向调用方返回 `QRect` 副本。
按全局逻辑坐标选择屏幕，位置不在可用屏幕上时回退到主屏幕；无屏幕、退出期间或
非 GUI 线程返回 `None`。不手动修改 QScreen 的父对象或所有权，也不全局禁用 GC。

回归覆盖整数/文本弹框接受和取消、启动卡片及下拉菜单的反复关闭和垃圾回收，
验证屏幕在应用运行期间仍有效，并在 QApplication 正常退出时释放。原生对象失效会
污染后续 GUI 测试，因此每种回收场景都在独立 offscreen 子进程运行，不连接设备：

```powershell
uv run --frozen --group dev --extra gui python -m pytest -q tests/test_gui_screen_geometry.py tests/test_gui_screen_lifetime.py tests/test_crash_diagnostics.py
```

此修复针对已确认的所有权问题，不代表排除了所有可能的原生故障。实际多屏、显示器
热插拔及长时间实机运行仍需受控验证；GUI 闪退不等于机械臂已经安全停止。

## Python 故障记录的线程范围

普通 Python 异常仍把完整异常堆栈写入日志文件，不输出到终端。

Windows 上由本项目安装的 `faulthandler` 自动故障处理只输出当前 Python 线程。
这是因为 Windows 的异常回调也可能观察到可恢复的 COM first-chance 异常，
CPython 3.12 在此回调中遍历其他线程的 Python 栈曾发生二次访问违规。
Linux 保留自动全线程记录；显式调用 `CrashDiagnostics.dump_threads()` 仍在
Python/GIL 上下文中生成全线程快照。CDB / ProcDump 的完整原生线程转储不受影响。
自动故障回调与显式调用的线程状态处理可参考
[CPython 3.12.12 实现](https://github.com/python/cpython/blob/v3.12.12/Modules/faulthandler.c#L152-L237)。

启动诊断中的 `fatal_threads=current/all/external` 分别表示当前线程、全部线程、
由外部组件管理。如果 pytest、`PYTHONFAULTHANDLER` 或 `-X faulthandler` 已先启用
故障处理器，本项目不会替换其文件或策略；外部策略需要由其启用方调整。

`fatal-*.log` 中出现 COM 异常不一定代表进程退出，应结合后续应用日志、退出状态及
未处理异常转储判断。正常启动命令、日志路径和设备配置均无需改变。

## 录制窗口的应用侧缓解

录制启动后复用同一个 `open()` 弹框，异步切换准备、录制、保存和命名状态；录制过程不再调用 `exec()` 或在 GUI 线程等待 SDK。创建方式选择及已有文件导入仍使用原有选择框。

窗口由主窗口持有，完成后断开完成回调并延迟销毁；一次只允许一个录制窗口。后台调用期间关闭窗口会先等待调用完成再处理取消，不强制终止 SDK 线程。保存成功后取消命名只放弃动作创建，不删除轨迹。保存失败可取消并释放录制会话，取消失败则保留窗口供重试。

主窗口关闭时若存在录制窗口，会先请求结束录制并暂缓关闭；录制窗口退出后可再次关闭主窗口。强制结束进程不能保证硬件清理。

窗口居中前检查屏幕有效性，记录 `screen.added/removed` 与 `dialog.screen_changed`。这些措施降低应用侧重入风险，不保证能够阻止 Qt 内部的空屏幕指针访问；仍需保留转储采集并进行实机复测。

## Windows 原生转储（需手动启用）

Python 堆栈只能看到进入 Qt 的位置，不能显示实际故障 DLL 内的调用栈。
从 [Microsoft ProcDump 官方页面](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump) 下载解压后，启动应用，在另一个 PowerShell 中执行：

```powershell
./scripts/watch_native_crash.ps1 -TargetProcessId 12345 -ProcDumpPath 'C:/Tools/ProcDump/procdump64.exe'
```

把 `12345` 换成本次 `diagnostics-<PID>-...jsonl` 的 PID，不是 uv 或终端 PID。核对脚本显示的进程路径后输入 `YES`，按 ProcDump 提示确认许可，等待监控就绪后再复现。脚本不会下载工具、自动接受许可或注册全局调试器。不要在机器人危险运动过程中附加调试器：捕获时可能暂停进程，须保证独立硬件安全停止手段可用。

使用 `-ma -e -n 1` 捕获一次未处理异常的完整内存转储，默认输出到 `logs/crash/native/`；Ctrl+C 停止监控。普通已处理的 first-chance 异常不会触发转储。若程序被强杀或未发生未处理异常，不保证生成文件。调试器也可能改变故障时序。

完整转储可能很大，且包含凭据、图片和进程内存，**不要提交版本库或公开上传**。转储用于在 WinDbg 中分析异常线程、故障模块和原生调用栈；缺少 SDK 符号时可能只能定位 DLL 与偏移。

新增阶段包括 `dialog.created`、`dialog.exec.begin/end`、`dialog.done.begin/end`、`dialog.destroyed`，记录唯一实例标识、事件循环嵌套层级、返回后的 C++ 对象有效性。阶段日志带 Windows 原生线程 ID 和单调时钟，可与转储线程对应；启动日志包含解释器路径及 Qt/PySide、Shiboken、SDK 包版本。销毁回调不访问已销毁的对象。

正常启动 GUI 时自动启用，不需要连接机器人或修改配置。仅增加诊断，不改变轨迹保存、回放或 Qt 对象销毁策略。

## Windows 屏幕原生析构跟踪（需手动启用）

当转储显示 `QScreen::geometry` 空指针，但不知道屏幕对象为何失效时，使用
`scripts/watch_qt_screens.ps1` 提前跟踪原生生命周期。它不修改正常启动行为、
不向 GUI 注入回调、不修改对象所有权，也不启用系统级调试器或 PageHeap。

### 先验证本机跟踪能力

```powershell
./scripts/watch_qt_screens.ps1 -Launch Probe
```

脚本会尝试从 PATH、Windows Debugging Tools、已安装的 Microsoft WinDbg 包中查找
`cdb.exe`。找不到时传入 `-CdbPath 'C:/Tools/Debuggers/x64/cdb.exe'`。
探针只创建一个 Qt 应用并在约半秒后正常退出，不读取项目配置、不加载 SDK、不连接硬件。
每次运行都需核对提示并输入 `YES`，没有自动确认开关。

探针成功必须看到 `Probe verified`：脚本会核对同一 PID、同一屏幕地址的实际创建及析构记录，
并确认虚析构断点已安装。仅出现 `CONFIGURED` 或 pending 断点不算验证成功。

### 从启动开始跟踪（推荐）

```powershell
# 全模拟启动，不连接真实硬件。
./scripts/watch_qt_screens.ps1 -Launch Simulation

# 按当前配置启动，可能初始化/使能真实设备；只在无运动、独立安全停止就绪时使用。
./scripts/watch_qt_screens.ps1 -Launch Hardware
```

可用 `-PythonPath` 指定已安装 GUI 依赖的 x64 Python，默认使用项目 `.venv/Scripts/python.exe`；
可用 `-ConfigPath` 指定已有配置（Probe 不接受该选项）。不会运行 `uv sync` 或更新依赖。
`Hardware` 不强制覆盖配置中的模拟模式，只是不额外传入 `--simulation`。

Windows 虚拟环境的 `python.exe` 可能只是转发程序，因此启动模式跟踪它创建的子进程，
并在每个子进程的初始断点重新安装跟踪。日志中的 PID 可区分转发程序与实际 GUI 进程。
这也意味着该启动模式下其他新建子进程会受到调试器影响。

### 附加到已运行的 GUI

```powershell
./scripts/watch_qt_screens.ps1 -TargetProcessId 12345
```

PID 使用应用诊断日志中的真实 Python PID，不要选择 uv、终端或转发程序。
脚本会显示可执行文件路径、验证 x64 架构，并在确认后再次核对进程身份。
附加只能记录此后的销毁，无法补回之前的创建/释放历史。此模式不跟踪子进程。

**不要对同一 PID 同时运行 ProcDump 和本跟踪器。** 先正常退出已有调试器。
停止跟踪时，在跟踪终端按 `Ctrl+C`，等 CDB 提示符出现后输入 `qd`，分离而不结束目标进程。
脚本使用 `-pd` 作为额外的分离保护，但不应以关闭终端代替正常分离。
断点和转储都会暂停目标，即使记录后自动继续也会改变时序；禁止在危险运动中附加或操作调试器。

### 记录内容与文件

每次生成独立的 `logs/crash/native/qt-screens-<时间>-<唯一标识>/`：

- `native-screen.log`：屏幕创建、屏幕移除、主屏幕变化、应用退出/析构，以及屏幕实际析构的原生栈。
- `session.json`：运行方式、解释器、调试器参数、结束状态与事件计数。
- `trace.cdb`、`screen-vtable.cdb`：此次实际使用的调试命令，便于复核。
- `unhandled_*.dmp`：发生未处理访问违规、回调异常、快速失败或堆损坏时尝试生成的完整转储；正常退出不生成。

事件包含十进制 Windows PID/TID、十六进制对象地址、时间及最多 48 层原生栈。
`QT_SCREEN_CTOR` 同时记录 `QScreen` 与平台屏幕地址，便于关联平台移除事件。
堆栈和寄存器仅写日志，终端保留调试器提示、错误和警告；不经 GUI 错误通知。
未处理异常记录后以未处理状态继续交给系统，不把原生故障伪装成执行成功。

重点查找真正独立输出的 `QT_SCREEN_VIRTUAL_DTOR pid=...` 或 `QT_SCREEN_DTOR pid=...`，
而不是断点配置行中包含的同名字符串。对照同地址的创建记录、应用析构和平台移除事件，
区分正常退出与运行中提前销毁。没有 Qt 私有调试符号时，栈可能显示最近导出符号加偏移；
这不是实际私有函数名，不能仅按该名字判断责任模块。

### 为什么还需要虚析构入口

在当前 Qt 优化构建中，虚调用的 deleting destructor 可内联析构体，绕过导出的
`QScreen::~QScreen`。因此脚本同时跟踪导出析构函数，以及由 `QScreen` 虚函数表解析的实际虚析构入口。
定位依据是 MSVC x64 的 QObject ABI，不是某个 Qt 版本的固定代码地址：先核对前三个表项
分别为 `metaObject`、`qt_metacast`、`qt_metacall`，再对第四项安装断点。
此入口可能显示为不相关导出符号的偏移，需结合表项来源解释。

仅支持 Windows x64 / Qt 6；架构不符会拒绝启动，虚表布局不符会记录
`QT_SCREEN_VTABLE_UNSUPPORTED`，此时不要认为虚析构跟踪已生效。
普通信号函数也可能被编译器内联，缺少某一条退出/移除信号记录本身不能证明异常。

为避免 CDB 命令文件编码和解释器转义问题，输出目录必须为 ASCII 路径，支持空格，
但不接受引号、分号、美元符号和反引号。项目路径含中文时，可指定
`-OutputDirectory 'C:/Temp/robot-screen-trace'`。完整转储可能包含密钥和图像，仍须本地保管、禁止提交。

使用 `-DryRun` 只查看目标、参数和命令，不创建文件、不启动调试器、不附加进程。
这套工具补齐证据，不是闪退修复；没有捕获到异常销毁不等于已经排除生命周期问题。

调试器机制参考：[断点及自动命令](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/bp--bu--bm--set-breakpoint-)、
[区分日志与终端输出](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/-outmask--control-output-mask-)、
[分离调试会话](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/ending-a-debugging-session-in-cdb)。

## 文件位置

位于配置的日志目录（默认 `logs`）：

- `application.jsonl`：原有应用日志，异常完整堆栈仍只写文件。
- `crash/diagnostics-<PID>-<唯一标识>.jsonl`：Python 未捕获异常、线程异常、析构等不可抛出异常、Qt 消息及关键操作阶段。
- `crash/fatal-<PID>-<唯一标识>.log`：进程环境头，以及 faulthandler 能捕获的致命错误的 Python 线程堆栈；Qt fatal 消息也会触发线程堆栈记录。

诊断详情不输出到终端，不经过 GUI 通知，避免递归触发界面故障。每次启动使用独立文件，文件写入随日志调用刷新；并非断电级持久化保证。诊断文件不轮转、不自动删除，请定期归档或清理已经退出进程的旧文件，勿删除运行中进程的文件。

## 轨迹命名保存链路

命名保存过程通过 `request_id` 关联；动作创建后另有 `action_id`，文本弹框与后台操作分别带 `dialog_id`、`worker_id`。同时记录进程 ID 和线程名，不记录输入文本或完整动作参数。

阶段顺序：

1. `trajectory.name.begin` → `text_dialog.construct` → `text_dialog.exec.begin`。
2. `text_dialog.accept_clicked` → `text_dialog.exec.end` → `text_dialog.read.end` → `trajectory.name.end`。
3. `trajectory.create.begin` → `action.validate.begin/end`。
4. `action.persist.begin/end`：动作库写入调用前后。
5. `action.notify.begin` → `action_list.refresh.begin/end` → `action.notify.end` → `trajectory.create.end`。

录制的后台 SDK 调用另记录 `recording.worker.begin/end/failed` 和 `recording.wait.begin/end`，失败记录完整异常堆栈。

再次闪退时，请提供故障时间、上述同一次启动的三个日志文件，以及轨迹目录是否已生成、动作库是否包含对应动作。最后一个 begin 没有对应 end 可缩小故障范围，但不能独立证明该阶段就是崩溃根因。

## 边界

当前捕获覆盖 GUI 启动、运行和显式关闭流程，退出该流程后恢复原有诊断钩子。解释器最终清理、强制终止、断电以及部分原生崩溃不一定能留下堆栈；日志不能代替 Windows dump 或 Linux core dump，也不能直接证明 Qt 生命周期问题。

若启动前已有调试器或测试框架启用 faulthandler，会保留其目标文件，诊断事件记录 `fatal_owned=False`；此时自动致命错误堆栈仍由原启用方接收，Qt fatal 的显式线程转储仍写本次 fatal 文件。

Qt 消息和第三方异常可能包含本机路径等信息，对外分享前请检查并脱敏。此功能不会修改系统崩溃转储策略，也不会自动上传日志。

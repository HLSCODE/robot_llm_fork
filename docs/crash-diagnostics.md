# GUI 闪退诊断

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

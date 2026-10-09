# Windows 免安装版

支持 Windows x86_64。解压整个目录后运行，不需要安装 Python、uv 或项目依赖。
不要单独移动 exe；`_internal` 必须与两个 exe 保持在同一个目录。

## 使用

1. 解压到有写权限的本地目录，例如 `D:\RobotWorkbench`，不要放在 Program Files。
2. 运行 `robot-init.exe`，选择初始化配置、初始化数据与校验配置。
3. 修改 `config/config.toml` 及其 include 子配置；密钥放在 `.env`。
4. 按需初始化语音模型，或复制离线模型并修改配置中的模型路径。
5. 运行 `robot-llm.exe`。无硬件测试可执行 `robot-llm.exe --simulation --disable-websocket`。

首次直接运行主程序也会增量创建配置，不覆盖已有文件；不会自动迁移旧数据。
旧数据迁移请显式执行 `robot-init.exe migrate-data` 并先备份。
便携版隐藏依赖同步选项，即使显式指定 dependencies 也不会运行 uv。
ASR 模型在独立可取消的进程中准备，默认模型缓存保存在 `models`；显式环境变量优先。

配置、`data`、`models`、`logs` 都位于 exe 旁边，不受启动时工作目录影响。
启动早期错误查看 `logs/portable-startup.log`，业务及原生崩溃信息查看 `logs`。
相机、串口等系统驱动和硬件权限仍需单独配置；便携包不包含驱动安装程序。

## 构建（开发者）

在 Windows x86_64、Python 3.12 和 uv 环境中运行：

```powershell
./scripts/build_windows_portable.ps1
# 保留历史产物，使用另一个输出目录
./scripts/build_windows_portable.ps1 -OutputDirectory dist-next
```

脚本使用锁定的 full 依赖与单独固定版本的 PyInstaller，生成目录版和 ZIP：

```text
dist/
  robot-llm-<version>-windows-x86_64/
    robot-llm.exe
    robot-init.exe
    _internal/
    config/                 # 仅 example，运行时增量创建实际配置
    .env.example
    data/                   # 空目录
    models/                 # 仅 KWS 文本资源，无模型权重
    logs/                   # 空目录
  robot-llm-<version>-windows-x86_64.zip
  robot-llm-<version>-windows-x86_64.zip.sha256
```

版本取自项目元数据。已存在同版本目标时构建拒绝覆盖。构建使用全新暂存目录及明确的
配置文件白名单，不复制开发机 `.env`、实际设备配置、实验数据或模型缓存。
包含 Qt、设备 SDK、视觉及语音依赖，因此体积较大；模型需要另外下载/交付。
第三方库自身携带的资源与许可证可能随依赖收集，发布前需审查再分发许可。
为避免 Windows 长路径限制，wheel 中嵌套的许可证文件移至 `_internal/_licenses`，
该目录的 `original-paths.json` 记录其原始路径；许可证内容保持不变。

构建自动执行配置初始化/校验、Qt 离屏窗口和关键 GUI 导入检查，不连接真实设备。
GitHub Actions 中可手动运行 **Windows portable** 工作流并下载构建产物。

发布前还需在无 Python 的干净 Windows 机器验证 GUI、中文/空格路径、移动目录后启动、
初始化取消、模型加载，以及相机与两种机械臂的真实功能。构建冒烟测试不代替硬件验收。
当前产物没有代码签名，Windows SmartScreen 可能提醒来源未知。

## 升级

先备份 `.env`、`config`、`data`、`models`，把新版本解压到新目录，再迁移这些用户文件。
不要只替换 exe 或把旧 `_internal` 混入新版。重新运行配置初始化只补齐缺失文件，
已有配置中新字段的合并需要按配置示例人工检查。

打包机制参考 [PyInstaller spec 文档](https://pyinstaller.org/en/stable/spec-files.html)。

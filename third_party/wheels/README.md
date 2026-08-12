# 本地硬件 SDK wheels

该目录保存无法从公共包索引安装的硬件厂商 wheel。业务代码只能通过
`src/devices` 下对应的 provider 使用这些包，不能直接依赖本目录路径。

## 天机机械臂

- Windows x86-64 / CPython 3.12：
  `windows-x86_64/tj_robot_proj-0.1.0-cp312-cp312-win_amd64.whl`
- Linux x86-64：尚未提供。获得 wheel 后应新增平台目录，并在
  `pyproject.toml` 中按平台选择对应 artifact。

天机 SDK 为专有软件，更新 wheel 前需核对版本、目标 Python ABI、平台架构、
内置 native library 与运动学配置文件，并重新运行 provider 契约测试和真实硬件验收。

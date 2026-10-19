# 本地硬件 SDK wheels

该目录保存无法从公共包索引安装的硬件厂商 wheel。业务代码只能通过
`src/devices` 下对应的 provider 使用这些包，不能直接依赖本目录路径。

## 天机机械臂

- Windows x86-64：
  `windows-x86_64/tj_robot_proj-0.2.0-py3-none-win_amd64.whl`
- Linux x86-64：
  `linux-x86_64/tj_robot_proj-0.2.0-py3-none-linux_x86_64.whl`

替换同版本 wheel 后，运行 `uv lock --refresh-package tj-robot-proj` 更新
`uv.lock` 中两个平台的哈希，再用 `uv sync --frozen --link-mode=copy --extra hardware`
验证安装。仅运行 `uv lock` 不会刷新同版本 wheel 的哈希。

天机 SDK 为专有软件，更新 wheel 前需核对版本、目标 Python ABI、平台架构、
内置 native library 与运动学配置文件，并重新运行 provider 契约测试和真实硬件验收。

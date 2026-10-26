# 联调示例与独立 Demo

本目录集中存放从项目根目录整理过来的联调示例，不属于主程序启动链路，也不是 `tests/` 中的自动化测试。厂商手册和历史需求文档见 [技术参考资料](../docs/references/README.md)。

## 目录

| 路径 | 用途 |
| --- | --- |
| [hardware/test_devices.py](hardware/test_devices.py) | 加粉装置 RS-485 联调：夹爪、针升降和针旋转 |
| [hardware/gripper_control_v2.py](hardware/gripper_control_v2.py) | 知行夹爪原始测试 Demo，独立管理串口和本地标定 |
| [hardware/rgd_gripper_control.py](hardware/rgd_gripper_control.py) | 大寰 RGD 夹爪原始测试 Demo，支持开合、旋转等测试 |
| [web/test_client.html](web/test_client.html) | WebSocket 浏览器测试客户端 |
| [web/test_panel.html](web/test_panel.html) | 浏览器端机器人控制测试面板 |
| [network/udp-receiver.py](network/udp-receiver.py) | 独立 UDP 位置信息接收工具 |

## 使用约定

**硬件 Demo 会直接操作真实设备。** 运行前阅读脚本，检查串口、设备站号和工作区域；不要与主程序同时连接同一串口。原始夹爪 Demo 不具备主程序的统一资源仲裁与安全控制策略，部分示例启动时会初始化硬件。

从项目根目录执行加粉联调，使用模块形式以保证 `src` 导入路径正确：

```shell
uv run --extra hardware python -m examples.hardware.test_devices --scan
uv run --extra hardware python -m examples.hardware.test_devices -i
```

不带 `-i` 或 `--scan` 会执行脚本中的自动化动作序列，不建议在未确认硬件时运行。默认串口可通过 `--port` 覆盖；其余测试参数按脚本说明检查。

独立夹爪 Demo 可按文件路径运行，但务必先核对其默认端口、力值、速度和角度单位：

```shell
uv run --extra hardware python examples/hardware/gripper_control_v2.py
uv run --extra hardware python examples/hardware/rgd_gripper_control.py
```

知行原始 Demo 将 `gripper_config.json` 写在脚本同目录中，该本机标定文件不提交版本库。原始 Demo 仅作协议参考，正式设备接入分别见 [知行夹爪说明](../docs/zhixing-gripper.md) 和 [大寰夹爪说明](../docs/dh-rgd-gripper.md)。

Web 页面可在浏览器中打开，连接已启动的 WebSocket 服务；服务地址、认证及当前协议以 [WebSocket API 文档](../docs/websocket-api.md) 为准，历史 Demo 不保证覆盖所有最新接口。

UDP 接收工具运行后会绑定脚本中的监听地址和端口，使用前注意端口占用：

```shell
uv run python examples/network/udp-receiver.py
```

`scripts/` 保留构建、初始化与项目维护工具；不要将这些原始 Demo 移入正式 Provider 或自动化测试启动流程。

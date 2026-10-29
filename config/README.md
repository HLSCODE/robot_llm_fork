# 配置示例索引

需要修改某项功能时，先按下表找到对应示例。每个 TOML 示例顶部都有“用途 / 场景 / 使用”说明，字段含义见 [配置文档](../docs/configuration.md)。

## 入口与应用功能

| 示例 | 用途 / 何时查看 |
| --- | --- |
| [config.example.toml](config.example.toml) | 唯一配置入口；选择 include 的子文件，或集中定义全部配置。 |
| [application.example.toml](fragments/application.example.toml) | 仿真模式、交互限制、GUI 主题、日志与运行数据保存目录。 |
| [services.example.toml](fragments/services.example.toml) | 数据采集、外部定位 UDP、动作超时/重试与安全停止等待；不包含网络任务服务端配置。 |
| [server.example.toml](fragments/server.example.toml) | WebSocket/TCP 启停、监听、认证/TLS、共享控制权、任务白名单和运行回执。 |
| [ai.example.toml](fragments/ai.example.toml) | LLM Provider、模型地址、默认模型及各 AI 任务的路由。 |
| [voice.example.toml](fragments/voice.example.toml) | 麦克风、语音会话、VAD/ASR、关键词唤醒与语音输出。 |
| [devices.example.toml](fragments/devices.example.toml) | 身体升降、继电器、颈部、表情屏，以及相机目录/角色和视觉功能。 |
| [mobile-base.example.toml](fragments/mobile-base.example.toml) | 独立移动底盘的通信地址、端口与超时。 |

## 机械臂

| 示例 | 用途 / 关联 |
| --- | --- |
| [robot.example.toml](fragments/robot.example.toml) | 选择 Provider、Robot Profile 数据隔离标识和通用运动默认值。 |
| [realman.example.toml](fragments/robots/realman.example.toml) | 睿尔曼型号、左右控制器连接与初始位姿；由 robot.provider 选择使用。 |
| [tianji.example.toml](fragments/robots/tianji.example.toml) | 天机连接、SDK 容差、基座/工具坐标变换和七关节限位；由 robot.provider 选择使用。 |

## 末端工具

| 示例 | 用途 / 关联 |
| --- | --- |
| [end-effectors.example.toml](fragments/end-effectors.example.toml) | 配置左右臂无工具、固定工具或快换挂载；引用下方工具实例 ID。 |
| [connections.example.toml](fragments/end-effectors/connections.example.toml) | 集中声明物理串口与通信参数，各工具通过 connection 引用。 |
| [robot.example.toml](fragments/end-effectors/robot.example.toml) | 复用机械臂 SDK 的内置夹爪能力；不是机械臂 Provider 配置。 |
| [zhixing.example.toml](fragments/end-effectors/zhixing.example.toml) | 知行夹爪：站号、行程、开闭方向、速度和力矩。 |
| [dh-rgd.example.toml](fragments/end-effectors/dh-rgd.example.toml) | 大寰 RGD 旋转夹爪：开闭端点、夹持/旋转参数及角度单位。 |
| [adp.example.toml](fragments/end-effectors/adp.example.toml) | ADP 吸液枪/移液工具：连接与超时。 |
| [kuaihuanshou.example.toml](fragments/end-effectors/kuaihuanshou.example.toml) | 快换手：连接、锁紧与供电等自身控制，不识别实际安装的工具。 |

## 数据迁移辅助文件

[migration-targets.example.json](migration-targets.example.json) 是**可选的旧动作迁移目标映射模板**，不是应用启动配置，不放入 include。

- 用途：旧夹爪/吸液枪等动作缺少机械臂或工具目标时，通过 `actions` 按动作 ID 或名称指定 `arm`、`tool_id`；`defaults` 用于按执行器指定默认迁移目标。
- 使用：复制为 `config/migration-targets.json`，按现场目标填写后，通过 `robot-init migrate-data --target-map config/migration-targets.json` 显式使用。
- 不提供映射也可以迁移，缺少目标的动作会标记待配置，需要在应用中补齐后才能执行。详见 [数据迁移](../docs/data-migration.md)。
- JSON 不支持注释，因此保留该示例为纯数据，用途说明集中放在这里。

## 使用规则

1. `.example.toml` 是版本库模板；复制为同目录去掉 `.example` 的 `.toml` 才是实际配置。
2. 子文件必须由 `config/config.toml` 的 include 引入，仅复制文件不会自动生效；机械臂 Provider 的选择也不会自动加载其参数文件。
3. 初始化命令只补缺失文件，已有实际配置及 include 不会自动覆盖或更新。
4. 修改示例不会改变已生成的实际配置；API Key/认证 Token 放 `.env`，不要写进 TOML。
5. include 后声明的同名字段覆盖先声明的字段，入口自身字段覆盖子配置，环境变量优先级最高。无需依赖覆盖关系重复维护同一功能的示例。

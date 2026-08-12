# 天机机械臂 Provider

天机双机械臂已通过 `src.devices.robots.tianji` 接入统一设备运行时。应用层、
执行器和 GUI 仍只使用 `ArmMotion`、`ArmStateReader`、`StoppableDevice` 等项目
能力接口，不直接导入厂商 SDK。

## 安装

当前 artifact 仅支持 Windows x86-64、CPython 3.12：

```powershell
uv sync --extra hardware
```

wheel 固定保存在：

```text
third_party/wheels/windows-x86_64/
  tj_robot_proj-0.1.0-cp312-cp312-win_amd64.whl
```

SHA-256：
`BB4EBD0D5F47C2D157A4D556C1D1C74B84A103BA9430FFC20FA4A27A9448C3FD`

仓库根目录的 `tj_robot_proj/` 是 SDK 源码临时资料目录，不是运行时依赖，可以
删除。未安装 wheel 时，这个同名目录可能被 Python 识别成不完整的 namespace
package，但它不能替代 `uv sync --extra hardware`；wheel 安装完成后会解析到虚拟
环境中的正式包。

## 配置

在 `config/config.toml` 的 `[robot]` 中选择 Provider：

```toml
robot_provider = "tianji"
robot_model = "tj-dual-7"
tianji_controller_ip = "192.168.1.190"
tianji_kinematics_config = "ccs_m6_40.MvKDCfg"
tianji_acceleration_percent = 50
tianji_linear_acceleration_m_s2 = 0.5

# 统一运动参数
move_velocity = 10
move_radius = 0
move_connect = 0
move_block = 1
```

`left/right` 分别映射到 SDK 的 `A/B` 臂。统一位姿使用米和弧度；Provider 在
SDK 边界转换为毫米和角度。`move_radius` 和 `move_connect` 当前必须保持为
`0`，因为天机 SDK 的本次接入没有等价语义，非零配置会被明确拒绝。

## 当前能力

已接入：

- 双臂笛卡尔目标运动；
- `LINEAR` 直线规划；
- `JOINT` 逆解后关节路径运动；
- 双臂状态、关节角和 TCP 位姿读取；
- 快速停止与软件急停；
- 统一连接、锁、订阅和幂等释放生命周期。

尚未声明：

- 集成夹爪；
- 遥操作跟随；
- 拖拽示教、轨迹保存与复现；
- 标准化遥测；
- 工具架换装。

这些能力不会复用 RealMan 实现，也不会以空方法伪装支持。运行时会依据 Provider
声明的 capability 明确拒绝不支持的操作。若天机末端夹爪通过独立协议接入，应
优先注册独立的夹爪设备或新增天机夹爪 capability adapter。

## Linux x86-64 后续接入

获得 Linux wheel 后：

1. 放入 `third_party/wheels/linux-x86_64/`；
2. 在 `pyproject.toml` 中为 `tj-robot-proj` 增加 Linux 平台 source；
3. 核对 wheel 内含 `libMarvinSDK.so`、`libKine.so` 和机型配置；
4. 在 Linux CPython 3.12 上运行导入、Provider 契约和真实双臂验收。

## 验收边界

自动测试使用 fake SDK 验证配置、能力声明、单位转换、A/B 臂映射、两种运动模式、
停止和资源释放，不会连接真实设备。上线前仍必须验证控制器连接、左右臂映射、
坐标系、关节限位、速度/加速度、阻塞完成判定、停止距离和断线恢复。

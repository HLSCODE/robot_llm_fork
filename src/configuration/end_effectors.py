"""Deployment descriptions for tools, mounts and shared serial connections."""

from dataclasses import dataclass
import math
import os

from .errors import ConfigLoadError


@dataclass(frozen=True, slots=True)
class EndEffectorConnection:
    id: str
    port: str
    transport: str = "serial"
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.port.strip() or self.transport != "serial":
            raise ConfigLoadError("末端连接必须有 ID、端口，且 transport 为 serial")
        if (
            self.baudrate <= 0
            or self.bytesize not in (5, 6, 7, 8)
            or self.parity not in ("N", "E", "O")
            or self.stopbits not in (1, 2)
        ):
            raise ConfigLoadError(f"末端连接 {self.id} 的串口参数无效")


@dataclass(frozen=True, slots=True)
class EndEffectorTool:
    id: str
    kind: str
    provider: str
    connection: str = ""
    station: int = 1
    response_timeout_seconds: float = 3.0
    motion_timeout_seconds: float = 60.0
    command_interval_seconds: float = 0.2
    speed: int = 100
    torque: int = 60
    acceleration: int = 2000
    deceleration: int = 2000
    position_min: int = 0
    position_max: int = 4000
    rotation_speed: int = 50
    rotation_force: int = 60
    rotation_units_per_degree: float = 1.0
    open_position: int = 0
    closed_position: int = 1000
    display_name: str = ""
    reverse_direction: bool = False
    read_device_range: bool = True

    @property
    def label(self) -> str:
        return self.display_name.strip() or self.id

    def __post_init__(self) -> None:
        if not isinstance(self.reverse_direction, bool) or not isinstance(self.read_device_range, bool):
            raise ConfigLoadError("夹爪 reverse_direction/read_device_range 必须为布尔值")
        if not isinstance(self.display_name, str):
            raise ConfigLoadError("末端工具 display_name 必须为字符串")
        supported = {
            "robot": "gripper",
            "zhixing": "gripper",
            "dh_rgd": "gripper",
            "adp": "pipette",
            "kuaihuanshou": "tool_changer",
        }
        if not self.id.strip() or supported.get(self.provider) != self.kind:
            raise ConfigLoadError(f"末端工具 {self.id!r} 的 kind/provider 不匹配")
        if any(
            not math.isfinite(v) or v <= 0
            for v in (
                self.response_timeout_seconds,
                self.motion_timeout_seconds,
                self.command_interval_seconds,
            )
        ):
            raise ConfigLoadError(f"末端工具 {self.id} 的超时必须为正数")
        if self.provider == "robot":
            if self.connection:
                raise ConfigLoadError("robot 末端复用机械臂连接，不能配置串口 connection")
        elif not self.connection:
            raise ConfigLoadError(f"末端工具 {self.id} 缺少 connection")
        if not 1 <= self.station <= 247:
            raise ConfigLoadError("末端设备站号必须在 1～247")
        if self.provider not in ("zhixing", "dh_rgd") and self.station != 1:
            raise ConfigLoadError("当前仅知行和大寰 RGD Provider 支持配置站号；其他协议使用已有固定地址")
        if not -(2**31) <= self.position_min < self.position_max < 2**31:
            raise ConfigLoadError("夹爪行程范围无效")
        if any(
            not 1 <= v <= 65535
            for v in (self.speed, self.torque, self.acceleration, self.deceleration)
        ):
            raise ConfigLoadError("夹爪运动参数必须在 1～65535")
        if self.provider == "dh_rgd" and (
            not 1 <= self.speed <= 100
            or not 20 <= self.torque <= 100
            or not 1 <= self.rotation_speed <= 100
            or not 20 <= self.rotation_force <= 100
            or not math.isfinite(self.rotation_units_per_degree)
            or self.rotation_units_per_degree <= 0
            or not 0 <= self.open_position <= 1000
            or not 0 <= self.closed_position <= 1000
            or self.open_position == self.closed_position
        ):
            raise ConfigLoadError("RGD 速度须为1～100、力为20～100、角度比例为正数，开闭位置须不同且为0～1000")


@dataclass(frozen=True, slots=True)
class EndEffectorMount:
    arm: str
    mode: str
    tool: str = ""
    changer: str = ""
    tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EndEffectorSettings:
    connections: tuple[EndEffectorConnection, ...] = ()
    tools: tuple[EndEffectorTool, ...] = ()
    mounts: tuple[EndEffectorMount, ...] = ()
    status_poll_interval_seconds: float = 2.0

    @property
    def configured(self) -> bool:
        return bool(self.connections or self.tools or self.mounts)

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.status_poll_interval_seconds)
            or self.status_poll_interval_seconds < 0.5
        ):
            raise ConfigLoadError("快换手状态刷新周期必须至少为 0.5 秒")
        connections = {item.id: item for item in self.connections}
        tools = {item.id: item for item in self.tools}
        if len(connections) != len(self.connections) or len(tools) != len(self.tools):
            raise ConfigLoadError("末端连接或工具 ID 重复")
        ports = [os.path.normcase(os.path.realpath(c.port)) for c in self.connections]
        if len(set(ports)) != len(ports):
            raise ConfigLoadError("同一末端串口只能声明一次连接")
        addresses: dict[tuple[str, str, int], set[str]] = {}
        for tool in self.tools:
            if tool.connection and tool.connection not in connections:
                raise ConfigLoadError(f"工具 {tool.id} 引用了不存在的连接 {tool.connection}")
            address = (tool.connection, tool.provider, tool.station)
            if tool.connection:
                addresses.setdefault(address, set()).add(tool.id)
        arms: set[str] = set()
        mounted_tools: set[str] = set()
        for mount in self.mounts:
            if mount.arm not in ("left", "right") or mount.arm in arms:
                raise ConfigLoadError("末端挂载必须使用唯一的 left/right")
            arms.add(mount.arm)
            if mount.mode == "none":
                if mount.tool or mount.changer or mount.tools:
                    raise ConfigLoadError(f"未安装末端 {mount.arm} 不能声明工具或快换参数")
                continue
            if mount.mode == "fixed":
                if mount.tool not in tools or mount.tools or mount.changer:
                    raise ConfigLoadError(
                        f"固定末端 {mount.arm} 必须绑定一个工具，不能声明快换参数"
                    )
                if tools[mount.tool].kind == "tool_changer":
                    raise ConfigLoadError("快换手应声明为 exchangeable 挂载")
            elif mount.mode == "exchangeable":
                changer = tools.get(mount.changer)
                if mount.tool or not changer or changer.kind != "tool_changer":
                    raise ConfigLoadError(f"快换末端 {mount.arm} 的快换手或共享连接无效")
                if not mount.tools or len(set(mount.tools)) != len(mount.tools):
                    raise ConfigLoadError("快换末端必须声明不重复的可选工具列表")
                for tool_id in mount.tools:
                    candidate = tools.get(tool_id)
                    if (
                        not candidate
                        or candidate.kind == "tool_changer"
                        or candidate.connection != changer.connection
                    ):
                        raise ConfigLoadError(f"快换工具 {tool_id} 必须引用同一共享连接")
            else:
                raise ConfigLoadError(f"未知末端安装模式: {mount.mode}")
            bindings = {mount.tool} if mount.mode == "fixed" else {mount.changer, *mount.tools}
            if mounted_tools & bindings:
                raise ConfigLoadError("同一工具实例不能绑定到多个机械臂末端")
            mounted_tools.update(bindings)
        for identities in addresses.values():
            if len(identities) < 2:
                continue
            # Electrically exclusive alternatives on one changer can share a
            # protocol address. Fixed or separate mounts cannot share a slave.
            alternatives = [
                m for m in self.mounts if m.mode == "exchangeable" and identities <= set(m.tools)
            ]
            if len(alternatives) != 1 or any(
                m is not alternatives[0] and identities & {m.tool, m.changer, *m.tools}
                for m in self.mounts
            ):
                raise ConfigLoadError("相同连接、协议和地址仅允许用于同一快换末端的互斥备选工具")

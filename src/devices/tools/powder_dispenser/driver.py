"""
加粉装置控制器
===============
封装同一条 RS-485 总线上的三个设备：
  - 加粉夹爪 (Electric Gripper, move_to 替代 grip/release)
  - 针升降电机 (Stepper Motor)
  - 针旋转电机 (Stepper Motor)

由 DeviceRuntime 管理生命周期，并通过统一 ActionEngine 的
`执行器: "加粉装置"` 动作入口调用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...transports import (
    SerialTransport,
    StepperBus,
    ElectricGripper,
)
from ...transports.devices.electric_gripper import MotionStatus
from ...transports.devices.stepper_motor import MotorStatus, MSeriesRegister
from ....configuration.settings import DeviceSettings

logger = logging.getLogger(__name__)

# 运动默认参数
DEFAULT_STEPS = 5000
DEFAULT_SPEED_RPM = 60
DEFAULT_ACCEL_MS = 200
DEFAULT_SAFE_LIFT_POSITION = 0
DEFAULT_DISPENSE_LIFT_POSITION = 50000
DEFAULT_ROTATION_HOME_POSITION = 0


@dataclass
class TappingController:
    """加粉装置控制器，管理串口生命周期。"""

    transport: SerialTransport
    gripper: ElectricGripper
    bus: StepperBus
    lift_address: int
    rotation_address: int

    @classmethod
    def from_settings(cls, settings: DeviceSettings) -> "TappingController":
        """Create the controller from an explicit device snapshot."""
        cfg = settings.tapping_config()
        transport = SerialTransport(
            cfg["port"],
            baudrate=cfg["baudrate"],
            timeout=cfg["timeout"],
        )
        gripper = ElectricGripper(transport, address=cfg["gripper_address"])
        bus = StepperBus(transport)
        return cls(
            transport=transport,
            gripper=gripper,
            bus=bus,
            lift_address=cfg["lift_address"],
            rotation_address=cfg["rotation_address"],
        )

    def close(self) -> None:
        """关闭串口连接。"""
        self.transport.close()

    # ---- 等待到位 ----

    def _wait_for_gripper(self, timeout: float = 8.0, poll_interval: float = 0.1) -> None:
        """轮询夹爪运动状态，直到到达目标位置或超时。"""
        start = time.time()
        while time.time() - start < timeout:
            status = self.gripper.read_motion_status()
            if status == MotionStatus.REACHED_TARGET:
                return
            time.sleep(poll_interval)
        raise TimeoutError(f"夹爪运动超时 ({timeout}s)")

    def _wait_for_motor(self, motor, timeout: float = 15.0, poll_interval: float = 0.1) -> None:
        """轮询步进电机状态，直到空闲/到位或超时。"""
        start = time.time()
        while time.time() - start < timeout:
            status = motor.read_status()
            if status == MotorStatus.IDLE_OR_ARRIVED:
                return
            time.sleep(poll_interval)
        raise TimeoutError(f"电机 {motor.address} 运动超时 ({timeout}s)")

    # ---- 夹爪 ----
    def gripper_move_to(self, percent: int) -> None:
        """设置夹爪开度 (0=全开, 100=全闭)，等待到位。"""
        self.gripper.move_to(percent)
        self._wait_for_gripper()

    def gripper_grip(self) -> None:
        """夹紧 (完全闭合)。"""
        self.gripper_move_to(100)

    def gripper_release(self) -> None:
        """释放 (完全张开)。"""
        self.gripper_move_to(0)

    # ---- 针升降 ----
    def read_lift_position(self) -> int:
        """读取升降电机实际位置。"""
        return int(self.bus.motor(self.lift_address).read_actual_position())

    def lift_move_to(self, steps: int) -> None:
        """升降电机运动到绝对位置，等待到位。"""
        motor = self.bus.motor(self.lift_address)
        motor.move_to(steps, rpm=DEFAULT_SPEED_RPM, acceleration_ms=DEFAULT_ACCEL_MS)
        self._wait_for_motor(motor)

    def lift_move_relative(self, delta_steps: int) -> None:
        """升降电机相对当前位置运动。正数上升，负数下降。"""
        self.lift_move_to(self.read_lift_position() + int(delta_steps))

    def lift_up(self, steps: int = DEFAULT_STEPS) -> None:
        """针上升到指定绝对位置。"""
        self.lift_move_to(abs(int(steps)))

    def lift_down(self, steps: int = DEFAULT_STEPS) -> None:
        """针下降到指定绝对位置。"""
        self.lift_move_to(abs(int(steps)))

    def lift_to_safe(self, position: int = DEFAULT_SAFE_LIFT_POSITION) -> None:
        """升降回安全绝对位置。"""
        self.lift_move_to(int(position))

    def lift_to_dispense(self, position: int = DEFAULT_DISPENSE_LIFT_POSITION) -> None:
        """升降到加粉绝对位置。"""
        self.lift_move_to(int(position))

    def lift_stop(self) -> None:
        """停止升降电机。"""
        self.bus.motor(self.lift_address).stop()

    def lift_enable(self) -> None:
        """使能升降电机。"""
        self.bus.motor(self.lift_address).write_register(MSeriesRegister.ENABLE, 0x0001)

    # ---- 针旋转 ----
    def read_rotation_position(self) -> int:
        """读取旋转电机实际位置。"""
        return int(self.bus.motor(self.rotation_address).read_actual_position())

    def rotation_move_to(self, steps: int) -> None:
        """旋转电机运动到绝对位置，等待到位。"""
        motor = self.bus.motor(self.rotation_address)
        motor.move_to(steps, rpm=DEFAULT_SPEED_RPM, acceleration_ms=DEFAULT_ACCEL_MS)
        self._wait_for_motor(motor)

    def rotation_move_relative(self, delta_steps: int) -> None:
        """旋转电机相对当前位置运动。正数正转，负数反转。"""
        self.rotation_move_to(self.read_rotation_position() + int(delta_steps))

    def rotation_cw(self, steps: int = DEFAULT_STEPS) -> None:
        """正转到绝对位置，兼容已有任务文件。"""
        self.rotation_move_to(abs(int(steps)))

    def rotation_ccw(self, steps: int = DEFAULT_STEPS) -> None:
        """反转回零位，兼容已有任务文件。"""
        self.rotation_move_to(0)

    def rotation_to_home(self, position: int = DEFAULT_ROTATION_HOME_POSITION) -> None:
        """旋转回指定绝对原点/安全位置。"""
        self.rotation_move_to(int(position))

    def rotation_stop(self) -> None:
        """停止旋转电机。"""
        self.bus.motor(self.rotation_address).stop()

    def rotation_enable(self) -> None:
        """使能旋转电机。"""
        self.bus.motor(self.rotation_address).write_register(MSeriesRegister.ENABLE, 0x0001)

    def enable_all(self) -> None:
        """使能两个电机。"""
        self.lift_enable()
        self.rotation_enable()


# 操作名称 -> 方法映射（供统一 ActionEngine 使用）
OPERATIONS = {
    # 夹爪
    "夹爪闭合": lambda ctrl, **kw: ctrl.gripper_grip(),
    "夹爪张开": lambda ctrl, **kw: ctrl.gripper_release(),
    "夹爪移动到": lambda ctrl, **kw: ctrl.gripper_move_to(int(kw.get("开度", 50))),
    # 针升降
    "针上升": lambda ctrl, **kw: ctrl.lift_up(steps=int(kw.get("步数", DEFAULT_STEPS))),
    "针下降": lambda ctrl, **kw: ctrl.lift_down(steps=int(kw.get("步数", DEFAULT_STEPS))),
    "针停止": lambda ctrl, **kw: ctrl.lift_stop(),
    # 针旋转
    "针正转": lambda ctrl, **kw: ctrl.rotation_cw(steps=int(kw.get("步数", DEFAULT_STEPS))),
    "针反转": lambda ctrl, **kw: ctrl.rotation_ccw(steps=int(kw.get("步数", DEFAULT_STEPS))),
    "针旋转停止": lambda ctrl, **kw: ctrl.rotation_stop(),
    # 通用
    "使能": lambda ctrl, **kw: ctrl.enable_all(),
}

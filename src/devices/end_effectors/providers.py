"""Small adapters for individual tool instances; SDK ownership stays outside."""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from threading import RLock

from ...configuration.end_effectors import EndEffectorTool
from ..runtime.ids import ROBOT_SYSTEM
from ..runtime.arm_models import ArmId
from ..runtime.contracts import GripperControl
from ..runtime.runtime import DeviceRuntime
from ..transports.core.transport import Transport


@runtime_checkable
class Gripper(Protocol):
    def open(self) -> None: ...
    def grip(self) -> None: ...


@runtime_checkable
class PositionedGripper(Gripper, Protocol):
    def move_closure(self, percent: float) -> None: ...


@runtime_checkable
class InitializableGripper(Gripper, Protocol):
    def initialize(self) -> None: ...


@runtime_checkable
class RotatingGripper(Gripper, Protocol):
    def rotate(
        self, angle_degrees: float, *, relative: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> None: ...
    def stop_rotation(self) -> None: ...


@dataclass
class RobotGripper:
    runtime: DeviceRuntime
    arm: ArmId

    def _robot(self) -> GripperControl:
        return self.runtime.require(ROBOT_SYSTEM, GripperControl)

    def open(self) -> None:
        self._robot().open_gripper(self.arm)

    def grip(self) -> None:
        self._robot().close_gripper(self.arm)

    def move_closure(self, percent: float) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("夹爪闭合比例必须为 0～100%")
        self.move_position(round((100 - percent) * 10))

    def move_position(self, position: int) -> None:
        if not 0 <= position <= 1000:
            raise ValueError("夹爪位置必须为 0～1000")
        self._robot().move_gripper(self.arm, position)

    def close(self) -> None:
        """The robot runtime owns the shared SDK connection."""


class ZhixingGripper:
    def __init__(self, transport: Transport, settings: EndEffectorTool) -> None:
        from ..tools.zhixing_gripper.driver import ZhixingGripperController

        self._station = settings.station
        self._range_loaded = not settings.read_device_range
        self._motion_lock = RLock()
        self._controller = ZhixingGripperController(
            transport,
            speed=settings.speed,
            torque=settings.torque,
            acceleration=settings.acceleration,
            deceleration=settings.deceleration,
            wait_timeout=settings.motion_timeout_seconds,
            station_ranges={settings.station: (settings.position_min, settings.position_max)},
            reverse_direction=settings.reverse_direction,
        )

    def open(self) -> None:
        self.move_closure(0)

    def grip(self) -> None:
        self.move_closure(100)

    def move_closure(self, percent: float) -> None:
        if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("夹爪闭合比例必须为 0～100%")
        with self._motion_lock:
            if not self._range_loaded:
                # Read calibration on first use, never perform startup motion or homing.
                self._controller.initialize()
                self._range_loaded = True
            self._controller.move_percent(self._station, percent)

    def close(self) -> None:
        # Do not send disable commands at application exit: a gripper may hold
        # a load. The application closes its shared physical port separately.
        self._controller.release_transport()

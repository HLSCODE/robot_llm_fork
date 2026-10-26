"""RGD V2.1 protocol. Construction/release never moves or calibrates hardware."""

from __future__ import annotations

import math
import logging
import threading
import time
from collections.abc import Callable

from ....configuration.end_effectors import EndEffectorTool
from ...transports.core.strategies import ModbusRTUStrategy
from ...transports.core.transport import Transport
from ...transports.protocols.modbus_rtu import ModbusRTUProtocol


class RGDGripper:
    def __init__(self, transport: Transport, settings: EndEffectorTool) -> None:
        self._transport = transport
        self._settings = settings
        self._protocol = ModbusRTUProtocol()
        self._lock = threading.RLock()

    def open(self) -> None:
        self.move_closure(0)

    def grip(self) -> None:
        self.move_closure(100)

    def move_closure(self, percent: float) -> None:
        if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("RGD 闭合度必须为0～100%")
        settings = self._settings
        target = round(settings.open_position + percent / 100 * (
            settings.closed_position - settings.open_position
        ))
        with self._lock:
            self._require_initialized(0x0200)
            start = self._read(0x0202)
            closing = (target - start) * (settings.closed_position - settings.open_position) > 0
            self._write(0x0101, settings.torque)
            self._write(0x0104, settings.speed)
            self._write(0x0103, target)
            self._wait(lambda: self._grip_complete(target, closing))

    def initialize(self) -> None:
        """Explicit homing; may physically move both gripper axes."""
        with self._lock:
            self._write(0x0100, 1)
            self._wait(lambda: self._read(0x0200) == 1 and self._read(0x020A) == 1)

    def rotate(
        self, angle_degrees: float, *, relative: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        if isinstance(angle_degrees, bool) or not math.isfinite(angle_degrees):
            raise ValueError("RGD 角度必须为有限数值")
        units = angle_degrees * self._settings.rotation_units_per_degree
        if not -32768 <= units <= 32767:
            raise ValueError("RGD 旋转角度超过有符号16位寄存器范围")
        target = round(units)
        with self._lock:
            self._require_initialized(0x020A)
            start = self._read_signed(0x0208)
            expected = start + target if relative else target
            if not -32768 <= expected <= 32767:
                raise ValueError("RGD 累计旋转角度将溢出，请先复位累计角度")
            self._write(0x0107, self._settings.rotation_speed)
            self._write(0x0108, self._settings.rotation_force)
            try:
                self._write(0x0109 if relative else 0x0105, target & 0xFFFF)
                self._wait(lambda: self._rotation_complete(expected), checkpoint=checkpoint)
            except Exception:
                # A stall can keep applying torque. Best-effort stop must not
                # replace the original failure or replay the movement command.
                try:
                    self.stop_rotation()
                except Exception:
                    logging.getLogger(__name__).exception("RGD 旋转失败后的停止指令失败")
                raise

    def stop_rotation(self) -> None:
        self._write(0x0502, 1)

    def close(self) -> None:
        self._transport.close()

    def _grip_complete(self, target: int, allow_contact: bool) -> bool:
        state = self._read(0x0201)
        if state == 3:
            raise IOError("RGD 夹持物体掉落")
        if state == 2:
            if not allow_contact:
                raise IOError("RGD 张开过程受阻")
            return True
        if state not in (0, 1):
            raise IOError(f"RGD 夹持状态异常: {state}")
        return state == 1 and self._read(0x0202) == target

    def _rotation_complete(self, expected: int) -> bool:
        state = self._read(0x020B)
        if state not in (0, 1):
            raise IOError(f"RGD 旋转堵转或状态异常: {state}")
        return state == 1 and self._read_signed(0x0208) == expected

    def _wait(
        self, completed: Callable[[], bool], *, checkpoint: Callable[[], None] | None = None,
    ) -> None:
        deadline = time.monotonic() + self._settings.motion_timeout_seconds
        while time.monotonic() < deadline:
            if checkpoint is not None:
                checkpoint()
            alarm = self._read(0x0205)
            if alarm:
                raise IOError(f"RGD 报警: 0x{alarm:04X}")
            if completed():
                return
            time.sleep(self._settings.command_interval_seconds)
        raise TimeoutError("RGD 动作等待超时")

    def _require_initialized(self, register: int) -> None:
        if self._read(register) != 1:
            raise ValueError("RGD 尚未初始化，请显式执行夹爪初始化动作")
        alarm = self._read(0x0205)
        if alarm:
            raise IOError(f"RGD 报警: 0x{alarm:04X}")

    def _read_signed(self, register: int) -> int:
        value = self._read(register)
        return value - 65536 if value >= 32768 else value

    def _read(self, register: int) -> int:
        station = self._settings.station
        request = self._protocol.build_read_registers(station, register)
        response = self._transport.transact_with_strategy(ModbusRTUStrategy(request, 7))
        return self._protocol.parse_read_registers(response, station, 1)[0]

    def _write(self, register: int, value: int) -> None:
        station = self._settings.station
        request = self._protocol.build_write_register(station, register, value)
        response = self._transport.transact_with_strategy(ModbusRTUStrategy(request, 8))
        self._protocol.parse_write_register(response, station, register, value)

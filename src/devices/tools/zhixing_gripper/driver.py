"""Zhixing protocol from examples/hardware/gripper_control_v2.py, using shared transport."""

from __future__ import annotations

import logging
import math
import time
from threading import RLock

from ...transports.core.strategies import ModbusRTUStrategy
from ...transports.core.transport import Transport
from ...transports.protocols.modbus_rtu import ModbusRTUProtocol

logger = logging.getLogger(__name__)


class ZhixingGripperController:
    """Own one connection and serialize complete motions for both stations.

    Percent follows CRT: 0 is fully open, 100 is fully closed. Each station
    has its own signed 32-bit stroke mapping (high register first).
    """

    def __init__(
        self,
        transport: Transport,
        *,
        speed: int = 100,
        torque: int = 60,
        acceleration: int = 2000,
        deceleration: int = 2000,
        wait_timeout: float = 8.0,
        left_range: tuple[int, int] = (0, 4000),
        right_range: tuple[int, int] = (0, 4000),
        station_ranges: dict[int, tuple[int, int]] | None = None,
    ) -> None:
        if not math.isfinite(wait_timeout) or wait_timeout <= 0:
            raise ValueError("知行夹爪等待超时必须为正数")
        for value in (speed, torque, acceleration, deceleration):
            if not 1 <= value <= 0xFFFF:
                raise ValueError("知行夹爪运动参数必须在 1..65535 之间")
        ranges = station_ranges if station_ranges is not None else {1: left_range, 2: right_range}
        if not ranges or any(isinstance(station, bool) or not 1 <= station <= 247 for station in ranges):
            raise ValueError("夹爪站号必须在 1～247")
        for low, high in ranges.values():
            if not -(2**31) <= low < high < 2**31:
                raise ValueError("知行夹爪行程范围无效")
        self._transport = transport
        self._protocol = ModbusRTUProtocol()
        self._lock = RLock()
        self._ranges = dict(ranges)
        self._motion_values = (speed, torque, acceleration, deceleration)
        self._wait_timeout = wait_timeout
        self._enabled: set[int] = set()

    def initialize(self) -> None:
        with self._lock:
            for station in self._ranges:
                # Verify both slaves respond even if their mapping is unavailable.
                self._check_alarm(station)
                try:
                    low = self._read32(station, 0x0404)
                    high = self._read32(station, 0x0406)
                    if high <= low:
                        raise ValueError(f"invalid stroke mapping: {low}..{high}")
                except Exception as exc:
                    logger.warning(
                        "知行夹爪站号 %s 读取行程映射失败，使用配置范围 %s: %s",
                        station,
                        self._ranges[station],
                        exc,
                    )
                else:
                    self._ranges[station] = (low, high)

    def move_percent(self, station: int, percent: float) -> None:
        if isinstance(station, bool) or station not in self._ranges:
            raise ValueError("知行夹爪站号未配置")
        if isinstance(percent, bool) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("知行夹爪开合度必须在 0..100 之间")
        with self._lock:
            self._check_alarm(station)
            # Reconfirm enable for every command, including retries after lost replies.
            # Track attempts so shutdown also handles an enable with no response.
            self._enabled.add(station)
            self._write(station, 0x0100, 1)
            time.sleep(0.2)
            low, high = self._ranges[station]
            target = int(round(low + percent / 100.0 * (high - low))) & 0xFFFFFFFF
            values = [(target >> 16) & 0xFFFF, target & 0xFFFF]
            request = self._protocol.build_write_registers(station, 0x0102, values)
            response = self._exchange(request, 8)
            self._protocol.parse_write_registers(response, station, 0x0102, 2)
            for register, value in zip(range(0x0104, 0x0108), self._motion_values):
                self._write(station, register, value)
            self._write(station, 0x0108, 1)
            deadline = time.monotonic() + self._wait_timeout
            while time.monotonic() < deadline:
                self._check_alarm(station)
                torque_arrived, position_arrived = self._read(station, 0x0601, 2)
                if position_arrived == 1 or torque_arrived == 1:
                    return
                time.sleep(0.02)
            raise TimeoutError(f"知行夹爪站号 {station} 运动超时 ({self._wait_timeout}s)")

    def close(self) -> None:
        with self._lock:
            try:
                for station in sorted(self._enabled):
                    try:
                        self._write(station, 0x0100, 0)
                    except Exception:
                        logger.exception("知行夹爪站号 %s 失能失败", station)
            finally:
                self._enabled.clear()
                self._transport.close()

    def release_transport(self) -> None:
        """Release a borrowed endpoint without changing a held load."""
        self._transport.close()

    def _check_alarm(self, station: int) -> None:
        alarm = self._read(station, 0x0612)[0]
        if alarm:
            raise IOError(f"知行夹爪站号 {station} 报警: 0x{alarm:04X}")

    def _exchange(self, request: bytes, response_size: int) -> bytes:
        return self._transport.transact_with_strategy(ModbusRTUStrategy(request, response_size))

    def _read(self, station: int, register: int, count: int = 1) -> list[int]:
        request = self._protocol.build_read_registers(station, register, count)
        response = self._exchange(request, self._protocol.expected_read_response_size(count))
        return self._protocol.parse_read_registers(response, station, count)

    def _read32(self, station: int, register: int) -> int:
        high, low = self._read(station, register, 2)
        value = (high << 16) | low
        return value - 0x100000000 if value >= 0x80000000 else value

    def _write(self, station: int, register: int, value: int) -> None:
        request = self._protocol.build_write_register(station, register, value)
        response = self._exchange(request, 8)
        self._protocol.parse_write_register(response, station, register, value)

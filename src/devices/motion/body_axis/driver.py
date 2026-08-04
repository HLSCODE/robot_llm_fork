"""Body-axis Modbus driver over an injected transport."""

from __future__ import annotations

import time

from ...transports import ModbusRTUStrategy, ProtocolError, Transport


class ModbusMotor:
    """Control the body lift axis without owning serial configuration."""

    def __init__(self, transport: Transport, slave_id: int) -> None:
        if not 1 <= slave_id <= 247:
            raise ValueError("Modbus slave_id must be in range 1..247")
        self.slave_id = slave_id
        self._transport = transport
        self.trigger = 0x6002
        self.pr0_mode = 0x6200
        self.pos_high = 0x6201
        self.pos_low = 0x6202
        self.speed = 0x6203
        self.acc = 0x6204
        self.dec = 0x6205
        self.enable_addr = 0x000F
        self._initialize_motion()
        self.enable()

    @staticmethod
    def _calculate_crc(data: bytes | bytearray) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc

    def _create_modbus_frame(
        self,
        function_code: int,
        address: int,
        value: int | None = None,
        count: int = 1,
    ) -> bytes:
        if function_code == 0x03:
            frame = bytearray(
                (
                    self.slave_id,
                    function_code,
                    address >> 8,
                    address & 0xFF,
                    count >> 8,
                    count & 0xFF,
                )
            )
        elif function_code == 0x06 and value is not None:
            frame = bytearray(
                (
                    self.slave_id,
                    function_code,
                    address >> 8,
                    address & 0xFF,
                    value >> 8,
                    value & 0xFF,
                )
            )
        else:
            raise ValueError(f"unsupported Modbus function: {function_code}")
        crc = self._calculate_crc(frame)
        frame.extend((crc & 0xFF, crc >> 8))
        return bytes(frame)

    def write_register(self, address: int, value: int) -> None:
        frame = self._create_modbus_frame(0x06, address, value)
        response = self._transport.transact_with_strategy(
            ModbusRTUStrategy(frame, 8)
        )
        if response != frame:
            raise ProtocolError(
                "body axis write response does not echo the request"
            )

    def read_holding_registers(
        self,
        address: int,
        count: int = 1,
    ) -> list[int]:
        if count <= 0 or count > 125:
            raise ValueError("Modbus register count must be in range 1..125")
        frame = self._create_modbus_frame(0x03, address, count=count)
        response = self._transport.transact_with_strategy(
            ModbusRTUStrategy(frame, 5 + 2 * count)
        )
        if (
            response[0] != self.slave_id
            or response[1] != 0x03
            or response[2] != 2 * count
        ):
            raise ProtocolError("body axis read response header is invalid")
        return [
            (response[3 + index * 2] << 8) | response[4 + index * 2]
            for index in range(count)
        ]

    def enable(self) -> None:
        self.write_register(self.enable_addr, 0x0001)
        time.sleep(0.2)

    def emergency_stop(self) -> None:
        self.write_register(self.trigger, 0x0040)
        time.sleep(0.1)
        self.enable()

    def _initialize_motion(self) -> None:
        self.write_register(self.pr0_mode, 0x0001)
        self.write_register(self.speed, 0x0058)
        self.write_register(self.acc, 0x0032)
        self.write_register(self.dec, 0x0032)
        time.sleep(0.2)

    @staticmethod
    def split_32bit(value: int) -> tuple[int, int]:
        normalized = value & 0xFFFFFFFF
        return (normalized >> 16) & 0xFFFF, normalized & 0xFFFF

    def move_to(self, position: int) -> None:
        high, low = self.split_32bit(position)
        self.write_register(self.pr0_mode, 0x0001)
        self.write_register(self.pos_high, high)
        self.write_register(self.pos_low, low)
        self.write_register(self.trigger, 0x0010)

    def is_reached(self) -> bool:
        return self.read_holding_registers(self.trigger)[0] == 0

    def close(self) -> None:
        self._transport.close()

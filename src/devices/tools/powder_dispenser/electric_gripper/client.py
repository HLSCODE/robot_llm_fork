"""Electric gripper client."""

from __future__ import annotations

from ....transports.core.strategies import ModbusRTUStrategy
from ....transports.core.transport import SerialTransport, Transport
from ....transports.protocols.modbus_rtu import ModbusRTUProtocol
from .registers import DEFAULT_GRIPPER_ADDRESS, GripperRegister, MotionStatus, clamp_percent


class ElectricGripper:
    def __init__(
        self,
        transport: Transport | str,
        *,
        address: int = DEFAULT_GRIPPER_ADDRESS,
        baudrate: int = 115200,
        timeout: float = 0.5,
    ) -> None:
        self.transport = (
            SerialTransport(transport, baudrate=baudrate, timeout=timeout)
            if isinstance(transport, str)
            else transport
        )
        self.address = int(address)
        self.protocol = ModbusRTUProtocol()

    def read_register(self, register: int) -> int:
        request = self.protocol.build_read_registers(self.address, int(register), 1)
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_read_response_size(1))
        )
        return self.protocol.parse_read_registers(response, self.address, 1)[0]

    def write_register(self, register: int, value: int) -> bool:
        request = self.protocol.build_write_register(self.address, int(register), int(value) & 0xFFFF)
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_write_response_size())
        )
        return self.protocol.parse_write_register(response, self.address, int(register), int(value) & 0xFFFF)

    def initialize(self) -> None:
        self.write_register(GripperRegister.INITIALIZE, 1)

    def move_to(self, percent: int) -> None:
        self.write_register(GripperRegister.TARGET_POS, clamp_percent(percent))

    def read_position(self) -> int:
        return self.read_register(GripperRegister.CURRENT_POS)

    def read_motion_status(self) -> MotionStatus:
        raw = self.read_register(GripperRegister.MOTION_STATUS)
        try:
            return MotionStatus(raw)
        except ValueError:
            return MotionStatus.REACHED_TARGET if raw == 0 else MotionStatus.ERROR


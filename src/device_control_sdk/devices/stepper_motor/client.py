"""Stepper motor client."""

from __future__ import annotations

from collections.abc import Mapping

from ...core.strategies import ModbusRTUStrategy
from ...core.transport import SerialTransport, Transport
from ...protocols.modbus_rtu import ModbusRTUProtocol
from .registers import (
    DEFAULT_STEPPER_SERIES_BY_ADDRESS,
    MSeriesRegister,
    MotorStatus,
    Register,
    StepperSeriesLike,
    get_stepper_spec,
    int32_to_registers,
    register_to_int16,
    registers_to_int32,
    speed_to_register,
)


class StepperBus:
    def __init__(
        self,
        transport: Transport | str,
        *,
        baudrate: int = 115200,
        timeout: float = 0.5,
        series_by_address: Mapping[int, StepperSeriesLike] | None = None,
    ) -> None:
        self.transport = (
            SerialTransport(transport, baudrate=baudrate, timeout=timeout)
            if isinstance(transport, str)
            else transport
        )
        self.series_by_address = dict(DEFAULT_STEPPER_SERIES_BY_ADDRESS)
        if series_by_address:
            self.series_by_address.update(series_by_address)

    def motor(self, address: int, series: StepperSeriesLike | None = None) -> "StepperMotor":
        return StepperMotor(self.transport, address=address, series=series or self.series_by_address.get(address))


class StepperMotor:
    def __init__(
        self,
        transport: Transport,
        *,
        address: int,
        series: StepperSeriesLike | None = None,
    ) -> None:
        self.transport = transport
        self.address = int(address)
        self.series = series
        self.protocol = ModbusRTUProtocol()

    def read_register(self, register: Register) -> int:
        values = self.read_registers(register, 1)
        return values[0]

    def read_registers(self, register: Register, count: int) -> list[int]:
        request = self.protocol.build_read_registers(self.address, int(register), int(count))
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_read_response_size(count))
        )
        return self.protocol.parse_read_registers(response, self.address, int(count))

    def write_register(self, register: Register, value: int) -> bool:
        request = self.protocol.build_write_register(self.address, int(register), int(value) & 0xFFFF)
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_write_response_size())
        )
        return self.protocol.parse_write_register(response, self.address, int(register), int(value) & 0xFFFF)

    def write_registers(self, register: Register, values: list[int]) -> bool:
        request = self.protocol.build_write_registers(self.address, int(register), values)
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_write_response_size())
        )
        return self.protocol.parse_write_registers(response, self.address, int(register), len(values))

    def read_status(self) -> MotorStatus:
        raw = self.read_register(MSeriesRegister.STATUS)
        try:
            return MotorStatus(raw)
        except ValueError:
            return MotorStatus.ERROR

    def read_actual_position(self) -> int:
        high, low = self.read_registers(MSeriesRegister.ACTUAL_POSITION_HIGH, 2)
        return registers_to_int32(high, low)

    def read_actual_speed(self) -> int:
        return register_to_int16(self.read_register(MSeriesRegister.ACTUAL_SPEED))

    def move_to(self, steps: int, *, rpm: float = 60, acceleration_ms: int = 200) -> None:
        spec = get_stepper_spec(self.series)
        high, low = int32_to_registers(steps)
        values = [high, low, 0, speed_to_register(rpm), int(acceleration_ms), 0]
        self.write_registers(spec.position_high, values)

    def stop(self) -> None:
        self.write_register(MSeriesRegister.EMERGENCY_STOP, 1)

    def set_actual_position_zero(self) -> None:
        self.write_registers(MSeriesRegister.ACTUAL_POSITION_HIGH, [0, 0])

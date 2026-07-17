"""Small Modbus RTU protocol helper."""

from __future__ import annotations

from enum import IntEnum

from .crc import append_crc, verify_crc
from ..core.exceptions import CRCError, ModbusException, ProtocolError


class FunctionCode(IntEnum):
    READ_COILS = 0x01
    READ_HOLDING_REGISTERS = 0x03
    WRITE_SINGLE_COIL = 0x05
    WRITE_SINGLE_REGISTER = 0x06
    WRITE_MULTIPLE_REGISTERS = 0x10


def _validate_slave_address(address: int) -> None:
    if not 1 <= int(address) <= 247:
        raise ProtocolError(f"invalid slave address: {address}")


def _validate_u16(value: int) -> None:
    if not 0 <= int(value) <= 0xFFFF:
        raise ProtocolError(f"invalid u16 value: {value}")


def _u16_pair(value: int) -> bytes:
    _validate_u16(value)
    return int(value).to_bytes(2, "big")


class ModbusRTUProtocol:
    def build_read_registers(self, address: int, register: int, count: int = 1) -> bytes:
        _validate_slave_address(address)
        return append_crc(
            bytes((int(address), FunctionCode.READ_HOLDING_REGISTERS))
            + _u16_pair(register)
            + _u16_pair(count)
        )

    def parse_read_registers(self, response: bytes, address: int, count: int) -> list[int]:
        self._check_response(response, address, FunctionCode.READ_HOLDING_REGISTERS)
        byte_count = response[2]
        if byte_count != count * 2:
            raise ProtocolError(f"unexpected register byte count: {byte_count}")
        return [
            int.from_bytes(response[3 + i * 2 : 5 + i * 2], "big")
            for i in range(count)
        ]

    def build_write_register(self, address: int, register: int, value: int) -> bytes:
        _validate_slave_address(address)
        return append_crc(
            bytes((int(address), FunctionCode.WRITE_SINGLE_REGISTER))
            + _u16_pair(register)
            + _u16_pair(value)
        )

    def parse_write_register(self, response: bytes, address: int, register: int, value: int) -> bool:
        self._check_response(response, address, FunctionCode.WRITE_SINGLE_REGISTER)
        expected = bytes((int(address), FunctionCode.WRITE_SINGLE_REGISTER)) + _u16_pair(register) + _u16_pair(value)
        if response[:-2] != expected:
            raise ProtocolError("write register echo mismatch")
        return True

    def build_write_registers(self, address: int, register: int, values: list[int]) -> bytes:
        _validate_slave_address(address)
        body = (
            bytes((int(address), FunctionCode.WRITE_MULTIPLE_REGISTERS))
            + _u16_pair(register)
            + _u16_pair(len(values))
            + bytes((len(values) * 2,))
            + b"".join(_u16_pair(v) for v in values)
        )
        return append_crc(body)

    def parse_write_registers(self, response: bytes, address: int, register: int, count: int) -> bool:
        self._check_response(response, address, FunctionCode.WRITE_MULTIPLE_REGISTERS)
        expected = bytes((int(address), FunctionCode.WRITE_MULTIPLE_REGISTERS)) + _u16_pair(register) + _u16_pair(count)
        if response[:-2] != expected:
            raise ProtocolError("write registers echo mismatch")
        return True

    def build_write_coil(self, address: int, coil: int, value: bool) -> bytes:
        _validate_slave_address(address)
        raw = 0xFF00 if value else 0x0000
        return append_crc(bytes((int(address), FunctionCode.WRITE_SINGLE_COIL)) + _u16_pair(coil) + _u16_pair(raw))

    def parse_write_coil(self, response: bytes, address: int, coil: int, value: bool) -> bool:
        self._check_response(response, address, FunctionCode.WRITE_SINGLE_COIL)
        raw = 0xFF00 if value else 0x0000
        expected = bytes((int(address), FunctionCode.WRITE_SINGLE_COIL)) + _u16_pair(coil) + _u16_pair(raw)
        if response[:-2] != expected:
            raise ProtocolError("write coil echo mismatch")
        return True

    def expected_read_response_size(self, count: int) -> int:
        return 5 + int(count) * 2

    def expected_write_response_size(self) -> int:
        return 8

    def _check_response(self, response: bytes, address: int, function: int) -> None:
        if len(response) < 5:
            raise ProtocolError(f"short response: {response!r}")
        if response[0] != int(address):
            raise ProtocolError(f"unexpected slave address: {response[0]}")
        if not verify_crc(response):
            raise CRCError(f"crc check failed: {response.hex()}")
        if response[1] & 0x80:
            raise ModbusException(f"modbus exception code: {response[2]}")
        if response[1] != int(function):
            raise ProtocolError(f"unexpected function: {response[1]}")


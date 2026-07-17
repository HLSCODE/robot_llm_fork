"""Minimal tapping relay device client."""

from __future__ import annotations

from ...core.strategies import ModbusRTUStrategy
from ...core.transport import SerialTransport, Transport
from ...protocols.modbus_rtu import ModbusRTUProtocol
from .registers import DEFAULT_TAPPING_DEVICE_ADDRESS, channel_to_coil


class TappingDevice:
    def __init__(self, transport: Transport | str, *, address: int = DEFAULT_TAPPING_DEVICE_ADDRESS) -> None:
        self.transport = SerialTransport(transport) if isinstance(transport, str) else transport
        self.address = int(address)
        self.protocol = ModbusRTUProtocol()

    def set_channel(self, channel: int, value: bool) -> bool:
        coil = channel_to_coil(channel)
        request = self.protocol.build_write_coil(self.address, coil, value)
        response = self.transport.transact_with_strategy(
            ModbusRTUStrategy(request, self.protocol.expected_write_response_size())
        )
        return self.protocol.parse_write_coil(response, self.address, coil, value)

    def on(self, channel: int) -> bool:
        return self.set_channel(channel, True)

    def off(self, channel: int) -> bool:
        return self.set_channel(channel, False)


from __future__ import annotations

import unittest

from src.devices.transports import (
    SerialSettings,
    SerialTransport,
    TransportError,
    TransportErrorCategory,
)


class _FakeSerial:
    def __init__(self, *, port=None, incoming: bytes = b"", **_options) -> None:
        self.port = port
        self.incoming = incoming
        self.is_open = port is not None
        self.rts = True
        self.dtr = True
        self.written = bytearray()
        self.open_state: tuple[object, bool, bool] | None = None

    def open(self) -> None:
        self.open_state = (self.port, self.rts, self.dtr)
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        return None

    def write(self, payload: bytes) -> int:
        self.written.extend(payload)
        return len(payload)

    def flush(self) -> None:
        return None

    def read(self, size: int) -> bytes:
        result = self.incoming[:size]
        self.incoming = self.incoming[size:]
        return result


class SerialTransportTests(unittest.TestCase):
    def test_open_retry_is_bounded_and_close_is_idempotent(self) -> None:
        attempts = 0
        serial_port = _FakeSerial(port="COM9")

        def factory(**_options):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError("port busy")
            return serial_port

        transport = SerialTransport(
            SerialSettings(
                port="COM9",
                open_attempts=3,
                open_retry_delay_seconds=0,
            ),
            serial_factory=factory,
        )

        self.assertEqual(3, attempts)
        self.assertTrue(transport.is_open)
        transport.close()
        transport.close()
        self.assertFalse(transport.is_open)

    def test_rts_and_dtr_are_set_before_open(self) -> None:
        serial_port = _FakeSerial()
        transport = SerialTransport(
            SerialSettings(port="COM8", rts=False, dtr=False),
            serial_factory=lambda **_options: serial_port,
        )

        self.assertEqual(("COM8", False, False), serial_port.open_state)
        transport.close()

    def test_closed_and_timeout_errors_have_stable_categories(self) -> None:
        serial_port = _FakeSerial(port="COM7")
        transport = SerialTransport(
            "COM7",
            serial_factory=lambda **_options: serial_port,
        )

        with self.assertRaises(TransportError) as timeout:
            transport.transact(b"request", 1)
        self.assertEqual(
            TransportErrorCategory.TIMEOUT,
            timeout.exception.category,
        )

        transport.close()
        with self.assertRaises(TransportError) as closed:
            transport.transact(b"request", 1)
        self.assertEqual(
            TransportErrorCategory.CLOSED,
            closed.exception.category,
        )
        self.assertEqual("COM7", closed.exception.port)

    def test_open_failure_preserves_endpoint_and_attempt_count(self) -> None:
        def fail(**_options):
            raise OSError("access denied")

        with self.assertRaises(TransportError) as raised:
            SerialTransport(
                SerialSettings(port="COM6", open_attempts=2),
                serial_factory=fail,
            )

        self.assertEqual(
            TransportErrorCategory.OPEN_FAILED,
            raised.exception.category,
        )
        self.assertEqual("COM6", raised.exception.port)
        self.assertIn("2 attempt(s)", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

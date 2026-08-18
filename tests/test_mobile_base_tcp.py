from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch

from src.configuration.settings import RobotSettings
from src.devices.motion.mobile_base.tcp.client import TcpMobileBaseClient
from src.devices.motion.mobile_base.tcp.provider import TcpMobileBaseProvider


class _FakeSocket:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.calls.append(("setsockopt", (level, option, value)))

    def settimeout(self, timeout: float) -> None:
        self.calls.append(("settimeout", timeout))

    def bind(self, address: tuple[str, int]) -> None:
        self.calls.append(("bind", address))

    def connect(self, address: tuple[str, int]) -> None:
        self.calls.append(("connect", address))

    def close(self) -> None:
        self.calls.append(("close", None))


class TcpMobileBaseTests(unittest.TestCase):
    def test_client_applies_timeout_before_connecting(self) -> None:
        fake_socket = _FakeSocket()
        client = TcpMobileBaseClient(
            "192.0.2.10",
            12345,
            bind_port=54321,
            timeout_seconds=2.5,
        )

        with patch.object(socket, "socket", return_value=fake_socket):
            client.connect()

        call_names = [name for name, _value in fake_socket.calls]
        self.assertLess(call_names.index("settimeout"), call_names.index("connect"))
        self.assertIn(("settimeout", 2.5), fake_socket.calls)
        self.assertIn(("bind", ("", 54321)), fake_socket.calls)

    def test_client_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout must be positive"):
            TcpMobileBaseClient("192.0.2.10", 12345, timeout_seconds=0)

    def test_robot_settings_reject_non_positive_mobile_base_timeout(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "move_controller_timeout_seconds must be positive",
        ):
            RobotSettings(move_controller_timeout_seconds=0)

    def test_provider_forwards_configured_timeout(self) -> None:
        settings = RobotSettings(move_controller_timeout_seconds=3.5)
        client = MagicMock()
        adapter = MagicMock()

        with (
            patch(
                "src.devices.motion.mobile_base.tcp.provider.TcpMobileBaseClient",
                return_value=client,
            ) as client_factory,
            patch(
                "src.devices.motion.mobile_base.tcp.provider.TcpMobileBaseAdapter",
                return_value=adapter,
            ),
        ):
            result = TcpMobileBaseProvider().create(settings)

        self.assertIs(adapter, result)
        client_factory.assert_called_once_with(
            host=settings.move_controller_host,
            port=settings.move_controller_port,
            bind_port=settings.move_controller_client_bind_port,
            timeout_seconds=3.5,
        )
        adapter.connect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

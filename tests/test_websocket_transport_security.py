from __future__ import annotations

import ssl
import unittest
from unittest.mock import MagicMock, patch

from src.robot_server.security import (
    create_server_ssl_context,
    normalize_allowed_origins,
)
from src.robot_server.ws_server import RobotWebSocketServer


class WebSocketTransportSecurityTests(unittest.TestCase):
    def test_origins_are_validated_and_deduplicated(self):
        self.assertEqual(
            ("https://robot.example", "http://localhost:3000"),
            normalize_allowed_origins(
                (
                    " https://robot.example ",
                    "https://robot.example",
                    "http://localhost:3000",
                )
            ),
        )
        for invalid in (
            "robot.example",
            "ftp://robot.example",
            "https://user:password@robot.example",
            "https://robot.example/path",
        ):
            with self.subTest(origin=invalid), self.assertRaises(ValueError):
                normalize_allowed_origins((invalid,))

    def test_tls_context_requires_a_pair_and_loads_the_certificate(self):
        self.assertIsNone(create_server_ssl_context("", ""))
        with self.assertRaises(ValueError):
            create_server_ssl_context("server.crt", "")

        context = MagicMock()
        with patch(
            "src.robot_server.security.transport.ssl.SSLContext",
            return_value=context,
        ):
            result = create_server_ssl_context("server.crt", "server.key")

        self.assertIs(context, result)
        self.assertEqual(ssl.TLSVersion.TLSv1_2, context.minimum_version)
        context.load_cert_chain.assert_called_once_with(
            certfile="server.crt",
            keyfile="server.key",
        )

    def test_remote_and_proxy_modes_enforce_secure_boundaries(self):
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            RobotWebSocketServer(
                services=object(),
                host="0.0.0.0",
                auth_token="token",
                allowed_origins=("https://robot.example",),
            )
        with self.assertRaisesRegex(ValueError, "requires authentication"):
            RobotWebSocketServer(
                services=object(),
                reverse_proxy_mode=True,
                allowed_origins=("https://robot.example",),
            )
        server = RobotWebSocketServer(
            services=object(),
            auth_token="token",
            reverse_proxy_mode=True,
            allowed_origins=("https://robot.example",),
        )
        self.assertEqual("ws://127.0.0.1:8765/", server.endpoint)


if __name__ == "__main__":
    unittest.main()

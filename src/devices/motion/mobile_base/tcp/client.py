"""Transport client for the mobile-base TCP protocol."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from typing import Any


class TcpMobileBaseClient:
    """Own a single TCP connection and exchange JSON protocol messages."""

    def __init__(
        self,
        host: str,
        port: int,
        bind_port: int | None = None,
        *,
        receive_size: int = 4096,
    ) -> None:
        self._host = host
        self._port = port
        self._bind_port = bind_port
        self._receive_size = receive_size
        self._socket: socket.socket | None = None
        self._receive_buffer = ""

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            return
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            if self._bind_port is not None:
                connection.bind(("", self._bind_port))
            connection.connect((self._host, self._port))
        except Exception:
            connection.close()
            raise
        self._socket = connection

    def send_command(self, command: Mapping[str, Any]) -> None:
        connection = self._require_connection()
        payload = json.dumps(dict(command), separators=(",", ":")).encode("utf-8")
        connection.sendall(payload)

    def receive_response(self) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        connection = self._require_connection()
        while True:
            stripped = self._receive_buffer.lstrip()
            if stripped:
                try:
                    response, end = decoder.raw_decode(stripped)
                except json.JSONDecodeError:
                    pass
                else:
                    self._receive_buffer = stripped[end:]
                    if not isinstance(response, dict):
                        raise ValueError("mobile-base response must be a JSON object")
                    return response
            data = connection.recv(self._receive_size)
            if not data:
                raise ConnectionError("mobile-base connection closed by peer")
            self._receive_buffer += data.decode("utf-8")

    def close(self) -> None:
        connection, self._socket = self._socket, None
        self._receive_buffer = ""
        if connection is not None:
            connection.close()

    def _require_connection(self) -> socket.socket:
        if self._socket is None:
            raise ConnectionError("mobile-base client is not connected")
        return self._socket


__all__ = ["TcpMobileBaseClient"]

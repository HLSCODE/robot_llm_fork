"""Application-owned UDP localization input."""

from __future__ import annotations

import json
import socket
from threading import Event, Lock, Thread
import time
from typing import Any


class LocalizationService:
    """Own one lazy UDP receiver and expose only fresh normalized readings."""

    def __init__(self, host: str = "0.0.0.0", port: int = 22222) -> None:
        if not host.strip():
            raise ValueError("localization host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("localization port must be in 1..65535")
        self._host = host
        self._port = port
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._lock = Lock()
        self._lifecycle_lock = Lock()
        self._latest: dict[str, Any] | None = None
        self._last_error: str | None = None

    def latest(
        self,
        *,
        max_age: float,
        valid_only: bool = True,
        wait_timeout: float = 0.0,
    ) -> dict[str, Any] | None:
        if max_age <= 0:
            raise ValueError("localization max age must be positive")
        if wait_timeout < 0:
            raise ValueError("localization wait timeout must not be negative")
        self._start()
        deadline = time.monotonic() + wait_timeout
        while True:
            with self._lock:
                latest = dict(self._latest) if self._latest else None
            if latest is not None:
                age = time.time() - float(latest["timestamp"])
                if age <= max_age and (
                    not valid_only or latest["id"] != -99
                ):
                    return latest
            if time.monotonic() >= deadline:
                return None
            self._stop_event.wait(min(0.05, max(0.0, deadline - time.monotonic())))

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def close(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            sock = self._socket
            self._socket = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
            self._thread = None

    def _start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(0.2)
                sock.bind((self._host, self._port))
            except Exception:
                sock.close()
                raise
            self._socket = sock
            self._thread = Thread(
                target=self._receive_loop,
                name="localization-udp",
                daemon=True,
            )
            self._thread.start()

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._socket
            if sock is None:
                return
            try:
                data, _address = sock.recvfrom(1024)
                reading = self.normalize_payload(
                    json.loads(data.decode("utf-8"))
                )
                with self._lock:
                    self._latest = reading
                    self._last_error = None
            except socket.timeout:
                continue
            except OSError:
                return
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)

    @staticmethod
    def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(payload.get("id", -99)),
            "x": float(payload.get("x", payload.get("X", 0.0))),
            "y": float(payload.get("y", payload.get("Y", 0.0))),
            "angle": float(
                payload.get(
                    "angle",
                    payload.get(
                        "Angle",
                        payload.get("angel", payload.get("Angel", 0.0)),
                    ),
                )
            ),
            "timestamp": time.time(),
            "raw": payload,
        }

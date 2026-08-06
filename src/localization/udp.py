"""UDP provider for the external tag-localization protocol."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from threading import Event, Lock, Thread
import time

from ..configuration.settings import LocalizationSettings
from .models import (
    ExternalLocalizationReading,
    parse_external_localization_payload,
)


class UdpExternalLocalizationProvider:
    """Own the UDP socket, receiver thread, and latest protocol reading."""

    def __init__(
        self,
        settings: LocalizationSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not settings.external_localization_host.strip():
            raise ValueError("external localization host must not be empty")
        if not 1 <= settings.external_localization_port <= 65535:
            raise ValueError("external localization port must be in 1..65535")
        self._settings = settings
        self._clock = clock
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._state_lock = Lock()
        self._lifecycle_lock = Lock()
        self._latest: ExternalLocalizationReading | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                receiver.settimeout(
                    self._settings.external_localization_socket_timeout_seconds
                )
                receiver.bind(
                    (
                        self._settings.external_localization_host,
                        self._settings.external_localization_port,
                    )
                )
            except Exception:
                receiver.close()
                raise
            self._socket = receiver
            self._thread = Thread(
                target=self._receive_loop,
                name="external-localization-udp",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> ExternalLocalizationReading | None:
        with self._state_lock:
            return self._latest

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def close(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            receiver, self._socket = self._socket, None
            if receiver is not None:
                receiver.close()
            thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(
                timeout=self._settings.external_localization_join_timeout_seconds
            )

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            receiver = self._socket
            if receiver is None:
                return
            try:
                data, _address = receiver.recvfrom(
                    self._settings.external_localization_receive_size_bytes
                )
                payload = json.loads(data.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("external localization payload must be an object")
                reading = parse_external_localization_payload(
                    payload,
                    received_at=self._clock(),
                )
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    return
                self._record_error("external localization socket failed")
                return
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                self._record_error(str(exc))
                continue
            with self._state_lock:
                self._latest = reading
                self._last_error = None

    def _record_error(self, message: str) -> None:
        with self._state_lock:
            self._last_error = message


class NullExternalLocalizationProvider:
    """No-I/O provider used by simulation mode."""

    def start(self) -> None:
        return

    def snapshot(self) -> ExternalLocalizationReading | None:
        return None

    @property
    def last_error(self) -> str | None:
        return None

    def close(self) -> None:
        return


__all__ = ["NullExternalLocalizationProvider", "UdpExternalLocalizationProvider"]

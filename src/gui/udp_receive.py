from __future__ import annotations

import json
import socket
import threading
import time
from typing import Optional


UDP_IP = "0.0.0.0"
UDP_PORT = 22222

_receiver: "LocalizationReceiver | None" = None


class LocalizationReceiver:
    """Background UDP receiver for the latest localization offset."""

    def __init__(self, ip: str = UDP_IP, port: int = UDP_PORT):
        self.ip = ip
        self.port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.2)
            sock.bind((self.ip, self.port))
        except Exception:
            sock.close()
            raise
        self._sock = sock

        self._thread = threading.Thread(target=self._recv_loop, name="localization-udp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def get_latest(
        self,
        max_age: float = 2.0,
        valid_only: bool = True,
        wait_timeout: float = 0.0,
    ) -> Optional[dict]:
        self.start()
        deadline = time.time() + max(0.0, wait_timeout)

        while True:
            with self._lock:
                latest = dict(self._latest) if self._latest else None

            if latest is not None:
                age = time.time() - float(latest.get("timestamp", 0.0))
                valid = latest.get("id") != -99
                if age <= max_age and (valid or not valid_only):
                    return latest

            if time.time() >= deadline:
                return None
            time.sleep(0.05)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def _recv_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                data, _addr = sock.recvfrom(1024)
                payload = json.loads(data.decode("utf-8"))
                latest = self._normalize_payload(payload)
                with self._lock:
                    self._latest = latest
                    self._last_error = None
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                time.sleep(0.1)

    def _normalize_payload(self, payload: dict) -> dict:
        return {
            "id": int(payload.get("id", -99)),
            "x": float(payload.get("x", payload.get("X", 0.0))),
            "y": float(payload.get("y", payload.get("Y", 0.0))),
            "angle": float(payload.get("angle", payload.get("Angle", payload.get("angel", payload.get("Angel", 0.0))))),
            "timestamp": time.time(),
            "raw": payload,
        }


def get_localization_receiver() -> LocalizationReceiver:
    global _receiver
    if _receiver is None:
        _receiver = LocalizationReceiver()
    return _receiver


def get_latest_position(max_age: float = 2.0, wait_timeout: float = 1.0) -> Optional[dict]:
    return get_localization_receiver().get_latest(max_age=max_age, wait_timeout=wait_timeout)


def udp_rev():
    """Compatibility wrapper for older scripts that expected one latest packet."""
    position = get_latest_position(max_age=10.0, wait_timeout=10.0)
    return position if position is not None else False


if __name__ == "__main__":
    receiver = get_localization_receiver()
    receiver.start()
    print(f"UDP listening on {UDP_IP}:{UDP_PORT}")
    try:
        while True:
            position = receiver.get_latest(max_age=10.0, valid_only=False, wait_timeout=1.0)
            if position is None:
                print("No localization data")
            elif position["id"] == -99:
                print("Tag not detected")
            else:
                print(
                    f"Tag ID: {position['id']} "
                    f"X: {position['x']:.3f}cm "
                    f"Y: {position['y']:.3f}cm "
                    f"Angle: {position['angle']:.3f}deg"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        receiver.stop()

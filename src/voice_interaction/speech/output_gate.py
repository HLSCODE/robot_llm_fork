"""Thread-safe state shared by audio playback and speech input."""
from __future__ import annotations

from threading import Lock


class AudioOutputGate:
    """Indicate that robot audio is currently reaching the speaker."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._playing = False

    def begin_playback(self) -> None:
        with self._lock:
            self._playing = True

    def end_playback(self) -> None:
        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

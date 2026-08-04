from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from ..protocol.messages import WebSocketErrorCode


@dataclass(frozen=True, slots=True)
class RequestAdmission:
    accepted: bool
    code: str | None = None
    retry_after_seconds: float | None = None


class WebSocketRequestLimiter:
    """Limit per-client request rate and server-wide active requests.

    The WebSocket event loop owns this object. It intentionally uses no locks;
    callers must admit and release requests on that same event loop.
    """

    _WINDOW_SECONDS = 1.0

    def __init__(
        self,
        *,
        max_requests_per_second: int,
        max_concurrent_requests: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second must be positive")
        if max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be positive")
        self._max_requests_per_second = max_requests_per_second
        self._max_concurrent_requests = max_concurrent_requests
        self._clock = clock
        self._recent_requests: dict[str, deque[float]] = {}
        self._active_by_client: dict[str, int] = {}
        self._active_total = 0

    def admit(self, client_id: str) -> RequestAdmission:
        now = self._clock()
        recent = self._recent_requests.setdefault(client_id, deque())
        window_start = now - self._WINDOW_SECONDS
        while recent and recent[0] <= window_start:
            recent.popleft()

        if len(recent) >= self._max_requests_per_second:
            retry_after = self._WINDOW_SECONDS - (now - recent[0])
            return RequestAdmission(
                accepted=False,
                code=WebSocketErrorCode.RATE_LIMITED.value,
                retry_after_seconds=max(0.001, retry_after),
            )

        recent.append(now)
        if self._active_total >= self._max_concurrent_requests:
            return RequestAdmission(
                accepted=False,
                code=WebSocketErrorCode.SERVER_BUSY.value,
            )

        self._active_total += 1
        self._active_by_client[client_id] = (
            self._active_by_client.get(client_id, 0) + 1
        )
        return RequestAdmission(accepted=True)

    def release(self, client_id: str) -> None:
        active = self._active_by_client.get(client_id, 0)
        if active <= 0:
            raise RuntimeError("request admission released without acquire")
        if active == 1:
            self._active_by_client.pop(client_id)
        else:
            self._active_by_client[client_id] = active - 1
        self._active_total -= 1

    def unregister(self, client_id: str) -> None:
        self._recent_requests.pop(client_id, None)

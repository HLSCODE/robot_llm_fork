"""Thread-safe WebSocket transport metrics."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable


@dataclass(frozen=True, slots=True)
class WebSocketMetricsSnapshot:
    connections_active: int
    connections_peak: int
    connections_total: int
    requests_active: int
    requests_peak: int
    requests_total: int
    invalid_requests_total: int
    rate_limited_total: int
    server_busy_total: int
    access_denied_total: int
    internal_errors_total: int
    request_duration_seconds_total: float
    request_duration_seconds_max: float
    messages_sent_total: int
    send_failures_total: int
    slow_sends_total: int
    send_timeouts_total: int
    slow_client_disconnects_total: int
    send_duration_seconds_total: float
    send_duration_seconds_max: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "connections_active": self.connections_active,
            "connections_peak": self.connections_peak,
            "connections_total": self.connections_total,
            "requests_active": self.requests_active,
            "requests_peak": self.requests_peak,
            "requests_total": self.requests_total,
            "invalid_requests_total": self.invalid_requests_total,
            "rate_limited_total": self.rate_limited_total,
            "server_busy_total": self.server_busy_total,
            "access_denied_total": self.access_denied_total,
            "internal_errors_total": self.internal_errors_total,
            "request_duration_seconds_total": self.request_duration_seconds_total,
            "request_duration_seconds_max": self.request_duration_seconds_max,
            "messages_sent_total": self.messages_sent_total,
            "send_failures_total": self.send_failures_total,
            "slow_sends_total": self.slow_sends_total,
            "send_timeouts_total": self.send_timeouts_total,
            "slow_client_disconnects_total": self.slow_client_disconnects_total,
            "send_duration_seconds_total": self.send_duration_seconds_total,
            "send_duration_seconds_max": self.send_duration_seconds_max,
        }


class WebSocketMetrics:
    """Own aggregate WebSocket service metrics without retaining client data."""

    def __init__(
        self,
        *,
        slow_send_threshold_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if slow_send_threshold_seconds <= 0:
            raise ValueError("slow_send_threshold_seconds must be positive")
        self._slow_send_threshold_seconds = slow_send_threshold_seconds
        self._clock = clock
        self._lock = Lock()
        self._connections_active = 0
        self._connections_peak = 0
        self._connections_total = 0
        self._requests_active = 0
        self._requests_peak = 0
        self._requests_total = 0
        self._invalid_requests_total = 0
        self._rate_limited_total = 0
        self._server_busy_total = 0
        self._access_denied_total = 0
        self._internal_errors_total = 0
        self._request_duration_seconds_total = 0.0
        self._request_duration_seconds_max = 0.0
        self._messages_sent_total = 0
        self._send_failures_total = 0
        self._slow_sends_total = 0
        self._send_timeouts_total = 0
        self._slow_client_disconnects_total = 0
        self._send_duration_seconds_total = 0.0
        self._send_duration_seconds_max = 0.0

    def connection_opened(self) -> None:
        with self._lock:
            self._connections_active += 1
            self._connections_total += 1
            self._connections_peak = max(
                self._connections_peak,
                self._connections_active,
            )

    def connection_closed(self) -> None:
        with self._lock:
            if self._connections_active <= 0:
                raise RuntimeError("connection metric closed without open")
            self._connections_active -= 1

    def request_started(self) -> float:
        started_at = self._clock()
        with self._lock:
            self._requests_active += 1
            self._requests_total += 1
            self._requests_peak = max(
                self._requests_peak,
                self._requests_active,
            )
        return started_at

    def request_finished(self, started_at: float) -> None:
        duration_seconds = max(0.0, self._clock() - started_at)
        with self._lock:
            if self._requests_active <= 0:
                raise RuntimeError("request metric finished without start")
            self._requests_active -= 1
            self._request_duration_seconds_total += duration_seconds
            self._request_duration_seconds_max = max(
                self._request_duration_seconds_max,
                duration_seconds,
            )

    def record_invalid_request(self) -> None:
        self._increment("_invalid_requests_total")

    def record_rate_limited(self) -> None:
        self._increment("_rate_limited_total")

    def record_server_busy(self) -> None:
        self._increment("_server_busy_total")

    def record_access_denied(self) -> None:
        self._increment("_access_denied_total")

    def record_internal_error(self) -> None:
        self._increment("_internal_errors_total")

    def send_started(self) -> float:
        return self._clock()

    def send_succeeded(self, started_at: float) -> None:
        duration_seconds = max(0.0, self._clock() - started_at)
        with self._lock:
            self._messages_sent_total += 1
            self._record_send_duration_unlocked(duration_seconds)

    def send_failed(self, started_at: float, *, timed_out: bool) -> None:
        duration_seconds = max(0.0, self._clock() - started_at)
        with self._lock:
            self._send_failures_total += 1
            if timed_out:
                self._send_timeouts_total += 1
                self._slow_client_disconnects_total += 1
            self._record_send_duration_unlocked(duration_seconds)

    def snapshot(self) -> WebSocketMetricsSnapshot:
        with self._lock:
            return WebSocketMetricsSnapshot(
                connections_active=self._connections_active,
                connections_peak=self._connections_peak,
                connections_total=self._connections_total,
                requests_active=self._requests_active,
                requests_peak=self._requests_peak,
                requests_total=self._requests_total,
                invalid_requests_total=self._invalid_requests_total,
                rate_limited_total=self._rate_limited_total,
                server_busy_total=self._server_busy_total,
                access_denied_total=self._access_denied_total,
                internal_errors_total=self._internal_errors_total,
                request_duration_seconds_total=(self._request_duration_seconds_total),
                request_duration_seconds_max=self._request_duration_seconds_max,
                messages_sent_total=self._messages_sent_total,
                send_failures_total=self._send_failures_total,
                slow_sends_total=self._slow_sends_total,
                send_timeouts_total=self._send_timeouts_total,
                slow_client_disconnects_total=(self._slow_client_disconnects_total),
                send_duration_seconds_total=self._send_duration_seconds_total,
                send_duration_seconds_max=self._send_duration_seconds_max,
            )

    def _increment(self, attribute: str) -> None:
        with self._lock:
            setattr(self, attribute, getattr(self, attribute) + 1)

    def _record_send_duration_unlocked(self, duration_seconds: float) -> None:
        self._send_duration_seconds_total += duration_seconds
        self._send_duration_seconds_max = max(
            self._send_duration_seconds_max,
            duration_seconds,
        )
        if duration_seconds >= self._slow_send_threshold_seconds:
            self._slow_sends_total += 1

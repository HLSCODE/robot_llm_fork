from __future__ import annotations

import unittest

from src.robot_server.metrics import WebSocketMetrics


class _Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


class WebSocketMetricsTests(unittest.TestCase):
    def test_metrics_track_peaks_latency_and_slow_sends(self):
        clock = _Clock()
        metrics = WebSocketMetrics(
            slow_send_threshold_seconds=0.5,
            clock=clock,
        )

        metrics.connection_opened()
        metrics.connection_opened()
        metrics.connection_closed()
        request_started = metrics.request_started()
        clock.now += 0.25
        metrics.request_finished(request_started)
        send_started = metrics.send_started()
        clock.now += 0.75
        metrics.send_succeeded(send_started)
        failed_send_started = metrics.send_started()
        clock.now += 1.0
        metrics.send_failed(failed_send_started, timed_out=True)
        metrics.record_invalid_request()
        metrics.record_rate_limited()
        metrics.record_server_busy()
        metrics.record_access_denied()
        metrics.record_internal_error()

        snapshot = metrics.snapshot()
        self.assertEqual(1, snapshot.connections_active)
        self.assertEqual(2, snapshot.connections_peak)
        self.assertEqual(2, snapshot.connections_total)
        self.assertEqual(1, snapshot.requests_total)
        self.assertEqual(0.25, snapshot.request_duration_seconds_max)
        self.assertEqual(1, snapshot.messages_sent_total)
        self.assertEqual(1, snapshot.send_failures_total)
        self.assertEqual(2, snapshot.slow_sends_total)
        self.assertEqual(1, snapshot.send_timeouts_total)
        self.assertEqual(1, snapshot.slow_client_disconnects_total)
        self.assertEqual(1, snapshot.invalid_requests_total)
        self.assertEqual(1, snapshot.rate_limited_total)
        self.assertEqual(1, snapshot.server_busy_total)
        self.assertEqual(1, snapshot.access_denied_total)
        self.assertEqual(1, snapshot.internal_errors_total)

    def test_metrics_reject_unbalanced_lifecycle_events(self):
        metrics = WebSocketMetrics(slow_send_threshold_seconds=0.5)

        with self.assertRaises(RuntimeError):
            metrics.connection_closed()
        with self.assertRaises(RuntimeError):
            metrics.request_finished(0.0)


if __name__ == "__main__":
    unittest.main()

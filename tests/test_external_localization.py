from __future__ import annotations

import json
import socket
import time
import unittest

from src.application.external_localization import ExternalLocalizationService
from src.configuration.settings import LocalizationSettings
from src.localization.models import (
    ExternalLocalizationReading,
    parse_external_localization_payload,
)
from src.localization.udp import UdpExternalLocalizationProvider


class _FakeProvider:
    def __init__(self, reading: ExternalLocalizationReading | None) -> None:
        self.reading = reading
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def snapshot(self) -> ExternalLocalizationReading | None:
        return self.reading

    @property
    def last_error(self) -> str | None:
        return None

    def close(self) -> None:
        self.closed = True


class ExternalLocalizationServiceTests(unittest.TestCase):
    def test_service_applies_freshness_policy_to_injected_provider(self) -> None:
        reading = parse_external_localization_payload(
            {"id": 7, "X": 1.5, "Y": -2.0, "Angel": 45},
            received_at=100.0,
        )
        provider = _FakeProvider(reading)
        service = ExternalLocalizationService(provider, wall_clock=lambda: 101.0)

        result = service.latest(max_age=2.0)

        self.assertTrue(provider.started)
        self.assertEqual(7, result["id"] if result else None)
        self.assertEqual(45.0, result["angle"] if result else None)

    def test_service_rejects_stale_or_invalid_readings(self) -> None:
        invalid = parse_external_localization_payload(
            {"id": -99},
            received_at=10.0,
        )
        service = ExternalLocalizationService(
            _FakeProvider(invalid),
            wall_clock=lambda: 11.0,
        )

        self.assertIsNone(service.latest(max_age=2.0, valid_only=True))
        self.assertIsNotNone(service.latest(max_age=2.0, valid_only=False))

    def test_close_delegates_transport_lifecycle(self) -> None:
        provider = _FakeProvider(None)
        service = ExternalLocalizationService(provider)

        service.close()

        self.assertTrue(provider.closed)


class UdpExternalLocalizationProviderTests(unittest.TestCase):
    def test_udp_provider_receives_and_normalizes_one_datagram(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        provider = UdpExternalLocalizationProvider(
            LocalizationSettings(
                external_localization_host="127.0.0.1",
                external_localization_port=port,
                external_localization_socket_timeout_seconds=0.02,
            )
        )
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sender.close)
        self.addCleanup(provider.close)
        provider.start()

        sender.sendto(
            json.dumps({"id": 3, "x": 2, "y": 4, "angle": 90}).encode(),
            ("127.0.0.1", port),
        )
        deadline = time.monotonic() + 1.0
        reading = provider.snapshot()
        while reading is None and time.monotonic() < deadline:
            time.sleep(0.01)
            reading = provider.snapshot()

        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertEqual(3, reading.tag_id)
        self.assertEqual(2.0, reading.x_cm)
        self.assertEqual(90.0, reading.angle_degrees)


if __name__ == "__main__":
    unittest.main()

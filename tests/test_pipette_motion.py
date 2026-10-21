from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.devices.tools.pipette import driver
from src.devices.tools.pipette.driver import ADP
from src.devices.transports import CRCError, ProtocolError, TransportError, TransportErrorCategory
from src.devices.transports.testing import FakeTransport


def _frame(body: bytes) -> bytes:
    return body + f"{ADP._cal_crc(body):04X}".encode("ascii") + b"\r\n"


@pytest.fixture
def motion_clock(monkeypatch):
    class Clock:
        elapsed = 0.0

        def monotonic(self):
            return self.elapsed

        def sleep(self, seconds):
            self.elapsed += seconds

    clock = Clock()
    monkeypatch.setattr(driver, "time", clock)
    return clock


@pytest.mark.parametrize("operation,function,value", [
    ("absorb", b"n", 200),
    ("dispense", b"p", 200),
    ("dispense_all", b"p", 0),
])
def test_liquid_motion_waits_for_target_and_never_repeats_command(
    operation, function, value, monkeypatch,
):
    transport = FakeTransport((
        _frame(b">01" + function + b"01"),
        _frame(b">01d00"),
        _frame(b">01d00"),
        _frame(b">01d01"),
    ))
    sleeps = []
    monkeypatch.setattr(driver, "time", SimpleNamespace(
        monotonic=lambda: 0.0, sleep=sleeps.append,
    ))
    pipette = ADP(transport)

    result = getattr(pipette, operation)(*(() if operation == "dispense_all" else (value,)))

    assert result is True
    assert sleeps == [0.1, 0.1, 0.1]
    assert [call.payload for call in transport.calls] == [
        pipette._create_command(function.decode("ascii"), value),
        b">01dB819", b">01dB819", b">01dB819",
    ]


@pytest.mark.parametrize("operation,function", [
    ("absorb", b"n"), ("dispense", b"p"), ("dispense_all", b"p"),
])
@pytest.mark.parametrize("reply", [b"00", b"02", b"", b"FF", b"0100"])
def test_liquid_motion_requires_exact_success_ack(operation, function, reply):
    transport = FakeTransport((_frame(b">01" + function + reply),))
    with pytest.raises(ProtocolError) as raised:
        getattr(ADP(transport), operation)(*(() if operation == "dispense_all" else (10,)))
    assert raised.value.raw_error_code == reply.decode("ascii")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [b"02", b"03", b"05", b"06", b"07", b"09", b"0A", b"FF"])
def test_pump_alarm_or_unknown_status_is_not_completion(status):
    transport = FakeTransport((_frame(b">01n01"), _frame(b">01d" + status)))
    with pytest.raises(ProtocolError) as raised:
        ADP(transport).absorb(10)
    assert raised.value.raw_error_code == status.decode("ascii")
    assert len(transport.calls) == 2


@pytest.mark.parametrize("reply,error_type", [
    (b">01d010000\r\n", CRCError),
    (_frame(b">02d01"), ProtocolError),
    (_frame(b">01p01"), ProtocolError),
    (_frame(b">01d"), ProtocolError),
    (_frame(b">01d0"), ProtocolError),
    (_frame(b">01d0100"), ProtocolError),
    (_frame(b">01dZZ"), ProtocolError),
    (_frame(b">01d01")[:-2], ProtocolError),
])
def test_failed_completion_query_never_returns_success_or_repeats_motion(reply, error_type):
    transport = FakeTransport((_frame(b">01p01"), reply))
    with pytest.raises(error_type):
        ADP(transport).dispense(10)
    assert len(transport.calls) == 2


def test_running_pump_times_out_without_replaying_motion(monkeypatch):
    elapsed = [0.0]

    def sleep(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr(driver, "time", SimpleNamespace(
        monotonic=lambda: elapsed[0], sleep=sleep,
    ))
    transport = FakeTransport((
        _frame(b">01n01"), _frame(b">01d00"), _frame(b">01d00"),
    ))
    with pytest.raises(TransportError, match="completion timed out") as raised:
        ADP(transport, motion_timeout_seconds=0.25).absorb(10)
    assert raised.value.category is TransportErrorCategory.TIMEOUT
    assert raised.value.operation == "wait_for_completion"
    assert elapsed[0] == pytest.approx(0.25)
    assert len(transport.calls) == 3


def test_completion_received_after_deadline_is_a_timeout(motion_clock):
    class LateQueryTransport(FakeTransport):
        def transact_with_strategy(self, strategy):
            if strategy.payload == b">01dB819":
                motion_clock.sleep(1.0)
            return super().transact_with_strategy(strategy)

    transport = LateQueryTransport((_frame(b">01p01"), _frame(b">01d01")))
    with pytest.raises(TransportError, match="completion timed out"):
        ADP(transport, motion_timeout_seconds=0.5).dispense(10)


@pytest.mark.parametrize("operation,function", [
    ("absorb", b"n"), ("dispense", b"p"), ("dispense_all", b"p"),
])
@pytest.mark.parametrize("timeout_operation", ["read", "read_until", "empty_reply"])
def test_completion_read_timeout_recovers_by_querying_status_only(
    operation, function, timeout_operation, motion_clock, caplog,
):
    timeout = (
        b"" if timeout_operation == "empty_reply" else TransportError(
            "serial timeout", category=TransportErrorCategory.TIMEOUT,
            operation=timeout_operation,
        )
    )

    class SlowQueryTransport(FakeTransport):
        def transact_with_strategy(self, strategy):
            if len(self.calls) == 1:
                motion_clock.sleep(5.0)
            return super().transact_with_strategy(strategy)

    transport = SlowQueryTransport((
        _frame(b">01" + function + b"01"), timeout,
        _frame(b">01d00"), _frame(b">01d01"),
    ))
    pipette = ADP(transport)
    assert getattr(pipette, operation)(*(() if operation == "dispense_all" else (500,)))
    assert [call.payload for call in transport.calls] == [
        pipette._create_command(function.decode("ascii"), 0 if operation == "dispense_all" else 500),
        b">01dB819", b">01dB819", b">01dB819",
    ]
    assert motion_clock.elapsed == pytest.approx(5.3)
    assert "retrying status query only" in caplog.text


def test_continuous_completion_read_timeouts_are_bounded_by_motion_deadline(motion_clock):
    class SilentQueryTransport(FakeTransport):
        def transact_with_strategy(self, strategy):
            if strategy.payload == b">01dB819":
                motion_clock.sleep(5.0)
            return super().transact_with_strategy(strategy)

    timeout = TransportError(
        "serial timeout", category=TransportErrorCategory.TIMEOUT, operation="read_until",
    )
    transport = SilentQueryTransport((_frame(b">01n01"), timeout, timeout))
    with pytest.raises(TransportError, match="motion completion timed out") as raised:
        ADP(transport, motion_timeout_seconds=10.0).absorb(500)
    assert raised.value.category is TransportErrorCategory.TIMEOUT
    assert raised.value.operation == "wait_for_completion"
    assert motion_clock.elapsed == pytest.approx(10.2)
    assert len(transport.calls) == 3


def test_alarm_after_query_timeout_still_fails_motion(motion_clock):
    transport = FakeTransport((
        _frame(b">01n01"),
        TransportError(
            "serial timeout", category=TransportErrorCategory.TIMEOUT, operation="read_until",
        ),
        _frame(b">01d06"),
    ))
    with pytest.raises(ProtocolError, match="aspiration blockage") as raised:
        ADP(transport).absorb(500)
    assert raised.value.raw_error_code == "06"
    assert len(transport.calls) == 3


@pytest.mark.parametrize("operation,function", [
    ("absorb", b"n"), ("dispense", b"p"), ("dispense_all", b"p"),
])
def test_motion_ack_timeout_never_replays_liquid_command(operation, function, caplog):
    caplog.set_level("INFO", logger=driver.__name__)
    transport = FakeTransport((TransportError(
        "serial timeout", category=TransportErrorCategory.TIMEOUT, operation="read_until",
    ),))
    with pytest.raises(TransportError, match="serial timeout"):
        getattr(ADP(transport), operation)(*(() if operation == "dispense_all" else (500,)))
    assert len(transport.calls) == 1
    assert f"command={function.decode('ascii')}" in caplog.text
    assert "operation=read_until" in caplog.text
    assert "ADP ASCII request:" in caplog.text
    assert "retrying status query only" not in caplog.text


@pytest.mark.parametrize("category,operation", [
    (TransportErrorCategory.TIMEOUT, "write"),
    (TransportErrorCategory.TIMEOUT, "transact"),
    (TransportErrorCategory.IO, "read_until"),
    (TransportErrorCategory.CLOSED, "transact"),
])
def test_query_write_timeout_or_connection_error_is_not_retried(category, operation, motion_clock):
    error = TransportError("exchange failed", category=category, operation=operation)
    transport = FakeTransport((_frame(b">01p01"), error))
    with pytest.raises(TransportError, match="exchange failed") as raised:
        ADP(transport).dispense(500)
    assert raised.value is error
    assert len(transport.calls) == 2


def test_first_completion_query_is_delayed_after_motion_ack(motion_clock):
    class TimedTransport(FakeTransport):
        def transact_with_strategy(self, strategy):
            if strategy.payload == b">01dB819":
                assert motion_clock.elapsed == pytest.approx(0.1)
            return super().transact_with_strategy(strategy)

    transport = TimedTransport((_frame(b">01n01"), _frame(b">01d01")))
    assert ADP(transport).absorb(500)


@pytest.mark.parametrize("parameter", ["motion_timeout_seconds", "poll_interval_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_motion_wait_configuration_must_be_finite_and_positive(parameter, value):
    with pytest.raises(ValueError):
        ADP(FakeTransport(), **{parameter: value})

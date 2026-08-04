"""Deterministic transport test doubles for device protocol tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .core.exceptions import TransportError
from .core.strategies import SerialExchangeStrategy
from .core.transport import StrategyTransport


@dataclass(frozen=True, slots=True)
class TransportCall:
    payload: bytes | None
    response_size: int | None
    strategy_name: str


class FakeTransport(StrategyTransport):
    """Return scripted responses while recording protocol interactions."""

    def __init__(
        self,
        responses: tuple[bytes | Exception, ...] = (),
    ) -> None:
        self._responses = deque(responses)
        self.calls: list[TransportCall] = []
        self.closed = False

    def transact(self, payload: bytes, response_size: int) -> bytes:
        self._ensure_open()
        self.calls.append(
            TransportCall(
                payload=bytes(payload),
                response_size=response_size,
                strategy_name="FixedLengthStrategy",
            )
        )
        response = self._next_response()
        if len(response) != response_size:
            raise TransportError(
                f"scripted response size mismatch: "
                f"expected {response_size}, got {len(response)}"
            )
        return response

    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes:
        self._ensure_open()
        payload = getattr(strategy, "payload", None)
        response_size = getattr(strategy, "response_size", None)
        self.calls.append(
            TransportCall(
                payload=bytes(payload) if payload is not None else None,
                response_size=(
                    int(response_size)
                    if response_size is not None
                    else None
                ),
                strategy_name=type(strategy).__name__,
            )
        )
        return self._next_response()

    def queue_response(self, response: bytes | Exception) -> None:
        self._responses.append(response)

    def close(self) -> None:
        self.closed = True

    def _next_response(self) -> bytes:
        if not self._responses:
            raise TransportError("no scripted transport response remains")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def _ensure_open(self) -> None:
        if self.closed:
            raise TransportError("transport is closed")

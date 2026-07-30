"""Provider routing, health tracking, circuit breaking, and fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from typing import Any

from .base import BaseLLMClient
from .errors import (
    LLMConfigError,
    LLMError,
    LLMProviderError,
    LLMTimeoutError,
)
from .fingerprints import fingerprint_messages
from .tasks.profiles import TaskProfile
from .types import (
    LLMCallProvenance,
    LLMCapability,
    LLMChatResult,
    LLMMessage,
    LLMStreamEvent,
)

logger = logging.getLogger(__name__)

ProviderLoader = Callable[[str], BaseLLMClient]


class ProviderHealthStatus(str, Enum):
    """Runtime health state derived from actual provider calls."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OPEN = "open"
    HALF_OPEN = "half_open"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    """Transport-safe provider health details."""

    provider: str
    status: ProviderHealthStatus
    successful_calls: int
    failed_calls: int
    consecutive_failures: int
    circuit_retry_after_s: float
    last_failure_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "consecutive_failures": self.consecutive_failures,
            "circuit_retry_after_s": self.circuit_retry_after_s,
            "last_failure_type": self.last_failure_type,
        }


@dataclass(slots=True)
class _ProviderHealthState:
    successful_calls: int = 0
    failed_calls: int = 0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    probe_in_flight: bool = False
    last_failure_type: str | None = None


class ProviderHealthTracker:
    """Thread-safe call health and circuit-breaker state."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("LLM circuit failure threshold must be at least 1")
        if recovery_seconds <= 0:
            raise ValueError("LLM circuit recovery seconds must be positive")
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._states: dict[str, _ProviderHealthState] = {}
        self._lock = RLock()

    def is_routable(self, provider: str, *, available: bool) -> bool:
        if not available:
            return False
        now = self._clock()
        with self._lock:
            state = self._states.get(provider)
            if state is None:
                return True
            if state.circuit_open_until > now:
                return False
            if state.circuit_open_until and state.probe_in_flight:
                return False
            return True

    def admit(self, provider: str, *, available: bool) -> tuple[bool, str]:
        if not available:
            return False, "unavailable"
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(provider, _ProviderHealthState())
            if state.circuit_open_until > now:
                return False, "circuit_open"
            if state.circuit_open_until:
                if state.probe_in_flight:
                    return False, "half_open_probe_in_flight"
                state.probe_in_flight = True
            return True, ""

    def record_success(self, provider: str) -> None:
        with self._lock:
            state = self._states.setdefault(provider, _ProviderHealthState())
            state.successful_calls += 1
            state.consecutive_failures = 0
            state.circuit_open_until = 0.0
            state.probe_in_flight = False
            state.last_failure_type = None

    def record_failure(self, provider: str, error: BaseException) -> None:
        with self._lock:
            state = self._states.setdefault(provider, _ProviderHealthState())
            state.failed_calls += 1
            state.consecutive_failures += 1
            state.probe_in_flight = False
            state.last_failure_type = type(error).__name__
            if state.consecutive_failures >= self._failure_threshold:
                state.circuit_open_until = (
                    self._clock() + self._recovery_seconds
                )

    def release_cancelled_call(self, provider: str) -> None:
        """Release a half-open probe without treating cancellation as failure."""
        with self._lock:
            state = self._states.get(provider)
            if state is not None:
                state.probe_in_flight = False

    def snapshot(
        self,
        provider: str,
        *,
        available: bool | None,
    ) -> ProviderHealthSnapshot:
        now = self._clock()
        with self._lock:
            state = self._states.get(provider, _ProviderHealthState())
            retry_after = max(0.0, state.circuit_open_until - now)
            if available is False:
                status = ProviderHealthStatus.UNAVAILABLE
            elif retry_after > 0:
                status = ProviderHealthStatus.OPEN
            elif state.circuit_open_until:
                status = ProviderHealthStatus.HALF_OPEN
            elif state.consecutive_failures:
                status = ProviderHealthStatus.DEGRADED
            elif state.successful_calls:
                status = ProviderHealthStatus.HEALTHY
            else:
                status = ProviderHealthStatus.UNKNOWN
            return ProviderHealthSnapshot(
                provider=provider,
                status=status,
                successful_calls=state.successful_calls,
                failed_calls=state.failed_calls,
                consecutive_failures=state.consecutive_failures,
                circuit_retry_after_s=round(retry_after, 3),
                last_failure_type=state.last_failure_type,
            )


class RoutedLLMClient(BaseLLMClient):
    """Profile-bound client that applies provider routing policy."""

    def __init__(
        self,
        *,
        profile: TaskProfile,
        primary_provider: str,
        fallback_providers: Sequence[str],
        explicit_provider: bool,
        provider_loader: ProviderLoader,
        health: ProviderHealthTracker,
    ) -> None:
        self._profile = profile
        self._primary_provider = primary_provider
        self._explicit_provider = explicit_provider
        self._provider_loader = provider_loader
        self._health = health
        self._prompt_template_sha256 = profile.template_sha256
        self._candidates = tuple(dict.fromkeys(
            (
                primary_provider,
                *(() if explicit_provider else fallback_providers),
            )
        ))

    def is_available(self) -> bool:
        for provider_name in self._candidates:
            client = self._provider_loader(provider_name)
            if self._capability_issue(client) is not None:
                continue
            if self._health.is_routable(
                provider_name,
                available=client.is_available(),
            ):
                return True
        return False

    def get_model_name(self) -> str:
        return self._provider_loader(
            self._primary_provider
        ).get_model_name()

    def get_provider_name(self) -> str:
        return self._primary_provider

    def capabilities(self) -> set[LLMCapability]:
        return self._provider_loader(
            self._primary_provider
        ).capabilities()

    async def chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> LLMChatResult:
        attempted: list[str] = []
        skipped: list[str] = []
        last_error: LLMError | None = None
        request_sha256 = fingerprint_messages(messages)

        for provider_name in self._candidates:
            client = self._provider_loader(provider_name)
            capability_issue = self._capability_issue(client)
            if capability_issue is not None:
                skipped.append(f"{provider_name}:{capability_issue}")
                continue

            admitted, reason = self._health.admit(
                provider_name,
                available=client.is_available(),
            )
            if not admitted:
                skipped.append(f"{provider_name}:{reason}")
                continue

            attempted.append(provider_name)
            try:
                result = await client.chat(messages, **options)
            except asyncio.CancelledError:
                self._health.release_cancelled_call(provider_name)
                raise
            except LLMConfigError as exc:
                last_error = exc
                skipped.append(f"{provider_name}:unavailable")
                self._health.release_cancelled_call(provider_name)
                continue
            except TimeoutError as exc:
                normalized = LLMTimeoutError(str(exc))
                last_error = normalized
                self._record_failure(provider_name, normalized)
                continue
            except LLMError as exc:
                last_error = exc
                self._record_failure(provider_name, exc)
                continue
            except Exception as exc:
                normalized = LLMProviderError(str(exc))
                last_error = normalized
                self._record_failure(provider_name, normalized)
                continue

            self._health.record_success(provider_name)
            provenance = self._build_provenance(
                provider=result.provider or provider_name,
                model=result.model or client.get_model_name(),
                attempted=attempted,
                request_sha256=request_sha256,
            )
            return replace(result, provenance=provenance)

        if last_error is not None:
            raise last_error
        details = ", ".join(skipped) or "no candidates"
        raise LLMConfigError(
            f"任务 {self._profile.name} 没有可用的 LLM provider（{details}）"
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        **options: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        attempted: list[str] = []
        skipped: list[str] = []
        last_error: LLMError | None = None
        request_sha256 = fingerprint_messages(messages)

        for provider_name in self._candidates:
            client = self._provider_loader(provider_name)
            capability_issue = self._capability_issue(client)
            if capability_issue is not None:
                skipped.append(f"{provider_name}:{capability_issue}")
                continue

            admitted, reason = self._health.admit(
                provider_name,
                available=client.is_available(),
            )
            if not admitted:
                skipped.append(f"{provider_name}:{reason}")
                continue

            attempted.append(provider_name)
            emitted = False
            failed = False
            stream = client.stream_chat(messages, **options)
            try:
                async for event in stream:
                    provenance = self._build_provenance(
                        provider=provider_name,
                        model=client.get_model_name(),
                        attempted=attempted,
                        request_sha256=request_sha256,
                    )
                    if event.type == "error":
                        error = LLMProviderError(
                            event.error or f"{provider_name} stream failed"
                        )
                        last_error = error
                        self._record_failure(provider_name, error)
                        failed = True
                        if emitted or self._explicit_provider:
                            yield replace(event, provenance=provenance)
                            return
                        break

                    emitted = True
                    if event.type == "done":
                        self._health.record_success(provider_name)
                    yield replace(event, provenance=provenance)
                    if event.type == "done":
                        return
            except asyncio.CancelledError:
                self._health.release_cancelled_call(provider_name)
                raise
            except TimeoutError as exc:
                error = LLMTimeoutError(str(exc))
                last_error = error
                self._record_failure(provider_name, error)
                failed = True
            except LLMError as exc:
                last_error = exc
                self._record_failure(provider_name, exc)
                failed = True
            except Exception as exc:
                error = LLMProviderError(str(exc))
                last_error = error
                self._record_failure(provider_name, error)
                failed = True
            finally:
                await self._close_stream(stream)

            if emitted:
                if not failed:
                    error = LLMProviderError(
                        f"{provider_name} stream ended without done event"
                    )
                    last_error = error
                    self._record_failure(provider_name, error)
                yield LLMStreamEvent(
                    type="error",
                    error=str(last_error),
                    provenance=self._build_provenance(
                        provider=provider_name,
                        model=client.get_model_name(),
                        attempted=attempted,
                        request_sha256=request_sha256,
                    ),
                )
                return

        last_provider = attempted[-1] if attempted else self._primary_provider
        client = self._provider_loader(last_provider)
        details = ", ".join(skipped) or "all calls failed"
        yield LLMStreamEvent(
            type="error",
            error=(
                str(last_error)
                if last_error is not None
                else (
                    f"任务 {self._profile.name} 没有可用的 LLM provider"
                    f"（{details}）"
                )
            ),
            provenance=self._build_provenance(
                provider=last_provider,
                model=client.get_model_name(),
                attempted=attempted,
                request_sha256=request_sha256,
            ),
        )

    async def close(self) -> None:
        """Provider resources are owned and closed by LLMRegistry."""

    def _capability_issue(
        self,
        client: BaseLLMClient,
    ) -> str | None:
        missing = set(self._profile.required_capabilities) - client.capabilities()
        if not missing:
            return None
        return "missing_" + "_".join(sorted(
            capability.value
            for capability in missing
        ))

    def _build_provenance(
        self,
        *,
        provider: str,
        model: str,
        attempted: Sequence[str],
        request_sha256: str,
    ) -> LLMCallProvenance:
        return LLMCallProvenance(
            task_profile=self._profile.name,
            prompt_version=self._profile.version,
            prompt_template_sha256=self._prompt_template_sha256,
            request_sha256=request_sha256,
            provider=provider,
            model=model,
            attempted_providers=tuple(attempted),
            fallback_used=bool(
                attempted
                and attempted[-1] != self._primary_provider
            ),
        )

    def _record_failure(
        self,
        provider_name: str,
        error: LLMError,
    ) -> None:
        self._health.record_failure(provider_name, error)
        logger.warning(
            "LLM provider call failed: task=%s provider=%s error=%s",
            self._profile.name,
            provider_name,
            type(error).__name__,
        )

    @staticmethod
    async def _close_stream(stream: AsyncIterator[LLMStreamEvent]) -> None:
        close = getattr(stream, "aclose", None)
        if close is None:
            return
        try:
            await close()
        except Exception:
            logger.debug("failed to close LLM stream", exc_info=True)

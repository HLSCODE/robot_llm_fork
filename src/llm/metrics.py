"""Thread-safe, payload-free metrics for routed LLM calls."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from types import MappingProxyType
from typing import Any


class LLMCallOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reported_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class LLMMetricsSnapshot:
    calls_total: int
    calls_succeeded_total: int
    calls_failed_total: int
    calls_cancelled_total: int
    fallback_calls_total: int
    call_duration_seconds_total: float
    call_duration_seconds_max: float
    input_tokens_total: int
    output_tokens_total: int
    tokens_total: int
    usage_reported_calls_total: int
    reported_cost_calls_total: int
    reported_cost_usd_total: float
    successful_provider_calls: Mapping[str, int]
    successful_model_calls: Mapping[str, int]
    task_calls: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        completed = self.calls_succeeded_total + self.calls_failed_total
        return {
            "calls_total": self.calls_total,
            "calls_succeeded_total": self.calls_succeeded_total,
            "calls_failed_total": self.calls_failed_total,
            "calls_cancelled_total": self.calls_cancelled_total,
            "fallback_calls_total": self.fallback_calls_total,
            "call_duration_seconds_total": self.call_duration_seconds_total,
            "call_duration_seconds_max": self.call_duration_seconds_max,
            "call_duration_seconds_average": (
                self.call_duration_seconds_total / self.calls_total
                if self.calls_total
                else 0.0
            ),
            "failure_rate": (
                self.calls_failed_total / completed if completed else 0.0
            ),
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
            "tokens_total": self.tokens_total,
            "usage_reported_calls_total": self.usage_reported_calls_total,
            "reported_cost_calls_total": self.reported_cost_calls_total,
            "reported_cost_usd_total": self.reported_cost_usd_total,
            "successful_provider_calls": dict(self.successful_provider_calls),
            "successful_model_calls": dict(self.successful_model_calls),
            "task_calls": dict(self.task_calls),
        }


class LLMMetrics:
    """Own aggregate metrics without retaining prompts, responses, or raw usage."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls_total = 0
        self._calls_succeeded_total = 0
        self._calls_failed_total = 0
        self._calls_cancelled_total = 0
        self._fallback_calls_total = 0
        self._call_duration_seconds_total = 0.0
        self._call_duration_seconds_max = 0.0
        self._input_tokens_total = 0
        self._output_tokens_total = 0
        self._tokens_total = 0
        self._usage_reported_calls_total = 0
        self._reported_cost_calls_total = 0
        self._reported_cost_usd_total = 0.0
        self._successful_provider_calls: dict[str, int] = {}
        self._successful_model_calls: dict[str, int] = {}
        self._task_calls: dict[str, int] = {}

    def record(
        self,
        *,
        outcome: LLMCallOutcome,
        duration_seconds: float,
        task_profile: str,
        provider: str = "",
        model: str = "",
        fallback_used: bool = False,
        usage: LLMUsage | None = None,
    ) -> None:
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("LLM metric duration must be finite and non-negative")
        with self._lock:
            self._calls_total += 1
            self._call_duration_seconds_total += duration_seconds
            self._call_duration_seconds_max = max(
                self._call_duration_seconds_max,
                duration_seconds,
            )
            if outcome is LLMCallOutcome.SUCCEEDED:
                self._calls_succeeded_total += 1
                _increment(self._successful_provider_calls, provider)
                _increment(self._successful_model_calls, model)
            elif outcome is LLMCallOutcome.FAILED:
                self._calls_failed_total += 1
            else:
                self._calls_cancelled_total += 1
            if fallback_used:
                self._fallback_calls_total += 1
            _increment(self._task_calls, task_profile)
            if usage is not None:
                self._usage_reported_calls_total += 1
                self._input_tokens_total += usage.input_tokens
                self._output_tokens_total += usage.output_tokens
                self._tokens_total += usage.total_tokens
                if usage.reported_cost_usd is not None:
                    self._reported_cost_calls_total += 1
                    self._reported_cost_usd_total += usage.reported_cost_usd

    def snapshot(self) -> LLMMetricsSnapshot:
        with self._lock:
            return LLMMetricsSnapshot(
                calls_total=self._calls_total,
                calls_succeeded_total=self._calls_succeeded_total,
                calls_failed_total=self._calls_failed_total,
                calls_cancelled_total=self._calls_cancelled_total,
                fallback_calls_total=self._fallback_calls_total,
                call_duration_seconds_total=self._call_duration_seconds_total,
                call_duration_seconds_max=self._call_duration_seconds_max,
                input_tokens_total=self._input_tokens_total,
                output_tokens_total=self._output_tokens_total,
                tokens_total=self._tokens_total,
                usage_reported_calls_total=self._usage_reported_calls_total,
                reported_cost_calls_total=self._reported_cost_calls_total,
                reported_cost_usd_total=self._reported_cost_usd_total,
                successful_provider_calls=MappingProxyType(
                    dict(self._successful_provider_calls)
                ),
                successful_model_calls=MappingProxyType(
                    dict(self._successful_model_calls)
                ),
                task_calls=MappingProxyType(dict(self._task_calls)),
            )


def parse_llm_usage(payload: Mapping[str, Any] | None) -> LLMUsage | None:
    if not payload:
        return None
    nested = payload.get("usage")
    usage = nested if isinstance(nested, Mapping) else payload
    input_tokens = _non_negative_int(usage, "prompt_tokens", "input_tokens")
    output_tokens = _non_negative_int(usage, "completion_tokens", "output_tokens")
    explicit_total = _non_negative_int(usage, "total_tokens")
    total_tokens = explicit_total or input_tokens + output_tokens
    cost = _non_negative_float(
        usage,
        "cost_usd",
        "total_cost_usd",
        "total_cost",
    )
    if input_tokens == output_tokens == total_tokens == 0 and cost is None:
        return None
    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reported_cost_usd=cost,
    )


def _increment(values: dict[str, int], key: str) -> None:
    normalized = key.strip()
    if normalized:
        values[normalized] = values.get(normalized, 0) + 1


def _non_negative_int(payload: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            return int(value)
    return 0


def _non_negative_float(
    payload: Mapping[str, Any],
    *keys: str,
) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            return float(value)
    return None

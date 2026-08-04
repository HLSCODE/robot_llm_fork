"""Run deterministic, hardware-free performance regression benchmarks."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from typing import Any

from src.application.teleoperation_observability import (
    TeleoperationAuditEvent,
    TeleoperationEventOutcome,
    TeleoperationEventType,
    TeleoperationObservability,
)
from src.core.action_schema import get_action_schema, validate_action_parameters
from src.core.models import ActionType
from src.device_runtime import ArmId
from src.device_runtime.resources import ResourceArbiter
from src.llm.regression import run_regression_suite
from src.llm.metrics import LLMCallOutcome, LLMMetrics, LLMUsage
from src.robot_server.protocol import (
    ACTION_REQUEST_SCHEMAS,
    WEBSOCKET_API_VERSION,
    WebSocketRequest,
)
from src.vision.metrics import VisionMetrics
from src.vision.models import VisionOperation, VisionPipelineResult


PERFORMANCE_SCHEMA_VERSION = 1
MAX_BENCHMARK_ITERATIONS = 1_000_000
MAX_BENCHMARK_SAMPLES = 20
MAX_BUDGET_MILLISECONDS = 60_000.0
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET_PATH = PROJECT_ROOT / "data" / "regression" / "performance_budgets.json"
Clock = Callable[[], float]
BenchmarkRunner = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class BenchmarkBudget:
    iterations: int
    samples: int
    max_median_ms: float


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    name: str
    run_batch: BenchmarkRunner


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    iterations: int
    samples_ms: tuple[float, ...]
    median_ms: float
    max_median_ms: float

    @property
    def succeeded(self) -> bool:
        return self.median_ms <= self.max_median_ms

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "succeeded": self.succeeded,
            "iterations": self.iterations,
            "samples_ms": [round(value, 3) for value in self.samples_ms],
            "median_ms": round(self.median_ms, 3),
            "max_median_ms": self.max_median_ms,
            "budget_used_ratio": round(self.median_ms / self.max_median_ms, 4),
        }


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    results: tuple[BenchmarkResult, ...]

    @property
    def succeeded(self) -> bool:
        return all(result.succeeded for result in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PERFORMANCE_SCHEMA_VERSION,
            "succeeded": self.succeeded,
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "results": [result.to_dict() for result in self.results],
        }


def default_benchmarks() -> dict[str, BenchmarkDefinition]:
    definitions = (
        BenchmarkDefinition("websocket_request_parse", _benchmark_websocket_request_parse),
        BenchmarkDefinition(
            "action_parameter_validation",
            _benchmark_action_parameter_validation,
        ),
        BenchmarkDefinition("resource_lease_cycle", _benchmark_resource_lease_cycle),
        BenchmarkDefinition(
            "teleoperation_observability",
            _benchmark_teleoperation_observability,
        ),
        BenchmarkDefinition(
            "llm_vision_observability",
            _benchmark_llm_vision_observability,
        ),
        BenchmarkDefinition("action_schema_snapshot", _benchmark_action_schema_snapshot),
        BenchmarkDefinition("llm_golden_regression", _benchmark_llm_golden_regression),
    )
    return {definition.name: definition for definition in definitions}


def load_budgets(path: Path) -> dict[str, BenchmarkBudget]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load performance budget document: {path}") from exc
    if not isinstance(document, dict):
        raise ValueError("performance budget document must be an object")
    _reject_unknown_fields(document, {"schema_version", "benchmarks"}, "document")
    if document.get("schema_version") != PERFORMANCE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported performance budget schema_version: "
            f"{document.get('schema_version')!r}"
        )
    raw_benchmarks = document.get("benchmarks")
    if not isinstance(raw_benchmarks, dict) or not raw_benchmarks:
        raise ValueError("performance budget benchmarks must be a non-empty object")
    return {
        str(name): _parse_budget(str(name), raw_budget)
        for name, raw_budget in raw_benchmarks.items()
    }


def run_benchmark_suite(
    budget_path: Path = DEFAULT_BUDGET_PATH,
    *,
    benchmarks: Mapping[str, BenchmarkDefinition] | None = None,
    clock: Clock = perf_counter,
) -> PerformanceReport:
    definitions = dict(default_benchmarks() if benchmarks is None else benchmarks)
    budgets = load_budgets(budget_path)
    if set(definitions) != set(budgets):
        missing = sorted(set(definitions) - set(budgets))
        unknown = sorted(set(budgets) - set(definitions))
        raise ValueError(
            f"performance benchmark registry mismatch: missing={missing}, unknown={unknown}"
        )

    results = tuple(
        _measure(definitions[name], budgets[name], clock)
        for name in sorted(definitions)
    )
    return PerformanceReport(results)


def write_report(path: Path, report: PerformanceReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _measure(
    definition: BenchmarkDefinition,
    budget: BenchmarkBudget,
    clock: Clock,
) -> BenchmarkResult:
    definition.run_batch(budget.iterations)
    samples: list[float] = []
    for _ in range(budget.samples):
        started_at = clock()
        definition.run_batch(budget.iterations)
        samples.append((clock() - started_at) * 1000.0)
    median_ms = median(samples)
    return BenchmarkResult(
        name=definition.name,
        iterations=budget.iterations,
        samples_ms=tuple(samples),
        median_ms=median_ms,
        max_median_ms=budget.max_median_ms,
    )


def _parse_budget(name: str, raw_budget: object) -> BenchmarkBudget:
    if not isinstance(raw_budget, dict):
        raise ValueError(f"benchmark '{name}' budget must be an object")
    _reject_unknown_fields(
        raw_budget,
        {"iterations", "samples", "max_median_ms"},
        f"benchmark '{name}'",
    )
    iterations = raw_budget.get("iterations")
    samples = raw_budget.get("samples")
    max_median_ms = raw_budget.get("max_median_ms")
    if (
        not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or not 1 <= iterations <= MAX_BENCHMARK_ITERATIONS
    ):
        raise ValueError(
            f"benchmark '{name}' iterations must be in 1..{MAX_BENCHMARK_ITERATIONS}"
        )
    if (
        not isinstance(samples, int)
        or isinstance(samples, bool)
        or not 3 <= samples <= MAX_BENCHMARK_SAMPLES
    ):
        raise ValueError(
            f"benchmark '{name}' samples must be in 3..{MAX_BENCHMARK_SAMPLES}"
        )
    if (
        not isinstance(max_median_ms, (int, float))
        or isinstance(max_median_ms, bool)
        or not math.isfinite(max_median_ms)
        or not 0 < max_median_ms <= MAX_BUDGET_MILLISECONDS
    ):
        raise ValueError(
            f"benchmark '{name}' max_median_ms must be in "
            f"(0, {MAX_BUDGET_MILLISECONDS}]"
        )
    return BenchmarkBudget(
        iterations=iterations,
        samples=samples,
        max_median_ms=float(max_median_ms),
    )


def _reject_unknown_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unknown or missing:
        raise ValueError(f"{label} fields mismatch: missing={missing}, unknown={unknown}")


def _benchmark_websocket_request_parse(iterations: int) -> None:
    request = {
        "api_version": WEBSOCKET_API_VERSION,
        "action": "create_action",
        "request_id": "performance-request",
        "name": "benchmark",
        "type": ActionType.WAIT.value,
        "parameters": {"wait_seconds": 0.1},
    }
    known_actions = set(ACTION_REQUEST_SCHEMAS)
    parsed: WebSocketRequest | None = None
    for _ in range(iterations):
        parsed = WebSocketRequest.parse(request, known_actions=known_actions)
    if parsed is None or parsed.action != "create_action":
        raise RuntimeError("WebSocket request benchmark produced an invalid result")


def _benchmark_action_parameter_validation(iterations: int) -> None:
    parameters = {"目标": "身体", "位置": 1000}
    validation = None
    for _ in range(iterations):
        validation = validate_action_parameters(ActionType.MOVE, parameters)
    if validation is None or not validation.is_valid:
        raise RuntimeError("action parameter benchmark produced an invalid result")


def _benchmark_resource_lease_cycle(iterations: int) -> None:
    arbiter = ResourceArbiter()
    for _ in range(iterations):
        lease = arbiter.acquire("performance", ("left-arm", "right-arm"))
        lease.release()
    if arbiter.snapshot():
        raise RuntimeError("resource lease benchmark leaked a resource owner")


def _benchmark_teleoperation_observability(iterations: int) -> None:
    observability = TeleoperationObservability(lambda _event: None)
    interval_seconds = 0.02
    for index in range(iterations):
        observability.record(
            TeleoperationAuditEvent(
                event_type=TeleoperationEventType.FOLLOW_COMMAND,
                outcome=TeleoperationEventOutcome.APPLIED,
                recorded_at_seconds=index * interval_seconds,
                owner_id="performance",
                arms=(ArmId.LEFT,),
                command_count=index + 1,
                duration_seconds=0.001,
            )
        )
    snapshot = observability.snapshot()
    if snapshot.follow_commands_total != iterations:
        raise RuntimeError("teleoperation observability benchmark lost commands")
    if iterations > 1 and not math.isclose(
        snapshot.observed_throughput_hz,
        50.0,
        rel_tol=1e-9,
    ):
        raise RuntimeError("teleoperation observability benchmark measured bad throughput")


def _benchmark_llm_vision_observability(iterations: int) -> None:
    llm_metrics = LLMMetrics()
    vision_metrics = VisionMetrics(
        model_version="performance-model",
        calibration_version="performance-calibration",
    )
    usage = LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    vision_result = VisionPipelineResult(
        successful=True,
        frames_processed=1,
        inference_count=1,
    )
    for _ in range(iterations):
        llm_metrics.record(
            outcome=LLMCallOutcome.SUCCEEDED,
            duration_seconds=0.01,
            task_profile="performance",
            provider="fixture",
            model="fixture-model",
            usage=usage,
        )
        vision_metrics.record_result(
            VisionOperation.CAPTURE,
            vision_result,
            duration_seconds=0.02,
        )
    if llm_metrics.snapshot().calls_total != iterations:
        raise RuntimeError("LLM observability benchmark lost calls")
    if vision_metrics.snapshot().frames_processed_total != iterations:
        raise RuntimeError("vision observability benchmark lost frames")


def _benchmark_action_schema_snapshot(iterations: int) -> None:
    schema: dict[str, object] = {}
    for _ in range(iterations):
        schema = get_action_schema()
    if ActionType.MOVE.value not in schema:
        raise RuntimeError("action schema benchmark produced an incomplete snapshot")


def _benchmark_llm_golden_regression(iterations: int) -> None:
    report = None
    for _ in range(iterations):
        report = run_regression_suite()
    if report is None or not report.succeeded:
        raise RuntimeError("LLM regression benchmark did not complete successfully")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic performance regression benchmarks."
    )
    parser.add_argument(
        "--budget",
        type=Path,
        default=DEFAULT_BUDGET_PATH,
        help="Path to the versioned performance budget document.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the atomic JSON report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        report = run_benchmark_suite(arguments.budget)
        if arguments.output is not None:
            write_report(arguments.output, report)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.succeeded else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": PERFORMANCE_SCHEMA_VERSION,
                    "succeeded": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

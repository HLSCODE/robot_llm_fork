from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.run_performance_benchmarks import (
    BenchmarkDefinition,
    DEFAULT_BUDGET_PATH,
    default_benchmarks,
    load_budgets,
    run_benchmark_suite,
    write_report,
)


def _write_budget(
    path: Path,
    benchmarks: dict[str, dict[str, int | float]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmarks": benchmarks,
            }
        ),
        encoding="utf-8",
    )


class PerformanceBenchmarkTests(unittest.TestCase):
    def test_versioned_default_budget_matches_registry(self) -> None:
        budgets = load_budgets(DEFAULT_BUDGET_PATH)

        self.assertEqual(set(default_benchmarks()), set(budgets))
        self.assertTrue(all(budget.samples >= 3 for budget in budgets.values()))

    def test_suite_uses_median_and_reports_budget_failure(self) -> None:
        calls: list[int] = []
        clock_values = iter((0.0, 0.010, 1.0, 1.020, 2.0, 2.030))
        with TemporaryDirectory() as temporary_directory:
            budget_path = Path(temporary_directory) / "budgets.json"
            _write_budget(
                budget_path,
                {
                    "test_case": {
                        "iterations": 7,
                        "samples": 3,
                        "max_median_ms": 15.0,
                    }
                },
            )
            report = run_benchmark_suite(
                budget_path,
                benchmarks={
                    "test_case": BenchmarkDefinition(
                        "test_case",
                        calls.append,
                    )
                },
                clock=lambda: next(clock_values),
            )

        self.assertFalse(report.succeeded)
        self.assertEqual((7, 7, 7, 7), tuple(calls))
        self.assertAlmostEqual(20.0, report.results[0].median_ms)
        self.assertFalse(report.results[0].succeeded)

    def test_registry_and_budget_names_must_match_exactly(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            budget_path = Path(temporary_directory) / "budgets.json"
            _write_budget(
                budget_path,
                {
                    "unknown": {
                        "iterations": 1,
                        "samples": 3,
                        "max_median_ms": 1.0,
                    }
                },
            )

            with self.assertRaisesRegex(ValueError, "registry mismatch"):
                run_benchmark_suite(
                    budget_path,
                    benchmarks={
                        "expected": BenchmarkDefinition(
                            "expected",
                            lambda _iterations: None,
                        )
                    },
                )

    def test_invalid_budget_fields_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            budget_path = Path(temporary_directory) / "budgets.json"
            budget_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "benchmarks": {
                            "test_case": {
                                "iterations": 1,
                                "samples": 2,
                                "max_median_ms": 1.0,
                                "unexpected": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "fields mismatch"):
                load_budgets(budget_path)

    def test_report_is_written_as_machine_readable_json(self) -> None:
        clock_values = iter((0.0, 0.001, 1.0, 1.001, 2.0, 2.001))
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            budget_path = root / "budgets.json"
            output_path = root / "report.json"
            _write_budget(
                budget_path,
                {
                    "test_case": {
                        "iterations": 1,
                        "samples": 3,
                        "max_median_ms": 5.0,
                    }
                },
            )
            report = run_benchmark_suite(
                budget_path,
                benchmarks={
                    "test_case": BenchmarkDefinition(
                        "test_case",
                        lambda _iterations: None,
                    )
                },
                clock=lambda: next(clock_values),
            )
            write_report(output_path, report)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["succeeded"])
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual("test_case", payload["results"][0]["name"])


if __name__ == "__main__":
    unittest.main()

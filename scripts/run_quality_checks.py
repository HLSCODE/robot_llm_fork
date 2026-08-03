"""Run the repository's local and CI quality gates through one entry point."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    arguments: tuple[str, ...]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUALITY_CHECKS = (
    QualityCheck(
        name="Compile",
        arguments=("-m", "compileall", "-q", "src", "tests", "scripts"),
    ),
    QualityCheck(
        name="Ruff",
        arguments=("-m", "ruff", "check", "."),
    ),
    QualityCheck(
        name="Mypy",
        arguments=("-m", "mypy"),
    ),
    QualityCheck(
        name="Pytest with coverage",
        arguments=(
            "-m",
            "pytest",
            "-q",
            "--cov",
            "--cov-report=term",
            "--cov-report=xml:coverage.xml",
        ),
    ),
    QualityCheck(
        name="LLM golden regression",
        arguments=("-m", "src.llm.regression"),
    ),
    QualityCheck(
        name="Performance regression",
        arguments=(
            "scripts/run_performance_benchmarks.py",
            "--output",
            "performance-report.json",
        ),
    ),
    QualityCheck(
        name="Wheel package smoke",
        arguments=("scripts/validate_package.py",),
    ),
)


def main() -> int:
    for check in QUALITY_CHECKS:
        print(f"\n==> {check.name}", flush=True)
        result = subprocess.run(
            (sys.executable, *check.arguments),
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"\nQuality gate failed: {check.name} (exit code {result.returncode})",
                file=sys.stderr,
            )
            return result.returncode

    print("\nAll quality gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

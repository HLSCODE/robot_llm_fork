"""Offline golden regression suite for classifier, planner, and skills."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..skill_system.builtin_catalog import get_builtin_skills
from ..skill_system.models import SkillMatchResult
from ..skill_system.skill_engine import SkillEngine
from .fingerprints import fingerprint_json
from .tasks import (
    INSTRUCTION_CLASSIFIER_PROFILE,
    ROBOT_PLANNER_PROFILE,
)
from .tasks.classifier import parse_instruction_classification
from .tasks.planner import parse_skill_plan_response

REGRESSION_SCHEMA_VERSION = 1
DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "regression"
    / "llm_planning_cases.json"
)


@dataclass(frozen=True, slots=True)
class RegressionFailure:
    category: str
    case_id: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "case_id": self.case_id,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RegressionReport:
    total: int
    passed: int
    failures: tuple[RegressionFailure, ...]

    @property
    def succeeded(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "total": self.total,
            "passed": self.passed,
            "failed": len(self.failures),
            "failures": [
                failure.to_dict()
                for failure in self.failures
            ],
        }


class _SkillCatalog:
    """Isolated catalog used by the offline runner."""

    def __init__(self) -> None:
        self._skills = {
            skill.id: skill
            for skill in get_builtin_skills()
        }

    def get_skill(self, skill_id: str):
        return self._skills.get(skill_id)

    def list_skills(self):
        return sorted(self._skills.values(), key=lambda skill: skill.name)


def run_regression_suite(
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
) -> RegressionReport:
    """Run deterministic regressions without making any network request."""
    dataset = _load_dataset(Path(dataset_path))
    failures: list[RegressionFailure] = []
    total = 0

    profiles = {
        profile.name: profile
        for profile in (
            INSTRUCTION_CLASSIFIER_PROFILE,
            ROBOT_PLANNER_PROFILE,
        )
    }
    for expected in dataset["profiles"]:
        total += 1
        case_id = expected["name"]
        profile = profiles.get(case_id)
        if profile is None:
            failures.append(RegressionFailure(
                "profile",
                case_id,
                "profile is not registered in the regression runner",
            ))
            continue
        actual = {
            "name": profile.name,
            "version": profile.version,
            "template_sha256": profile.template_sha256,
        }
        _record_mismatch(
            failures,
            category="profile",
            case_id=case_id,
            expected=expected,
            actual=actual,
        )

    for case in dataset["classification_cases"]:
        total += 1
        try:
            actual = parse_instruction_classification(
                json.dumps(case["model_response"], ensure_ascii=False)
            )
        except Exception as exc:
            failures.append(_exception_failure(
                "classification",
                case["id"],
                exc,
            ))
            continue
        _record_mismatch(
            failures,
            category="classification",
            case_id=case["id"],
            expected=case["expected"],
            actual=actual,
        )

    for case in dataset["planning_cases"]:
        total += 1
        try:
            plan = parse_skill_plan_response(
                json.dumps(case["model_response"], ensure_ascii=False)
            )
        except Exception as exc:
            failures.append(_exception_failure(
                "planning",
                case["id"],
                exc,
            ))
            continue
        actual = {
            "skill_id": plan.skill_id,
            "skill_name": plan.skill_name,
            "parameters": plan.parameters,
            "reasoning": plan.reasoning,
            "confidence": plan.confidence,
            "error": plan.error,
            "fallback_suggestion": plan.fallback_suggestion,
        }
        _record_mismatch(
            failures,
            category="planning",
            case_id=case["id"],
            expected=case["expected"],
            actual=actual,
        )

    catalog = _SkillCatalog()
    engine = SkillEngine(catalog)
    total += 1
    skill_summaries = [
        {
            **skill.get_summary(),
            "icon": skill.icon,
            "estimated_time": skill.estimate_total_time(),
        }
        for skill in catalog.list_skills()
    ]
    _record_mismatch(
        failures,
        category="skill_catalog",
        case_id="default_skill_catalog",
        expected=dataset["skill_catalog"],
        actual={
            "version": "1",
            "sha256": fingerprint_json(skill_summaries),
        },
    )

    for case in dataset["skill_cases"]:
        total += 1
        try:
            items, validation = engine.parse_and_expand(SkillMatchResult(
                skill_id=case["skill_id"],
                skill_name=case["skill_id"],
                confidence=1.0,
                extracted_params=case["parameters"],
                reasoning="offline regression",
            ))
        except Exception as exc:
            failures.append(_exception_failure(
                "skill",
                case["id"],
                exc,
            ))
            continue
        actual = {
            "validation_code": validation.code.value,
            "actions": [
                {
                    "name": item.definition.name,
                    "type": item.definition.type.name,
                    "parameters": item.definition.parameters,
                }
                for item in items
            ],
        }
        _record_mismatch(
            failures,
            category="skill",
            case_id=case["id"],
            expected=case["expected"],
            actual=actual,
        )

    return RegressionReport(
        total=total,
        passed=total - len(failures),
        failures=tuple(failures),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run offline LLM planning golden regressions.",
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        default=str(DEFAULT_DATASET_PATH),
        help="Path to the strict schema-v1 regression dataset.",
    )
    args = parser.parse_args()
    try:
        report = run_regression_suite(args.dataset)
    except Exception as exc:
        output = {
            "succeeded": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.succeeded else 1


def _load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        dataset = json.load(file)
    if not isinstance(dataset, dict):
        raise ValueError("regression dataset root must be an object")
    if dataset.get("schema_version") != REGRESSION_SCHEMA_VERSION:
        raise ValueError(
            "unsupported regression dataset schema_version: "
            f"{dataset.get('schema_version')!r}"
        )
    required = {
        "profiles",
        "classification_cases",
        "planning_cases",
        "skill_catalog",
        "skill_cases",
    }
    missing = required - dataset.keys()
    if missing:
        raise ValueError(
            "regression dataset missing fields: "
            + ", ".join(sorted(missing))
        )
    unexpected = dataset.keys() - required - {"schema_version"}
    if unexpected:
        raise ValueError(
            "regression dataset has unknown fields: "
            + ", ".join(sorted(unexpected))
        )
    _validate_cases(
        dataset["profiles"],
        section="profiles",
        required={"name", "version", "template_sha256"},
    )
    _validate_cases(
        dataset["classification_cases"],
        section="classification_cases",
        required={"id", "input", "model_response", "expected"},
    )
    _validate_cases(
        dataset["planning_cases"],
        section="planning_cases",
        required={"id", "input", "model_response", "expected"},
    )
    _validate_cases(
        dataset["skill_cases"],
        section="skill_cases",
        required={"id", "skill_id", "parameters", "expected"},
    )
    _require_exact_keys(
        dataset["skill_catalog"],
        context="skill_catalog",
        required={"version", "sha256"},
    )
    return dataset


def _record_mismatch(
    failures: list[RegressionFailure],
    *,
    category: str,
    case_id: str,
    expected: Any,
    actual: Any,
) -> None:
    if actual == expected:
        return
    failures.append(RegressionFailure(
        category=category,
        case_id=case_id,
        message=(
            "expected "
            f"{json.dumps(expected, ensure_ascii=False, sort_keys=True)}"
            ", got "
            f"{json.dumps(actual, ensure_ascii=False, sort_keys=True)}"
        ),
    ))


def _validate_cases(
    cases: Any,
    *,
    section: str,
    required: set[str],
) -> None:
    if not isinstance(cases, list):
        raise ValueError(f"{section} must be an array")
    ids: set[str] = set()
    for index, case in enumerate(cases):
        context = f"{section}[{index}]"
        _require_exact_keys(
            case,
            context=context,
            required=required,
        )
        case_id = case.get("id") or case.get("name")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{context} must have a non-empty id or name")
        if case_id in ids:
            raise ValueError(f"{section} contains duplicate id: {case_id}")
        ids.add(case_id)


def _require_exact_keys(
    value: Any,
    *,
    context: str,
    required: set[str],
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    missing = required - value.keys()
    unexpected = value.keys() - required
    if missing:
        raise ValueError(
            f"{context} missing fields: " + ", ".join(sorted(missing))
        )
    if unexpected:
        raise ValueError(
            f"{context} has unknown fields: "
            + ", ".join(sorted(unexpected))
        )


def _exception_failure(
    category: str,
    case_id: str,
    error: BaseException,
) -> RegressionFailure:
    return RegressionFailure(
        category=category,
        case_id=case_id,
        message=f"raised {type(error).__name__}: {error}",
    )


if __name__ == "__main__":
    raise SystemExit(main())

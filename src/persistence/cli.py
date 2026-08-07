"""Validate or explicitly migrate persisted application catalogs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Sequence

from .storage import JsonCompositionRepository
from ..skill_system.skill_registry import SkillRegistry


@dataclass(frozen=True, slots=True)
class DataOperationReport:
    action_count: int
    task_count: int
    skill_count: int
    migrated_actions: bool = False
    migrated_tasks: tuple[str, ...] = ()
    migrated_skills: bool = False


def run_data_operation(
    *,
    actions_file: Path,
    tasks_directory: Path,
    skills_file: Path,
    migrate: bool,
) -> DataOperationReport:
    """Validate all inputs first, then optionally perform explicit migration."""
    repository = JsonCompositionRepository(
        actions_file=actions_file,
        tasks_directory=tasks_directory,
    )
    registry = SkillRegistry()
    actions = repository.load_actions()
    task_names = repository.list_task_names()
    for task_name in task_names:
        repository.load_task(task_name)
    skill_count = registry.load_from_json(skills_file)

    if not migrate:
        return DataOperationReport(
            action_count=len(actions),
            task_count=len(task_names),
            skill_count=skill_count,
        )

    return DataOperationReport(
        action_count=len(actions),
        task_count=len(task_names),
        skill_count=skill_count,
        migrated_actions=repository.migrate_legacy_actions(),
        migrated_tasks=repository.migrate_legacy_tasks(),
        migrated_skills=registry.migrate_json(skills_file),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or explicitly migrate robot-llm application data.",
    )
    parser.add_argument("operation", choices=("validate", "migrate"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--actions-file", type=Path)
    parser.add_argument("--tasks-directory", type=Path)
    parser.add_argument("--skills-file", type=Path)
    arguments = parser.parse_args(argv)

    root = arguments.data_root.resolve()
    report = run_data_operation(
        actions_file=(arguments.actions_file or root / "actions_library.json").resolve(),
        tasks_directory=(arguments.tasks_directory or root / "tasks").resolve(),
        skills_file=(
            arguments.skills_file or root / "skills" / "skill_library.json"
        ).resolve(),
        migrate=arguments.operation == "migrate",
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

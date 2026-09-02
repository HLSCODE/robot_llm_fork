"""Interactive and automation-friendly application initialization commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from .initialization import (
    EventKind,
    InitializationEvent,
    InitializationPlan,
    InitializationRunner,
    InitializationStep,
    KWS_MODELS,
    StepStatus,
    SUPPORTED_EXTRAS,
)


DEFAULT_STEPS = (
    InitializationStep.CONFIGURATION,
    InitializationStep.DATA_MIGRATION,
    InitializationStep.DEPENDENCIES,
    InitializationStep.VALIDATION,
)
DEFAULT_EXTRAS = ("gui", "server", "ai")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the complete initializer, using Textual when attached to a terminal."""
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    if raw_arguments and raw_arguments[0] == "migrate-data":
        return _run_data_migration_command(raw_arguments[1:])
    arguments = _build_parser().parse_args(raw_arguments)
    try:
        plan = _plan_from_arguments(arguments)
    except ValueError as exc:
        print(f"初始化参数错误: {exc}", file=sys.stderr)
        return 2

    if arguments.non_interactive or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _run_plain(plan)
    return _run_textual(plan)


def models_main(argv: Sequence[str] | None = None) -> int:
    """Initialize ASR and KWS models without running other setup stages."""
    parser = argparse.ArgumentParser(description="准备本项目使用的语音模型。")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--asr", action="store_true", help="准备 ASR、VAD 和标点模型")
    parser.add_argument("--kws", action="store_true", help="下载 KWS 模型")
    parser.add_argument("--check", action="store_true", help="模型完成后校验配置")
    parser.add_argument("--kws-model", choices=tuple(KWS_MODELS), default="zh-en")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    steps: list[InitializationStep] = []
    if arguments.asr:
        steps.append(InitializationStep.ASR_MODELS)
    if arguments.kws:
        steps.append(InitializationStep.KWS_MODEL)
    if arguments.check:
        steps.append(InitializationStep.VALIDATION)
    if not steps:
        parser.error("至少选择 --asr 或 --kws；可额外指定 --check")
    plan = InitializationPlan(
        project_root=arguments.project_root.resolve(),
        steps=tuple(steps),
        kws_model=arguments.kws_model,
        dry_run=arguments.dry_run,
    )
    return _run_plain(plan)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="交互式初始化依赖、本机配置、语音模型并执行配置校验。"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="关闭 Textual 界面，按命令行参数直接执行。",
    )
    parser.add_argument(
        "--steps",
        default=",".join(step.value for step in DEFAULT_STEPS),
        help=(
            "逗号分隔的步骤：configuration,data_migration,dependencies,"
            "asr_models,kws_model,validation"
        ),
    )
    parser.add_argument(
        "--extras",
        default=",".join(DEFAULT_EXTRAS),
        help="uv 可选依赖组，使用逗号分隔。",
    )
    parser.add_argument("--kws-model", choices=tuple(KWS_MODELS), default="zh-en")
    parser.add_argument(
        "--no-frozen", action="store_true", help="依赖同步时允许更新 uv.lock。"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅展示计划，不执行副作用。")
    return parser


def _plan_from_arguments(arguments: argparse.Namespace) -> InitializationPlan:
    steps = _parse_steps(arguments.steps)
    extras = _parse_extras(arguments.extras)
    return InitializationPlan(
        project_root=arguments.project_root.resolve(),
        steps=steps,
        extras=extras,
        kws_model=arguments.kws_model,
        frozen=not arguments.no_frozen,
        dry_run=arguments.dry_run,
    )


def _parse_steps(raw_steps: str) -> tuple[InitializationStep, ...]:
    values = tuple(value.strip() for value in raw_steps.split(",") if value.strip())
    if not values:
        raise ValueError("步骤不能为空")
    try:
        return tuple(dict.fromkeys(InitializationStep(value) for value in values))
    except ValueError as exc:
        raise ValueError(f"未知初始化步骤: {exc}") from exc


def _parse_extras(raw_extras: str) -> tuple[str, ...]:
    extras = tuple(dict.fromkeys(value.strip() for value in raw_extras.split(",") if value.strip()))
    unknown = set(extras) - SUPPORTED_EXTRAS
    if unknown:
        raise ValueError(f"未知依赖组: {', '.join(sorted(unknown))}")
    return extras


def _run_plain(plan: InitializationPlan) -> int:
    def report(event: InitializationEvent) -> None:
        if event.kind is EventKind.LOG:
            print(f"  {event.message}")
            return
        assert event.status is not None
        print(f"[{event.status.value}] {event.step.label}: {event.message}")

    results = InitializationRunner(report).run(plan)
    return 1 if any(result.status is StepStatus.FAILED for result in results) else 0


def _run_data_migration_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="robot-init migrate-data",
        description="显式初始化数据目录并迁移受支持的旧版数据。",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true", help="仅展示步骤，不写入数据。")
    arguments = parser.parse_args(argv)
    return _run_plain(
        InitializationPlan(
            project_root=arguments.project_root.resolve(),
            steps=(InitializationStep.DATA_MIGRATION,),
            dry_run=arguments.dry_run,
        )
    )


def _run_textual(initial_plan: InitializationPlan) -> int:
    try:
        from .initialization_tui import InitializationApp
    except ImportError as exc:
        print("Textual 未安装，请先执行 uv sync。", file=sys.stderr)
        print(f"详细信息: {exc}", file=sys.stderr)
        return 2
    result = InitializationApp(initial_plan).run()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())

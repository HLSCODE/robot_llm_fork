"""Incrementally initialize local configuration from versioned examples."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
from typing import Sequence


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """Files created or preserved during one initialization run."""

    created: tuple[Path, ...]
    skipped: tuple[Path, ...]


def initialize_configuration(project_root: Path) -> InitializationResult:
    """Create missing local config files without changing existing files."""
    root = project_root.resolve()
    mappings = _template_mappings(root)
    created: list[Path] = []
    skipped: list[Path] = []

    for source, destination in mappings:
        if _copy_if_missing(source, destination):
            created.append(destination)
        else:
            skipped.append(destination)

    return InitializationResult(created=tuple(created), skipped=tuple(skipped))


def _template_mappings(project_root: Path) -> tuple[tuple[Path, Path], ...]:
    env_template = project_root / ".env.example"
    config_template = project_root / "config" / "config.example.toml"
    fragment_directory = project_root / "config" / "fragments"
    required_templates = (env_template, config_template)
    missing = tuple(path for path in required_templates if not path.is_file())
    if missing:
        rendered = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"缺少配置模板: {rendered}")

    fragment_templates = tuple(sorted(fragment_directory.glob("*.example.toml")))
    if not fragment_templates:
        raise FileNotFoundError(f"未找到子配置模板: {fragment_directory}")

    mappings = [
        (env_template, project_root / ".env"),
        (config_template, project_root / "config" / "config.toml"),
    ]
    mappings.extend(
        (
            template,
            template.with_name(template.name.removesuffix(".example.toml") + ".toml"),
        )
        for template in fragment_templates
    )
    return tuple(mappings)


def _copy_if_missing(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_stream:
        try:
            destination_stream = destination.open("xb")
        except FileExistsError:
            return False
        try:
            with destination_stream:
                shutil.copyfileobj(source_stream, destination_stream)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for incremental local configuration initialization."""
    parser = argparse.ArgumentParser(
        description="从 example 模板增量创建本机 config 与 .env，已存在文件不会覆盖。"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="项目根目录，默认使用当前工作目录。",
    )
    arguments = parser.parse_args(argv)

    try:
        result = initialize_configuration(arguments.project_root)
    except (FileNotFoundError, OSError) as exc:
        print(f"配置初始化失败: {exc}", file=sys.stderr)
        return 1

    root = arguments.project_root.resolve()
    for path in result.created:
        print(f"已创建: {path.relative_to(root)}")
    for path in result.skipped:
        print(f"已跳过: {path.relative_to(root)}（文件已存在）")
    print(f"配置初始化完成：创建 {len(result.created)}，跳过 {len(result.skipped)}")
    return 0

"""Assemble immutable application settings from TOML and environment overrides."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from collections.abc import Callable
from typing import cast, get_type_hints

from dotenv import load_dotenv

from .data_paths import PROJECT_ROOT
from .environment import environment_overrides
from .errors import ConfigLoadError
from .settings import ApplicationSettings
from .toml_source import configuration_document_paths, load_toml_sections


def default_config_path(
    *,
    working_directory: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Resolve local config without depending solely on process working directory."""
    working_root = (working_directory or Path.cwd()).resolve()
    working_candidate = working_root / "config" / "config.toml"
    if working_candidate.is_file():
        return working_candidate

    project_candidate = project_root.resolve() / "config" / "config.toml"
    if project_candidate.is_file():
        return project_candidate
    return working_candidate


def configuration_source_paths(config_path: str | Path | None = None) -> tuple[Path, ...]:
    """Return the entry and fragments that the application would load."""
    resolved_config = _resolve_config_path(config_path)
    if not resolved_config.is_file():
        return ()
    return configuration_document_paths(resolved_config)


def load_application_settings(
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | None = None,
) -> ApplicationSettings:
    """Load defaults < TOML < environment into one immutable snapshot.

    A missing conventional config file is allowed so installed commands can use
    typed defaults. An explicitly supplied path must exist.
    """
    resolved_config = _resolve_config_path(config_path)
    if config_path is not None and not resolved_config.is_file():
        raise ConfigLoadError(f"配置文件不存在: {resolved_config}")

    resolved_env = (
        Path(env_file)
        if env_file is not None
        else _default_env_path(resolved_config, has_explicit_config=config_path is not None)
    )
    if resolved_env.is_file():
        load_dotenv(resolved_env, override=False)

    try:
        sections = load_toml_sections(resolved_config) if resolved_config.is_file() else {}
        overrides = environment_overrides()
        return _build_settings(sections, overrides)
    except ConfigLoadError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ConfigLoadError("配置无法解析；请检查 config/config.toml 和 .env") from exc


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return default_config_path()
    return Path(config_path).expanduser().resolve()


def _default_env_path(config_path: Path, *, has_explicit_config: bool) -> Path:
    if not has_explicit_config and config_path.parent.name == "config":
        return config_path.parent.parent / ".env"
    return Path.cwd() / ".env"


def _build_settings(
    toml_sections: dict[str, dict[str, object]],
    environment_sections: dict[str, dict[str, object]],
) -> ApplicationSettings:
    defaults = ApplicationSettings.defaults()
    group_types = get_type_hints(ApplicationSettings)
    groups: dict[str, object] = {}

    for group_definition in fields(ApplicationSettings):
        group_name = group_definition.name
        settings_type = group_types[group_name]
        default_group = getattr(defaults, group_name)
        values = {
            definition.name: getattr(default_group, definition.name)
            for definition in fields(settings_type)
        }
        values.update(toml_sections.get(group_name, {}))
        values.update(environment_sections.get(group_name, {}))
        groups[group_name] = settings_type(**values)

    settings_factory = cast(Callable[..., ApplicationSettings], ApplicationSettings)
    return settings_factory(**groups)

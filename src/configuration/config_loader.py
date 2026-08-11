"""Assemble immutable application settings from TOML and environment overrides."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from collections.abc import Callable
from typing import cast, get_type_hints

from dotenv import load_dotenv

from .environment import environment_overrides
from .errors import ConfigLoadError
from .settings import ApplicationSettings
from .toml_source import load_toml_sections


def default_config_path() -> Path:
    """Return the conventional project-local TOML configuration path."""
    return Path.cwd() / "config" / "config.toml"


def load_application_settings(
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | None = None,
) -> ApplicationSettings:
    """Load defaults < TOML < environment into one immutable snapshot.

    A missing conventional config file is allowed so installed commands can use
    typed defaults. An explicitly supplied path must exist.
    """
    resolved_config = Path(config_path) if config_path is not None else default_config_path()
    if config_path is not None and not resolved_config.is_file():
        raise ConfigLoadError(f"配置文件不存在: {resolved_config}")

    resolved_env = Path(env_file) if env_file is not None else Path.cwd() / ".env"
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

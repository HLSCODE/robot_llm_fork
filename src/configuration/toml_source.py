"""Strict TOML configuration source."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import get_type_hints

from .errors import ConfigLoadError
from .settings import ApplicationSettings
from .value_parsing import coerce_value


SCHEMA_VERSION = 1
_RESERVED_ROOT_KEYS = frozenset({"schema_version"})
_FORBIDDEN_TOML_GROUPS = frozenset({"secrets"})


def load_toml_sections(path: Path) -> dict[str, dict[str, object]]:
    """Load and validate a TOML document without accepting unknown fields."""
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigLoadError(f"TOML 语法错误: {path}") from exc

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ConfigLoadError(f"配置 schema_version 无效，当前要求 {SCHEMA_VERSION}")

    group_types = get_type_hints(ApplicationSettings)
    unknown_root = set(document) - set(group_types) - _RESERVED_ROOT_KEYS
    if unknown_root:
        _raise_unknown("配置表", unknown_root)

    forbidden = set(document) & _FORBIDDEN_TOML_GROUPS
    if forbidden:
        raise ConfigLoadError("敏感字段不得写入 TOML，请使用 .env 或系统环境变量")

    sections: dict[str, dict[str, object]] = {}
    for group_name, settings_type in group_types.items():
        if group_name in _FORBIDDEN_TOML_GROUPS or group_name not in document:
            continue
        raw_section = document[group_name]
        if not isinstance(raw_section, dict):
            raise ConfigLoadError(f"配置项 {group_name} 必须是 TOML 表")
        field_types = get_type_hints(settings_type)
        unknown_fields = set(raw_section) - set(field_types)
        if unknown_fields:
            _raise_unknown(f"[{group_name}] 字段", unknown_fields)
        sections[group_name] = {
            name: coerce_value(value, field_types[name], f"{group_name}.{name}")
            for name, value in raw_section.items()
        }
    return sections


def _raise_unknown(kind: str, names: set[str]) -> None:
    rendered = ", ".join(sorted(names))
    raise ConfigLoadError(f"未知{kind}: {rendered}")

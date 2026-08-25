"""Strict TOML configuration source with deterministic fragment includes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import get_type_hints

from .errors import ConfigLoadError
from .settings import ApplicationSettings
from .value_parsing import coerce_value


SCHEMA_VERSION = 5
_INCLUDE_KEY = "include"
_RESERVED_ROOT_KEYS = frozenset({"schema_version", _INCLUDE_KEY})
_FORBIDDEN_TOML_GROUPS = frozenset({"secrets"})


@dataclass(frozen=True, slots=True)
class _ConfigDocument:
    path: Path
    content: dict[str, object]


def load_toml_sections(path: Path) -> dict[str, dict[str, object]]:
    """Load one entry document plus optional fragments.

    Fragments are loaded in declaration order. Later fragments override earlier
    fragments field by field, and fields declared in the entry document override
    every fragment. Sequence-valued fields are replaced as one value rather than
    implicitly appended.
    """
    entry = _read_document(path)
    _validate_entry_version(entry)
    fragments = _load_fragments(entry)

    merged: dict[str, dict[str, object]] = {}
    for fragment in fragments:
        _merge_sections(merged, _parse_sections(fragment, is_entry=False))
    _merge_sections(merged, _parse_sections(entry, is_entry=True))
    return merged


def configuration_document_paths(path: Path) -> tuple[Path, ...]:
    """Return the validated entry and included fragment paths in load order."""
    entry = _read_document(path)
    _validate_entry_version(entry)
    fragments = _load_fragments(entry)
    return (entry.path.resolve(), *(fragment.path.resolve() for fragment in fragments))


def _read_document(path: Path) -> _ConfigDocument:
    try:
        with path.open("rb") as stream:
            content = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"配置文件不存在: {path}") from exc
    except OSError as exc:
        raise ConfigLoadError(f"配置文件无法读取: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigLoadError(f"TOML 语法错误: {path}") from exc
    return _ConfigDocument(path=path, content=content)


def _validate_entry_version(document: _ConfigDocument) -> None:
    version = document.content.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ConfigLoadError(
            f"配置 schema_version 无效，当前要求 {SCHEMA_VERSION}: {document.path}"
        )


def _load_fragments(entry: _ConfigDocument) -> tuple[_ConfigDocument, ...]:
    raw_includes = entry.content.get(_INCLUDE_KEY, [])
    if not isinstance(raw_includes, list):
        raise ConfigLoadError(f"配置项 include 必须是字符串数组: {entry.path}")

    base_directory = entry.path.parent.resolve()
    seen_paths: set[Path] = set()
    fragments: list[_ConfigDocument] = []
    for index, raw_path in enumerate(raw_includes):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ConfigLoadError(
                f"配置项 include[{index}] 必须是非空相对路径: {entry.path}"
            )
        include_path = Path(raw_path)
        if include_path.is_absolute():
            raise ConfigLoadError(
                f"配置项 include[{index}] 不允许使用绝对路径: {entry.path}"
            )
        resolved_path = (base_directory / include_path).resolve()
        if not resolved_path.is_relative_to(base_directory):
            raise ConfigLoadError(
                f"配置项 include[{index}] 不允许超出入口配置目录: {entry.path}"
            )
        if resolved_path in seen_paths:
            raise ConfigLoadError(f"配置 include 重复: {resolved_path}")
        seen_paths.add(resolved_path)
        fragment = _read_document(resolved_path)
        if "schema_version" in fragment.content or _INCLUDE_KEY in fragment.content:
            raise ConfigLoadError(
                f"子配置不得声明 schema_version 或 include: {fragment.path}"
            )
        fragments.append(fragment)
    return tuple(fragments)


def _parse_sections(
    document: _ConfigDocument,
    *,
    is_entry: bool,
) -> dict[str, dict[str, object]]:
    group_types = get_type_hints(ApplicationSettings)
    allowed_metadata = _RESERVED_ROOT_KEYS if is_entry else frozenset()
    unknown_root = set(document.content) - set(group_types) - allowed_metadata
    if unknown_root:
        _raise_unknown("配置表", unknown_root, document.path)

    forbidden = set(document.content) & _FORBIDDEN_TOML_GROUPS
    if forbidden:
        raise ConfigLoadError(
            f"敏感字段不得写入 TOML，请使用 .env 或系统环境变量: {document.path}"
        )

    sections: dict[str, dict[str, object]] = {}
    for group_name, settings_type in group_types.items():
        if group_name in _FORBIDDEN_TOML_GROUPS or group_name not in document.content:
            continue
        raw_section = document.content[group_name]
        if not isinstance(raw_section, dict):
            raise ConfigLoadError(
                f"配置项 {group_name} 必须是 TOML 表: {document.path}"
            )
        if group_name == "llm_providers":
            raw_section = _normalize_llm_provider_catalog(raw_section, document.path)
        field_types = get_type_hints(settings_type)
        unknown_fields = set(raw_section) - set(field_types)
        if unknown_fields:
            _raise_unknown(f"[{group_name}] 字段", unknown_fields, document.path)
        sections[group_name] = {
            name: _coerce_document_value(
                value,
                field_types[name],
                f"{group_name}.{name}",
                document.path,
            )
            for name, value in raw_section.items()
        }
    return sections


def _normalize_llm_provider_catalog(
    raw_section: dict[str, object],
    source_path: Path,
) -> dict[str, object]:
    """Convert ergonomic ``[llm_providers.<id>]`` tables to typed entries."""
    providers: list[dict[str, object]] = []
    for provider_id, raw_provider in raw_section.items():
        if not provider_id.strip():
            raise ConfigLoadError(f"LLM provider ID 不能为空: {source_path}")
        if not isinstance(raw_provider, dict):
            raise ConfigLoadError(
                f"配置项 llm_providers.{provider_id} 必须是 TOML 表: {source_path}"
            )
        providers.append({"id": provider_id, **raw_provider})
    return {"providers": providers}


def _coerce_document_value(
    value: object,
    expected_type: object,
    field_name: str,
    source_path: Path,
) -> object:
    try:
        return coerce_value(value, expected_type, field_name)
    except ConfigLoadError as exc:
        raise ConfigLoadError(f"{exc}: {source_path}") from exc


def _merge_sections(
    target: dict[str, dict[str, object]],
    source: dict[str, dict[str, object]],
) -> None:
    for group_name, values in source.items():
        target.setdefault(group_name, {}).update(values)


def _raise_unknown(kind: str, names: set[str], source_path: Path) -> None:
    rendered = ", ".join(sorted(names))
    raise ConfigLoadError(f"未知{kind}: {rendered}: {source_path}")

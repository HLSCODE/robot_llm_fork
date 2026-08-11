"""Typed conversion shared by TOML and environment configuration sources."""

from __future__ import annotations

import json
from types import UnionType
from typing import Union, get_args, get_origin

from .errors import ConfigLoadError


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def coerce_value(value: object, expected_type: object, field_name: str) -> object:
    """Validate and freeze one already structured configuration value."""
    origin = get_origin(expected_type)
    arguments = get_args(expected_type)

    if origin in (Union, UnionType):
        if value is None and type(None) in arguments:
            return None
        candidates = tuple(candidate for candidate in arguments if candidate is not type(None))
        for candidate in candidates:
            try:
                return coerce_value(value, candidate, field_name)
            except ConfigLoadError:
                continue
        raise _type_error(field_name, expected_type)

    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise _type_error(field_name, expected_type)
        item_type = arguments[0] if arguments else object
        return tuple(coerce_value(item, item_type, field_name) for item in value)

    if expected_type is bool:
        if type(value) is not bool:
            raise _type_error(field_name, expected_type)
        return value
    if expected_type is int:
        if type(value) is not int:
            raise _type_error(field_name, expected_type)
        return value
    if expected_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _type_error(field_name, expected_type)
        return float(value)
    if expected_type is str:
        if not isinstance(value, str):
            raise _type_error(field_name, expected_type)
        return value
    return value


def parse_environment_value(raw_value: str, expected_type: object, field_name: str) -> object:
    """Parse one string override without including its raw value in errors."""
    origin = get_origin(expected_type)
    arguments = get_args(expected_type)
    normalized = raw_value.strip()
    try:
        if origin in (Union, UnionType):
            if not normalized and type(None) in arguments:
                return None
            candidate = next(item for item in arguments if item is not type(None))
            return parse_environment_value(raw_value, candidate, field_name)
        if origin is tuple:
            parsed = _parse_sequence(normalized)
            item_type = arguments[0] if arguments else str
            if get_origin(item_type) is tuple and parsed and not isinstance(parsed[0], list):
                if len(parsed) != 16:
                    raise ValueError
                parsed = [parsed[index : index + 4] for index in range(0, 16, 4)]
            return tuple(
                _parse_environment_sequence_item(item, item_type, field_name)
                for item in parsed
            )
        if expected_type is bool:
            lowered = normalized.lower()
            if lowered in _TRUE_VALUES:
                return True
            if lowered in _FALSE_VALUES:
                return False
            raise ValueError
        if expected_type is int:
            return int(normalized)
        if expected_type is float:
            return float(normalized)
        if expected_type is str:
            return raw_value
        raise ValueError
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigLoadError(f"环境变量 {field_name} 的类型或格式无效") from exc


def _parse_sequence(value: str) -> list[object]:
    if not value:
        return []
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError
        return parsed
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_environment_sequence_item(
    value: object,
    expected_type: object,
    field_name: str,
) -> object:
    if isinstance(value, str):
        return parse_environment_value(value, expected_type, field_name)
    return coerce_value(value, expected_type, field_name)


def _type_error(field_name: str, expected_type: object) -> ConfigLoadError:
    return ConfigLoadError(f"配置项 {field_name} 的类型无效，应为 {expected_type}")

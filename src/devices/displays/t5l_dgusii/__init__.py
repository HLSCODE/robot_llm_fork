"""
T5L DGUSII SDK package.

Exports are loaded lazily so selecting another expression-display provider
does not import serial-specific dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "DgusClient": ".client",
    "TraceCallback": ".client",
    "print_trace": ".client",
    "DEFAULT_EXPRESSION_CONFIGS": ".config",
    "DEFAULT_SDK_CONFIG": ".config",
    "DgusSdkConfig": ".config",
    "ExpressionConfig": ".config",
    "config_from_dict": ".config",
    "config_to_dict": ".config",
    "load_config": ".config",
    "AnimationIconConfig": ".controls",
    "AnimationIconControl": ".controls",
    "Expression": ".services",
    "ExpressionSwitcher": ".services",
    "default_expressions": ".services",
    "parse_expression_specs": ".services",
    "T5LDgusSdk": ".sdk",
    "T5LServiceContainer": ".sdk",
    "close_sdk": ".sdk",
    "create_sdk": ".sdk",
    "default_container": ".sdk",
    "get_expression_service": ".sdk",
    "get_sdk": ".sdk",
    "build_write_bytes_frame": ".protocol",
    "build_write_frame": ".protocol",
    "build_write_words_frame": ".protocol",
    "SerialConfig": ".transport",
    "SerialTransport": ".transport",
    "parse_int": ".utils",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value

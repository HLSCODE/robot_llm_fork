from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..base import ClearBeforeSwitch
from .controls import AnimationIconConfig
from .services import Expression
from .transport import SerialConfig
from .utils import parse_int


@dataclass(frozen=True)
class ExpressionConfig:
    name: str
    icon_lib: int
    icon_start: int = 0
    icon_end: int = 63

    def to_expression(self) -> Expression:
        return Expression(self.name, self.icon_lib, self.icon_start, self.icon_end)


@dataclass(frozen=True)
class DgusSdkConfig:
    serial: SerialConfig = field(default_factory=SerialConfig)
    animation_icon: AnimationIconConfig = field(default_factory=AnimationIconConfig)
    expressions: list[ExpressionConfig] = field(default_factory=list)
    clear_vps: list[int] = field(default_factory=list)
    test_interval: float = 1.5

    def expression_models(self) -> list[Expression]:
        return [item.to_expression() for item in self.expressions]


DEFAULT_EXPRESSION_CONFIGS: list[ExpressionConfig] = [
    ExpressionConfig("happy", 24, 0, 63),
    ExpressionConfig("sad", 27, 0, 63),
    ExpressionConfig("angry", 30, 0, 63),
    ExpressionConfig("speechless", 33, 0, 63),
    ExpressionConfig("default_1", 36, 0, 63),
    ExpressionConfig("default_2", 39, 0, 63),
]


DEFAULT_SDK_CONFIG = DgusSdkConfig(expressions=DEFAULT_EXPRESSION_CONFIGS)


def _parse_optional_int(value: Any) -> int:
    return parse_int(value) if isinstance(value, str) else int(value)


def _parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"{name} must be a boolean")


def _parse_clear_before_switch(value: Any) -> ClearBeforeSwitch:
    if value == "stop":
        return "stop"
    if value == "hide":
        return "hide"
    if value == "none":
        return "none"
    raise ValueError(
        "animation_icon.clear_before_switch must be one of ['hide', 'none', 'stop']"
    )


def _load_serial_config(data: dict[str, Any]) -> SerialConfig:
    base = asdict(DEFAULT_SDK_CONFIG.serial)
    base.update(data)
    return SerialConfig(
        port=str(base["port"]),
        baudrate=int(base["baudrate"]),
        timeout=float(base["timeout"]),
        write_timeout=float(base["write_timeout"]),
    )


def _load_animation_icon_config(data: dict[str, Any]) -> AnimationIconConfig:
    base = asdict(DEFAULT_SDK_CONFIG.animation_icon)
    base.update(data)
    return AnimationIconConfig(
        vp_addr=_parse_optional_int(base["vp_addr"]),
        sp_addr=_parse_optional_int(base["sp_addr"]),
        start_value=_parse_optional_int(base["start_value"]),
        stop_value=_parse_optional_int(base["stop_value"]),
        hide_value=_parse_optional_int(base["hide_value"]),
        clear_before_switch=_parse_clear_before_switch(base["clear_before_switch"]),
        switch_delay=float(base["switch_delay"]),
        update_icon_range=_parse_bool(base["update_icon_range"], "animation_icon.update_icon_range"),
    )


def _load_expression_config(item: dict[str, Any]) -> ExpressionConfig:
    return ExpressionConfig(
        name=str(item["name"]),
        icon_lib=_parse_optional_int(item["icon_lib"]),
        icon_start=_parse_optional_int(item.get("icon_start", 0)),
        icon_end=_parse_optional_int(item.get("icon_end", 63)),
    )


def config_from_dict(data: dict[str, Any]) -> DgusSdkConfig:
    return DgusSdkConfig(
        serial=_load_serial_config(data.get("serial", {})),
        animation_icon=_load_animation_icon_config(data.get("animation_icon", {})),
        expressions=[
            _load_expression_config(item)
            for item in data.get("expressions", asdict(DEFAULT_SDK_CONFIG)["expressions"])
        ],
        clear_vps=[_parse_optional_int(item) for item in data.get("clear_vps", [])],
        test_interval=float(data.get("test_interval", DEFAULT_SDK_CONFIG.test_interval)),
    )


def load_config(path: str | Path | None = None) -> DgusSdkConfig:
    if path is None:
        return DEFAULT_SDK_CONFIG

    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return config_from_dict(data)


def config_to_dict(config: DgusSdkConfig = DEFAULT_SDK_CONFIG) -> dict[str, Any]:
    return {
        "serial": asdict(config.serial),
        "animation_icon": asdict(config.animation_icon),
        "expressions": [asdict(item) for item in config.expressions],
        "clear_vps": config.clear_vps,
        "test_interval": config.test_interval,
    }

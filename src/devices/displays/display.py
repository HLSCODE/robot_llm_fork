from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ExpressionDisplayBackend, ExpressionSpec


class ExpressionDisplayUnavailable(RuntimeError):
    """Raised when the optional expression display is disabled or unavailable."""


DEFAULT_EXPRESSIONS: tuple[ExpressionSpec, ...] = (
    ExpressionSpec("happy", 24, 0, 63),
    ExpressionSpec("sad", 27, 0, 63),
    ExpressionSpec("angry", 30, 0, 63),
    ExpressionSpec("speechless", 33, 0, 63),
    ExpressionSpec("default_1", 36, 0, 63),
    ExpressionSpec("default_2", 39, 0, 63),
)

PROVIDER_T5L_DGUSII = "t5l_dgusii"


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_int(value: Any, *, default: int | None = None) -> int:
    if value is None or value == "":
        if default is None:
            raise ValueError("Missing integer value")
        return default
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 0)


def _parse_float(value: Any, *, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _parse_expression_specs(value: Any) -> tuple[ExpressionSpec, ...]:
    if value is None:
        return DEFAULT_EXPRESSIONS
    if isinstance(value, (list, tuple)):
        result: list[ExpressionSpec] = []
        for item in value:
            if isinstance(item, ExpressionSpec):
                result.append(item)
            elif isinstance(item, dict):
                result.append(
                    ExpressionSpec(
                        name=str(item["name"]),
                        icon_lib=_parse_int(item["icon_lib"]),
                        icon_start=_parse_int(item.get("icon_start"), default=0),
                        icon_end=_parse_int(item.get("icon_end"), default=63),
                    )
                )
            else:
                raise ValueError(f"Invalid expression item: {item}")
        return tuple(result) or DEFAULT_EXPRESSIONS

    text = str(value).strip()
    if not text:
        return DEFAULT_EXPRESSIONS

    result: list[ExpressionSpec] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(":")]
        if len(parts) == 2:
            name, icon_lib = parts
            result.append(ExpressionSpec(name, _parse_int(icon_lib)))
        elif len(parts) == 4:
            name, icon_lib, icon_start, icon_end = parts
            result.append(
                ExpressionSpec(
                    name=name,
                    icon_lib=_parse_int(icon_lib),
                    icon_start=_parse_int(icon_start),
                    icon_end=_parse_int(icon_end),
                )
            )
        else:
            raise ValueError(f"Invalid expression spec: {item}")
    return tuple(result) or DEFAULT_EXPRESSIONS


def _parse_int_list(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(_parse_int(item) for item in value if item != "")
    text = str(value).strip()
    if not text:
        return ()
    return tuple(_parse_int(item.strip()) for item in text.split(",") if item.strip())


@dataclass(frozen=True)
class ExpressionDisplaySettings:
    enabled: bool = False
    provider: str = PROVIDER_T5L_DGUSII
    config_path: Path | None = None
    port: str = "COM4"
    baudrate: int = 115200
    timeout: float = 0.5
    write_timeout: float = 1.0
    vp_addr: int = 0x5602
    sp_addr: int = 0x8000
    start_value: int = 0x0000
    stop_value: int = 0x0001
    hide_value: int = 0x0002
    clear_before_switch: str = "stop"
    switch_delay: float = 0.1
    update_icon_range: bool = True
    expressions: tuple[ExpressionSpec, ...] = DEFAULT_EXPRESSIONS
    clear_vps: tuple[int, ...] = ()
    test_interval: float = 1.5
    tx_delay: float = 0.05

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ExpressionDisplaySettings":
        config_path_value = str(data.get("config_path", "") or "").strip()
        config_path = Path(config_path_value) if config_path_value else None

        return cls(
            enabled=_parse_bool(data.get("enabled"), default=False),
            provider=str(data.get("provider") or PROVIDER_T5L_DGUSII).strip().lower(),
            config_path=config_path,
            port=str(data.get("port") or "COM4"),
            baudrate=_parse_int(data.get("baudrate"), default=115200),
            timeout=_parse_float(data.get("timeout"), default=0.5),
            write_timeout=_parse_float(data.get("write_timeout"), default=1.0),
            vp_addr=_parse_int(data.get("vp_addr"), default=0x5602),
            sp_addr=_parse_int(data.get("sp_addr"), default=0x8000),
            start_value=_parse_int(data.get("start_value"), default=0x0000),
            stop_value=_parse_int(data.get("stop_value"), default=0x0001),
            hide_value=_parse_int(data.get("hide_value"), default=0x0002),
            clear_before_switch=str(data.get("clear_before_switch") or "stop"),
            switch_delay=_parse_float(data.get("switch_delay"), default=0.1),
            update_icon_range=_parse_bool(data.get("update_icon_range"), default=True),
            expressions=_parse_expression_specs(data.get("expressions")),
            clear_vps=_parse_int_list(data.get("clear_vps")),
            test_interval=_parse_float(data.get("test_interval"), default=1.5),
            tx_delay=_parse_float(data.get("tx_delay"), default=0.05),
        )


class ExpressionDisplay:
    """Project-level facade that delegates to the configured display strategy."""

    def __init__(self, settings: ExpressionDisplaySettings):
        self.settings = settings
        self._backend = create_backend(settings)

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def list_configured_expressions(self) -> list[ExpressionSpec]:
        return self._backend.list_configured_expressions()

    def switch(self, expression: str | int):
        return self._backend.switch(expression)

    def run_test(self) -> None:
        self._backend.run_test()

    def close(self) -> None:
        self._backend.close()


def create_backend(settings: ExpressionDisplaySettings) -> ExpressionDisplayBackend:
    from .registry import resolve_expression_display_provider

    provider = resolve_expression_display_provider(settings.provider)
    return provider.create(settings)

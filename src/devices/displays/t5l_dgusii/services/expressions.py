from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from ..utils import parse_int


@dataclass(frozen=True)
class Expression:
    name: str
    icon_lib: int
    icon_start: int = 0
    icon_end: int = 63


class ExpressionTarget(Protocol):
    @property
    def active_vp(self) -> int:
        ...

    def hide_vp(self, addr: int) -> bytes:
        ...

    def switch_icon_lib(
        self,
        icon_lib: int,
        *,
        icon_start: int = 0,
        icon_end: int = 63,
        mode: int | None = None,
    ) -> None:
        ...


def default_expressions() -> list[Expression]:
    from ..config import DEFAULT_SDK_CONFIG

    return DEFAULT_SDK_CONFIG.expression_models()


def parse_expression_specs(text: str) -> list[Expression]:
    """
    Parse: name:lib or name:lib:start:end, comma separated.
    Example: happy:24:0:63,sad:27:0:63
    """
    if not text.strip():
        return default_expressions()

    result: list[Expression] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue

        parts = item.split(":")
        if len(parts) == 2:
            name, lib = parts
            result.append(Expression(name=name, icon_lib=parse_int(lib)))
        elif len(parts) == 4:
            name, lib, start, end = parts
            result.append(
                Expression(
                    name=name,
                    icon_lib=parse_int(lib),
                    icon_start=parse_int(start),
                    icon_end=parse_int(end),
                )
            )
        else:
            raise ValueError(f"Invalid expression spec: {item}")

    if not result:
        raise ValueError("No expression configured")
    return result


class ExpressionSwitcher:
    def __init__(
        self,
        target: ExpressionTarget,
        expressions: Sequence[Expression] | None = None,
        *,
        clear_vps: Sequence[int] | None = None,
        test_interval: float = 1.5,
    ):
        self.target = target
        self.expressions = list(expressions or default_expressions())
        self.clear_vps = list(clear_vps or [])
        self.test_interval = test_interval
        self.current: str | None = None
        self._lock = RLock()

    def list_expressions(self) -> list[Expression]:
        return list(self.expressions)

    def get(self, value: str | int) -> Expression:
        if isinstance(value, int) or str(value).isdigit():
            index = int(value)
            if index < 1 or index > len(self.expressions):
                raise ValueError(f"Expression index out of range: 1~{len(self.expressions)}")
            return self.expressions[index - 1]

        name = str(value).lower()
        for expression in self.expressions:
            if expression.name.lower() == name:
                return expression
        raise ValueError(f"Unknown expression: {value}")

    def clear_legacy_vps(self) -> None:
        active_vp = self.target.active_vp
        for addr in self.clear_vps:
            if addr != active_vp:
                self.target.hide_vp(addr)

    def switch(self, value: str | int) -> Expression:
        with self._lock:
            expression = self.get(value)
            self.clear_legacy_vps()
            self.target.switch_icon_lib(
                expression.icon_lib,
                icon_start=expression.icon_start,
                icon_end=expression.icon_end,
            )
            self.current = expression.name
            return expression

    def run_test(self) -> None:
        with self._lock:
            for expression in self.expressions:
                self.switch(expression.name)
                time.sleep(self.test_interval)

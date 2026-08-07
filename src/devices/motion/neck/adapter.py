"""Project adapter for the transport-injected PWM neck controller."""

from __future__ import annotations

from collections.abc import Mapping

from ...transports import Transport
from .pwm import (
    HorizontalServoConfig,
    NeckController,
    ServoAxis,
    VerticalServoConfig,
)


class PWMNeckController:
    """颈部双轴舵机控制器（水平 + 垂直）"""

    def __init__(
        self,
        transport: Transport,
        horizontal_config: Mapping[str, object],
        vertical_config: Mapping[str, object],
    ) -> None:
        h_cfg = HorizontalServoConfig(**_servo_config_values(horizontal_config))
        v_cfg = VerticalServoConfig(**_servo_config_values(vertical_config))
        self._controller = NeckController(transport, h_cfg, v_cfg)

    # ---------------- 对外 API（代理到 SDK）----------------
    def move_horizontal(self, pwm: int, time_ms: int | None = None) -> None:
        """水平舵机移到绝对 PWM 值"""
        self._controller.move_to(pwm, ServoAxis.HORIZONTAL, time_ms)

    def move_vertical(self, pwm: int, time_ms: int | None = None) -> None:
        self._controller.move_to(pwm, ServoAxis.VERTICAL, time_ms)

    def move_both(
        self,
        h_pwm: int,
        v_pwm: int,
        time_ms: int | None = None,
    ) -> None:
        self._controller.move_to_both(h_pwm, v_pwm, time_ms)

    def offset_horizontal(
        self,
        offset: int,
        time_ms: int | None = None,
    ) -> None:
        self._controller.move_offset(offset, ServoAxis.HORIZONTAL, time_ms)

    def offset_vertical(
        self,
        offset: int,
        time_ms: int | None = None,
    ) -> None:
        self._controller.move_offset(offset, ServoAxis.VERTICAL, time_ms)

    def reset(self, time_ms: int | None = None) -> None:
        self._controller.reset(time_ms)

    @property
    def current_pwm(self) -> dict[ServoAxis, int]:
        return self._controller.current_pwm

    def close(self) -> None:
        self._controller.close()


def _servo_config_values(config: Mapping[str, object]) -> dict[str, int]:
    fields = ("servo_id", "initial_pwm", "pwm_max", "pwm_min", "default_time")
    values: dict[str, int] = {}
    for field in fields:
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field} must be an integer")
        values[field] = value
    return values

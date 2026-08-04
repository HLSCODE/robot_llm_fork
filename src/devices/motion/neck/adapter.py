"""Project adapter for the transport-injected PWM neck controller."""

from __future__ import annotations

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
        horizontal_config: dict,
        vertical_config: dict,
    ) -> None:
        h_cfg = HorizontalServoConfig(**horizontal_config)
        v_cfg = VerticalServoConfig(**vertical_config)
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
    def current_pwm(self) -> dict:
        return self._controller.current_pwm

    def close(self) -> None:
        self._controller.close()

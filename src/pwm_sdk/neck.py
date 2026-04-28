"""
Neck servo controller.

Serial command formats
----------------------
Single servo:  #IndexPpwmTtime!
  - '#'   : 1 char  (literal)
  - Index : 3 chars (000-254, zero-padded)
  - 'P'   : 1 char  (literal)
  - pwm   : 4 chars (0500-2500, zero-padded)
  - 'T'   : 1 char  (literal)
  - time  : 4 chars (0000-9999, zero-padded)
  - '!'   : 1 char  (literal)
  Total   : 15 chars

Multi-servo:   {#000P1500T1000!#001P0900T1000!}
  Wrap multiple single-servo commands in '{' '}' with no separators.
"""

from __future__ import annotations

import time
from enum import Enum

import serial

from .config import HorizontalServoConfig, ServoConfig, VerticalServoConfig


class ServoAxis(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


def _build_single_cmd(servo_id: int, pwm: int, time_ms: int) -> str:
    """Build a 15-char single-servo command string."""
    return f"#{servo_id:03d}P{pwm:04d}T{time_ms:04d}!"


class NeckController:
    """
    Controls a two-servo neck assembly over a serial port.

    Parameters
    ----------
    serial_port : str
        Serial port identifier, e.g. ``"COM3"`` or ``"/dev/ttyUSB0"``.
    baud_rate : int
        Baud rate for the serial connection.
    horizontal_config : HorizontalServoConfig, optional
        Configuration for the horizontal (left-right) servo.
    vertical_config : VerticalServoConfig, optional
        Configuration for the vertical (up-down) servo.

    Examples
    --------
    >>> ctrl = NeckController("COM3", 9600)
    >>> ctrl.move_to(1800, ServoAxis.HORIZONTAL)
    >>> ctrl.move_offset(-100, ServoAxis.VERTICAL)
    >>> ctrl.reset()
    >>> ctrl.close()
    """

    def __init__(
        self,
        serial_port: str,
        baud_rate: int,
        horizontal_config: HorizontalServoConfig | None = None,
        vertical_config: VerticalServoConfig | None = None,
    ) -> None:
        self._h_cfg: HorizontalServoConfig = horizontal_config or HorizontalServoConfig()
        self._v_cfg: VerticalServoConfig = vertical_config or VerticalServoConfig()

        # Track current PWM for each axis
        self._current_pwm: dict[ServoAxis, int] = {
            ServoAxis.HORIZONTAL: self._h_cfg.initial_pwm,
            ServoAxis.VERTICAL: self._v_cfg.initial_pwm,
        }

        # Open without port first, then assert RTS=False and DTR=False before
        # activating the port.  Both lines are wired to the controller's MCU
        # reset pin on some boards; asserting them causes an unwanted reboot.
        self._serial = serial.Serial(
            baudrate=baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        self._serial.rts = False
        self._serial.dtr = False
        self._serial.port = serial_port
        self._serial.open()
        # Immediately de-assert both lines so neither triggers an MCU reset.

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def move_to(
        self,
        pwm: int,
        axis: ServoAxis,
        time_ms: int | None = None,
    ) -> None:
        """
        Move a servo to an absolute PWM position.

        Parameters
        ----------
        pwm : int
            Target PWM value (clamped to the servo's configured range).
        axis : ServoAxis
            Which servo to move.
        time_ms : int, optional
            Movement duration in milliseconds. Uses the servo's default if omitted.
        """
        cfg = self._cfg(axis)
        pwm = self._clamp(pwm, cfg)
        duration = time_ms if time_ms is not None else cfg.default_time
        self._send_single(cfg.servo_id, pwm, duration)
        self._current_pwm[axis] = pwm
        time.sleep(duration / 1000)

    def move_offset(
        self,
        offset: int,
        axis: ServoAxis,
        time_ms: int | None = None,
    ) -> None:
        """
        Move a servo by a PWM offset relative to its current position.

        Parameters
        ----------
        offset : int
            PWM offset (positive or negative).
        axis : ServoAxis
            Which servo to move.
        time_ms : int, optional
            Movement duration in milliseconds. Uses the servo's default if omitted.
        """
        target = self._current_pwm[axis] + offset
        self.move_to(target, axis, time_ms)

    def move_to_both(
        self,
        h_pwm: int,
        v_pwm: int,
        time_ms: int | None = None,
    ) -> None:
        """
        Move both servos simultaneously to absolute PWM positions.

        Uses a single multi-servo command so both axes start moving at the
        same time, then blocks for ``max(h_time, v_time)`` milliseconds.

        Parameters
        ----------
        h_pwm : int
            Target PWM for the horizontal servo (clamped to its range).
        v_pwm : int
            Target PWM for the vertical servo (clamped to its range).
        time_ms : int, optional
            Movement duration for both servos. Uses each servo's own
            ``default_time`` if omitted.
        """
        h_pwm = self._clamp(h_pwm, self._h_cfg)
        v_pwm = self._clamp(v_pwm, self._v_cfg)
        h_time = time_ms if time_ms is not None else self._h_cfg.default_time
        v_time = time_ms if time_ms is not None else self._v_cfg.default_time
        h_cmd = _build_single_cmd(self._h_cfg.servo_id, h_pwm, h_time)
        v_cmd = _build_single_cmd(self._v_cfg.servo_id, v_pwm, v_time)
        self._send_raw("{" + h_cmd + v_cmd + "}")
        self._current_pwm[ServoAxis.HORIZONTAL] = h_pwm
        self._current_pwm[ServoAxis.VERTICAL] = v_pwm
        time.sleep(max(h_time, v_time) / 1000)

    def move_offset_both(
        self,
        h_offset: int,
        v_offset: int,
        time_ms: int | None = None,
    ) -> None:
        """
        Move both servos simultaneously by PWM offsets relative to their
        current positions.

        Parameters
        ----------
        h_offset : int
            PWM offset for the horizontal servo (positive or negative).
        v_offset : int
            PWM offset for the vertical servo (positive or negative).
        time_ms : int, optional
            Movement duration for both servos. Uses each servo's own
            ``default_time`` if omitted.
        """
        h_target = self._current_pwm[ServoAxis.HORIZONTAL] + h_offset
        v_target = self._current_pwm[ServoAxis.VERTICAL] + v_offset
        self.move_to_both(h_target, v_target, time_ms)

    def reset(self, time_ms: int | None = None) -> None:
        """
        Return both servos to their initial PWM positions simultaneously.

        Parameters
        ----------
        time_ms : int, optional
            Movement duration in milliseconds for both servos.
            Uses each servo's own default if omitted.
        """
        h_time = time_ms if time_ms is not None else self._h_cfg.default_time
        v_time = time_ms if time_ms is not None else self._v_cfg.default_time

        h_cmd = _build_single_cmd(self._h_cfg.servo_id, self._h_cfg.initial_pwm, h_time)
        v_cmd = _build_single_cmd(self._v_cfg.servo_id, self._v_cfg.initial_pwm, v_time)
        self._send_raw("{" + h_cmd + v_cmd + "}")
        self._current_pwm[ServoAxis.HORIZONTAL] = self._h_cfg.initial_pwm
        self._current_pwm[ServoAxis.VERTICAL] = self._v_cfg.initial_pwm
        time.sleep(max(h_time, v_time) / 1000)

    @property
    def current_pwm(self) -> dict[ServoAxis, int]:
        """Return a copy of the current PWM values keyed by axis."""
        return dict(self._current_pwm)

    def close(self) -> None:
        """Close the underlying serial connection."""
        if self._serial.is_open:
            print("[CLOSE] Closing serial connection")
            self._serial.close()

    def __enter__(self) -> "NeckController":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cfg(self, axis: ServoAxis) -> ServoConfig:
        return self._h_cfg if axis == ServoAxis.HORIZONTAL else self._v_cfg

    @staticmethod
    def _clamp(pwm: int, cfg: ServoConfig) -> int:
        return max(cfg.pwm_min, min(cfg.pwm_max, pwm))

    def _send_single(self, servo_id: int, pwm: int, time_ms: int) -> None:
        self._send_raw(_build_single_cmd(servo_id, pwm, time_ms))

    def _send_raw(self, data: str) -> None:
        print(f"[TX] {data}")
        self._serial.write(data.encode("ascii"))
        # self._serial.flush()  # block until all bytes are physically transmitted
        self._read_response()

    def _read_response(self, timeout: float = 0.1) -> None:
        """Read and print any bytes the controller sends back within *timeout* seconds."""
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            waiting = self._serial.in_waiting
            if waiting:
                buf += self._serial.read(waiting)
                deadline = time.time() + timeout  # extend on each new chunk
        if buf:
            try:
                print(f"[RX] {buf.decode('ascii', errors='replace')}")
            except Exception:
                print(f"[RX] (hex) {buf.hex(' ').upper()}")

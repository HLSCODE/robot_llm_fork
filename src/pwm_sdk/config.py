from dataclasses import dataclass, field

PWM_ABSOLUTE_MIN = 500
PWM_ABSOLUTE_MAX = 2500


@dataclass
class ServoConfig:
    """Base configuration for a servo motor."""
    servo_id: int
    initial_pwm: int
    pwm_max: int
    pwm_min: int
    default_time: int

    def __post_init__(self) -> None:
        if not (PWM_ABSOLUTE_MIN <= self.pwm_min <= self.pwm_max <= PWM_ABSOLUTE_MAX):
            raise ValueError(
                f"pwm_min ({self.pwm_min}) and pwm_max ({self.pwm_max}) must satisfy "
                f"{PWM_ABSOLUTE_MIN} <= pwm_min <= pwm_max <= {PWM_ABSOLUTE_MAX}"
            )
        if not (self.pwm_min <= self.initial_pwm <= self.pwm_max):
            raise ValueError(
                f"initial_pwm ({self.initial_pwm}) must be within "
                f"[{self.pwm_min}, {self.pwm_max}]"
            )
        if self.default_time < 0 or self.default_time > 9999:
            raise ValueError(f"default_time must be between 0 and 9999, got {self.default_time}")


@dataclass
class HorizontalServoConfig(ServoConfig):
    """
    Configuration for the horizontal (left-right) servo.
    360-degree servo: PWM 500 = 0°, PWM 2500 = 360°.

    Defaults:
        pwm_max    = 2100
        pwm_min    = 1100
        default_time = 1500
        initial_pwm  = 1600
    """
    servo_id: int = 0
    initial_pwm: int = 1600
    pwm_max: int = 2100
    pwm_min: int = 1100
    default_time: int = 1500


@dataclass
class VerticalServoConfig(ServoConfig):
    """
    Configuration for the vertical (up-down) servo.
    180-degree servo: PWM 500 = 0°, PWM 2500 = 180°.

    Defaults:
        pwm_max    = 1700
        pwm_min    = 1200
        default_time = 2500
        initial_pwm  = 1600
    """
    servo_id: int = 1
    initial_pwm: int = 1600
    pwm_max: int = 1700
    pwm_min: int = 1200
    default_time: int = 2500

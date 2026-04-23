"""
PWM 颈部舵机控制器（项目适配层）

对 pwm_sdk.NeckController 的薄封装，接口风格与项目其他
串口设备（ADP, ModbusMotor, Kuaihuanshou）一致：
- 构造函数参数可选，None 时从 config.env 读取
- 提供 close() 释放串口
- 异常在构造时吞掉并返回 None ser，不阻塞启动
"""
from typing import Optional

# 延迟加载配置（避免循环导入，跟 adp.py 同样的套路）
_pwm_neck_config_cache = None

def _get_pwm_neck_config():
    global _pwm_neck_config_cache
    if _pwm_neck_config_cache is None:
        try:
            from ..core.config_loader import Config
            _pwm_neck_config_cache = Config.get_instance().get_pwm_neck_config()
        except Exception as e:
            print(f"加载 PWM 颈部舵机配置失败：{e}，使用默认值")
            _pwm_neck_config_cache = {
                "port": "/dev/neck",
                "baudrate": 9600,
                "horizontal": {
                    "servo_id": 0, "initial_pwm": 1600,
                    "pwm_min": 1100, "pwm_max": 2100, "default_time": 1500,
                },
                "vertical": {
                    "servo_id": 1, "initial_pwm": 1600,
                    "pwm_min": 1200, "pwm_max": 1700, "default_time": 2500,
                },
            }
    return _pwm_neck_config_cache


class PWMNeckController:
    """颈部双轴舵机控制器（水平 + 垂直）"""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: Optional[int] = None,
        horizontal_config: Optional[dict] = None,
        vertical_config: Optional[dict] = None,
    ):
        cfg = _get_pwm_neck_config()
        port = port or cfg["port"]
        baudrate = baudrate or cfg["baudrate"]
        h_cfg_dict = horizontal_config or cfg["horizontal"]
        v_cfg_dict = vertical_config or cfg["vertical"]

        self.port = port
        self.baudrate = baudrate
        self._controller = None  # pwm_sdk.NeckController 实例

        # 延迟 import SDK，避免项目启动时 SDK 任何问题阻塞
        try:
            from ..pwm_sdk import (
                NeckController, HorizontalServoConfig, VerticalServoConfig,
            )
            h_cfg = HorizontalServoConfig(**h_cfg_dict)
            v_cfg = VerticalServoConfig(**v_cfg_dict)
            self._controller = NeckController(port, baudrate, h_cfg, v_cfg)
            print(f"PWM 颈部舵机初始化成功：{port} @ {baudrate}")
        except Exception as e:
            print(f"PWM 颈部舵机初始化失败：{e}")
            self._controller = None

    # ---------------- 对外 API（代理到 SDK）----------------
    def move_horizontal(self, pwm: int, time_ms: Optional[int] = None):
        """水平舵机移到绝对 PWM 值"""
        from ..pwm_sdk import ServoAxis
        if self._controller is None:
            return
        self._controller.move_to(pwm, ServoAxis.HORIZONTAL, time_ms)

    def move_vertical(self, pwm: int, time_ms: Optional[int] = None):
        from ..pwm_sdk import ServoAxis
        if self._controller is None:
            return
        self._controller.move_to(pwm, ServoAxis.VERTICAL, time_ms)

    def move_both(self, h_pwm: int, v_pwm: int, time_ms: Optional[int] = None):
        if self._controller is None:
            return
        self._controller.move_to_both(h_pwm, v_pwm, time_ms)

    def offset_horizontal(self, offset: int, time_ms: Optional[int] = None):
        from ..pwm_sdk import ServoAxis
        if self._controller is None:
            return
        self._controller.move_offset(offset, ServoAxis.HORIZONTAL, time_ms)

    def offset_vertical(self, offset: int, time_ms: Optional[int] = None):
        from ..pwm_sdk import ServoAxis
        if self._controller is None:
            return
        self._controller.move_offset(offset, ServoAxis.VERTICAL, time_ms)

    def reset(self, time_ms: Optional[int] = None):
        if self._controller is None:
            return
        self._controller.reset(time_ms)

    @property
    def current_pwm(self) -> dict:
        if self._controller is None:
            return {}
        return self._controller.current_pwm

    def close(self):
        if self._controller is not None:
            try:
                self._controller.close()
                print(f"PWM 颈部舵机串口 {self.port} 已关闭")
            except Exception as e:
                print(f"关闭 PWM 颈部舵机失败：{e}")
            self._controller = None
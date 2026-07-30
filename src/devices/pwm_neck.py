"""
PWM 颈部舵机控制器（项目适配层）

对 pwm_sdk.NeckController 的薄封装，接口风格与项目其他
串口设备（ADP, ModbusMotor, Kuaihuanshou）一致：
- 构造函数接收组合根解析后的显式配置
- 提供 close() 释放串口
- 异常在构造时吞掉并返回 None ser，不阻塞启动
"""
from typing import Optional
from ..pwm_sdk import (
    NeckController, HorizontalServoConfig, VerticalServoConfig, ServoAxis
)

class PWMNeckController:
    """颈部双轴舵机控制器（水平 + 垂直）"""

    def __init__(
        self,
        port: str,
        baudrate: int,
        horizontal_config: dict,
        vertical_config: dict,
    ):
        self.port = port
        self.baudrate = baudrate
        self._controller = None  # pwm_sdk.NeckController 实例

        # 初始化 SDK 实例
        try:
            h_cfg = HorizontalServoConfig(**horizontal_config)
            v_cfg = VerticalServoConfig(**vertical_config)
            self._controller = NeckController(port, baudrate, h_cfg, v_cfg)
            print(f"PWM 颈部舵机初始化成功：{port} @ {baudrate}")
        except Exception as e:
            print(f"PWM 颈部舵机初始化失败：{e}")
            self._controller = None

    # ---------------- 对外 API（代理到 SDK）----------------
    def move_horizontal(self, pwm: int, time_ms: Optional[int] = None):
        """水平舵机移到绝对 PWM 值"""
        if self._controller is None:
            return
        self._controller.move_to(pwm, ServoAxis.HORIZONTAL, time_ms)

    def move_vertical(self, pwm: int, time_ms: Optional[int] = None):
        if self._controller is None:
            return
        self._controller.move_to(pwm, ServoAxis.VERTICAL, time_ms)

    def move_both(self, h_pwm: int, v_pwm: int, time_ms: Optional[int] = None):
        if self._controller is None:
            return
        self._controller.move_to_both(h_pwm, v_pwm, time_ms)

    def offset_horizontal(self, offset: int, time_ms: Optional[int] = None):
        if self._controller is None:
            return
        self._controller.move_offset(offset, ServoAxis.HORIZONTAL, time_ms)

    def offset_vertical(self, offset: int, time_ms: Optional[int] = None):
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

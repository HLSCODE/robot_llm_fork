from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from ..client import DgusClient
from ..protocol import check_byte, check_word

ClearBeforeSwitch = Literal["stop", "hide", "none"]


@dataclass(frozen=True)
class AnimationIconConfig:
    vp_addr: int = 0x5602
    sp_addr: int = 0x8000
    start_value: int = 0x0000
    stop_value: int = 0x0001
    hide_value: int = 0x0002
    clear_before_switch: ClearBeforeSwitch = "stop"
    switch_delay: float = 0.10
    update_icon_range: bool = True


class AnimationIconControl:
    """
    DGUSII animation icon control.

    Protocol details stay in DgusClient/protocol; this class only models the
    animation icon SP/VP semantics from the application guide:
    SP+0x07 ICON_Start, SP+0x08 ICON_End, SP+0x09:H ICON_Lib.
    """

    def __init__(self, client: DgusClient, config: AnimationIconConfig):
        self.client = client
        self.config = config

    @property
    def active_vp(self) -> int:
        return self.config.vp_addr

    def start(self) -> bytes:
        return self.client.write_words(
            self.config.vp_addr,
            [self.config.start_value],
            "VP=V_Start, start animation",
        )

    def stop(self) -> bytes:
        return self.client.write_words(
            self.config.vp_addr,
            [self.config.stop_value],
            "VP=V_Stop, stop animation",
        )

    def hide(self) -> bytes:
        return self.hide_vp(self.config.vp_addr)

    def hide_vp(self, addr: int) -> bytes:
        return self.client.write_words(
            addr,
            [self.config.hide_value],
            f"VP other value, hide VP=0x{addr:04X}",
        )

    def write_icon_range(self, icon_start: int, icon_end: int) -> bytes:
        return self.client.write_words(
            self.config.sp_addr + 0x07,
            [check_word(icon_start, "icon_start"), check_word(icon_end, "icon_end")],
            f"SP+0x07/0x08, ICON_Start={icon_start}, ICON_End={icon_end}",
        )

    def write_icon_lib(self, icon_lib: int, *, mode: int | None = None) -> bytes:
        """
        Set ICON_Lib.

        When mode is None, only SP+0x09:H is written as a single byte:
            5A A5 04 82 80 09 18

        If mode is provided, SP+0x09 is written as one word:
            ICON_Lib in high byte, Mode in low byte.
        """
        icon_lib = check_byte(icon_lib, "icon_lib")
        if mode is None:
            return self.client.write_bytes(
                self.config.sp_addr + 0x09,
                [icon_lib],
                f"SP+0x09:H, ICON_Lib=0x{icon_lib:02X}",
            )

        mode = check_byte(mode, "mode")
        value = (icon_lib << 8) | mode
        return self.client.write_words(
            self.config.sp_addr + 0x09,
            [value],
            f"SP+0x09, ICON_Lib=0x{icon_lib:02X}, Mode=0x{mode:02X}",
        )

    def prepare_switch(self) -> None:
        if self.config.clear_before_switch == "stop":
            self.stop()
            time.sleep(self.config.switch_delay)
        elif self.config.clear_before_switch == "hide":
            self.hide()
            time.sleep(self.config.switch_delay)
        elif self.config.clear_before_switch == "none":
            return
        else:
            raise ValueError("clear_before_switch must be stop, hide, or none")

    def switch_icon_lib(
        self,
        icon_lib: int,
        *,
        icon_start: int = 0,
        icon_end: int = 63,
        mode: int | None = None,
    ) -> None:
        self.prepare_switch()
        if self.config.update_icon_range:
            self.write_icon_range(icon_start, icon_end)
        self.write_icon_lib(icon_lib, mode=mode)
        time.sleep(self.config.switch_delay)
        self.start()

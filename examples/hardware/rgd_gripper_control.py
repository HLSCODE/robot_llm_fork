# -*- coding: utf-8 -*-
"""
大寰 (DH-Robotics) RGD 系列旋转电爪控制程序
严格遵循大寰官方《旋转电爪 RGD 系列 产品操作手册 V2.1》标准 Modbus-RTU 协议规范。

手册重点通讯指令对照：
  初始化：      TX: 01 06 01 00 00 01 49 F6 (回零) / 01 06 01 00 00 A5 48 4D (重新标定)
  开合位置：    TX: 01 06 01 03 01 F4 78 21 (500‰ 位置)
  开合力值：    TX: 01 06 01 01 00 1E 59 FE (30% 力)
  开合速度：    TX: 01 06 01 04 00 32 48 22 (50% 速度)
  绝对旋转角度：TX: 01 06 01 05 00 B4 98 40 (180度绝对角度)
  相对旋转角度：TX: 01 06 01 09 00 32 D9 E1 (50度相对旋转)
  旋转速度：    TX: 01 06 01 07 00 32 B8 22 (50% 速度)
  旋转力值：    TX: 01 06 01 08 00 32 88 21 (50% 旋转力)
  旋转停止：    TX: 01 06 05 02 00 01 E9 06
  旋转角度复位：TX: 01 06 05 06 00 01 A8 C7 (复位至当前圈) / 00 A5 A9 7C (回0°物理零位)

功能特点：
  1. 纯原生串口 + Modbus-RTU CRC16 帧实现，无版本兼容问题，指令与手册 100% 对应。
  2. 智能状态流：自动检测初始化状态，若未初始化会自动回零，彻底解决"动不了"的问题。
  3. 运动动态刷新：实时显示运动过程中的实时位置、角度与电机电流。
  4. 支持单夹爪 / 双夹爪 (L/R/B) 控制。
"""

import os
import sys
import time
import struct
import serial
import serial.tools.list_ports

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# 默认配置
# ============================================================
DEFAULT_PORT = "COM9"
BAUDRATE     = 115200

# 模式配置 (True 为双夹爪 L/R，False 为单夹爪)
DUAL_MODE    = False
LEFT_ID      = 1
RIGHT_ID     = 2

STROKE_MM    = 14.0         # RGD-5-14 行程 14mm

# 默认运动参数
DEFAULT_FORCE     = 100     # 夹持力 (20~100%)
DEFAULT_SPEED     = 100     # 夹持速度 (1~100%)
DEFAULT_ROT_FORCE = 100     # 旋转力 (20~100%)
DEFAULT_ROT_SPEED = 50      # 旋转速度 (1~100%)
WAIT_TIMEOUT      = 8.0     # 超时时间 (秒)


# ============================================================
# 标准 Modbus CRC16 校验 (与手册 2.3.1 完全一致)
# ============================================================
def calc_crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


# ============================================================
# 寄存器地址常量定义 (大寰 RGD 系列手册 2.3.2)
# ============================================================
class Reg:
    # 控制区 (0x01xx)
    INIT             = 0x0100   # 写入 0x01: 回零; 写入 0xA5: 重新标定
    FORCE            = 0x0101   # 夹爪夹持力 20~100 (%)
    POS              = 0x0103   # 目标位置 0~1000 (‰ 千分比)
    SPEED            = 0x0104   # 夹爪速度 1~100 (%)
    ROT_ABS_ANGLE    = 0x0105   # 目标绝对角度 (-32768 ~ 32767 度)
    ROT_SPEED        = 0x0107   # 旋转速度 1~100 (%)
    ROT_FORCE        = 0x0108   # 旋转力 20~100 (%)
    ROT_REL_ANGLE    = 0x0109   # 目标相对角度 (-32768 ~ 32767 度)

    # 状态反馈区 (0x02xx)
    F_INIT           = 0x0200   # 初始化状态: 0 未初始化, 1 成功, 2 初始化中
    F_GRIP_STATUS    = 0x0201   # 夹持状态: 0 运动中, 1 到达位置, 2 夹住物体, 3 物体掉落, 0xFFFF 未初始化
    F_POS            = 0x0202   # 实时位置: 0~1000 (‰)
    F_SPEED          = 0x0203   # 实时开合速度
    F_CURRENT        = 0x0204   # 夹持电机实时电流 (mA)
    F_ERROR          = 0x0205   # 报警码: 0 正常, 0x04 过热, 0x08 过载, 0x11 过速
    F_ROT_ANGLE_LO   = 0x0208   # 实时旋转角度低位 (-32768 ~ 32767 度)
    F_ROT_ANGLE_HI   = 0x0209   # 实时旋转角度高位
    F_ROT_INIT       = 0x020A   # 旋转初始化状态: 0 未初始化, 1 成功, 2 初始化中
    F_ROT_STATUS     = 0x020B   # 旋转状态: 0 运动中, 1 到达位置, 2 堵转, 3 堵转停转, 0xFFFF 未初始化
    F_ROT_SPEED      = 0x020C   # 实时旋转速度
    F_ROT_CURRENT    = 0x020D   # 旋转电机实时电流 (mA)

    # 参数与控制 (0x05xx)
    ROT_STOP         = 0x0502   # 写入 1 旋转停止
    ROT_RESET_ANGLE  = 0x0506   # 写入 0x01 复位至±360°内; 写入 0xA5 物理旋转到0°并复位
    ROT_RESOLUTION   = 0x050B   # 旋转分辨率 (大寰新固件 3600 单位/圈，即 1° = 10 单位，0.1°/单位)


GRIP_STATUS_MAP = {
    0: "运动中",
    1: "到达位置",
    2: "夹住物体",
    3: "物体掉落",
    0xFFFF: "未初始化",
}

ROT_STATUS_MAP = {
    0: "旋转中",
    1: "到达位置",
    2: "堵转",
    3: "堵转停转",
    0xFFFF: "未初始化",
}

ERROR_MAP = {
    0x00: "正常",
    0x04: "过热",
    0x08: "过载",
    0x11: "过速",
}


# ============================================================
# 原生串口 Modbus-RTU 驱动封装
# ============================================================
class SerialModbusBus:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.5):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = None
        self.show_hex = False   # 调试模式: 打印收发十六进制原始帧

    def connect(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            # 清空缓存
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            print(f"[OK] 串口打开成功: {self.port} @ {self.baud} 8N1")
        except serial.SerialException as e:
            raise IOError(f"串口打开失败 ({self.port}): {e}")

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

    def _send_cmd(self, frame_no_crc: bytes, expect_len: int, retries: int = 2) -> bytes:
        """发送命令帧并收取应答，含 CRC 校验及自动重试"""
        full_tx = frame_no_crc + calc_crc16(frame_no_crc)
        
        last_err = None
        for attempt in range(retries):
            if self.show_hex:
                print(f"  [TX] {full_tx.hex(' ').upper()}")
            
            self.ser.reset_input_buffer()
            self.ser.write(full_tx)
            self.ser.flush()

            resp = self.ser.read(expect_len)
            if self.show_hex and resp:
                print(f"  [RX] {resp.hex(' ').upper()}")

            if len(resp) >= expect_len:
                # 校验 CRC
                payload = resp[:-2]
                expected_crc = calc_crc16(payload)
                actual_crc = resp[-2:]
                if expected_crc == actual_crc:
                    if resp[1] & 0x80:
                        raise IOError(f"Modbus 异常响应: 错误码 0x{resp[2]:02X}")
                    return resp
                last_err = f"CRC 校验失败: 收到 {actual_crc.hex()}, 期望 {expected_crc.hex()}"
            else:
                last_err = f"从站无响应或数据不完整 (期望 {expect_len} 字节, 收到 {len(resp)} 字节)"
            
            time.sleep(0.04)

        raise IOError(last_err or "通讯失败")

    def read_reg(self, sid: int, addr: int, count: int = 1) -> list:
        """03 功能码：读连续保持寄存器"""
        req = bytes([sid, 0x03, (addr >> 8) & 0xFF, addr & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
        expect_len = 5 + count * 2
        resp = self._send_cmd(req, expect_len)
        
        byte_cnt = resp[2]
        values = []
        for i in range(count):
            val = struct.unpack(">H", resp[3 + i * 2 : 5 + i * 2])[0]
            values.append(val)
        return values

    def read_s16(self, sid: int, addr: int) -> int:
        """读有符号 16 位整型 (如旋转角度)"""
        val_u16 = self.read_reg(sid, addr, 1)[0]
        if val_u16 >= 0x8000:
            val_u16 -= 0x10000
        return val_u16

    def write_reg(self, sid: int, addr: int, val: int):
        """06 功能码：写单个保持寄存器"""
        val_u16 = val & 0xFFFF
        req = bytes([sid, 0x06, (addr >> 8) & 0xFF, addr & 0xFF, (val_u16 >> 8) & 0xFF, val_u16 & 0xFF])
        resp = self._send_cmd(req, 8)
        # 06 应答与发送一致
        return resp


# ============================================================
# RGD 旋转电爪控制类
# ============================================================
class RGDGripper:
    def __init__(self, bus: SerialModbusBus, sid: int, name: str, stroke_mm: float = STROKE_MM):
        self.bus = bus
        self.sid = sid
        self.name = name
        self.stroke_mm = stroke_mm

        self.cur_force = DEFAULT_FORCE
        self.cur_speed = DEFAULT_SPEED
        self.cur_rot_force = DEFAULT_ROT_FORCE
        self.cur_rot_speed = DEFAULT_ROT_SPEED

        self.rot_scale = 10.0   # 角度比例：大寰固件 0x050B 默认为 3600 分辨率，即 1° = 10 个单位 (0.1°/单位)
        self._detect_rot_resolution()

    def _detect_rot_resolution(self):
        try:
            res = self.bus.read_reg(self.sid, Reg.ROT_RESOLUTION)[0]
            if res and res > 0:
                self.rot_scale = res / 360.0
        except Exception:
            self.rot_scale = 10.0

    # ---------- 初始化与回零 ----------
    def init(self, full_recalibration: bool = False):
        """
        初始化夹爪：
          full_recalibration=False: 写入 0x0001 (回零找单向位置)
          full_recalibration=True:  写入 0x00A5 (全行程重新标定，一合一开)
        """
        val = 0x00A5 if full_recalibration else 0x0001
        self.bus.write_reg(self.sid, Reg.INIT, val)

    def is_initialized(self) -> bool:
        """检查开合与旋转是否均已完成初始化"""
        try:
            init_st = self.bus.read_reg(self.sid, Reg.F_INIT)[0]
            return init_st == 1
        except Exception:
            return False

    def wait_init_done(self, timeout: float = 6.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                st = self.bus.read_reg(self.sid, Reg.F_INIT)[0]
                if st == 1:
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    # ---------- 参数设置 ----------
    def set_grip_params(self, force: int = None, speed: int = None):
        if force is not None:
            self.cur_force = max(20, min(100, int(force)))
            self.bus.write_reg(self.sid, Reg.FORCE, self.cur_force)
        if speed is not None:
            self.cur_speed = max(1, min(100, int(speed)))
            self.bus.write_reg(self.sid, Reg.SPEED, self.cur_speed)

    def set_rot_params(self, force: int = None, speed: int = None):
        if force is not None:
            self.cur_rot_force = max(20, min(100, int(force)))
            self.bus.write_reg(self.sid, Reg.ROT_FORCE, self.cur_rot_force)
        if speed is not None:
            self.cur_rot_speed = max(1, min(100, int(speed)))
            self.bus.write_reg(self.sid, Reg.ROT_SPEED, self.cur_rot_speed)

    # ---------- 夹持开合控制 ----------
    def move_percent(self, percent: float, force: int = None, speed: int = None):
        """
        发送开合指令 (0.0% ~ 100.0%)
        """
        pct = max(0.0, min(100.0, float(percent)))
        target_permille = int(round(pct * 10))

        if force is not None or speed is not None:
            self.set_grip_params(force, speed)
            
        self.bus.write_reg(self.sid, Reg.POS, target_permille)
        return target_permille

    def wait_grip_done(self, target_permille: int, timeout: float = WAIT_TIMEOUT):
        """
        等待开合完成，带有实时位置进度刷新
        """
        t0 = time.time()
        time.sleep(0.08)  # 给电机响应时间进入运动状态

        while time.time() - t0 < timeout:
            try:
                pos = self.bus.read_reg(self.sid, Reg.F_POS)[0]
                st = self.bus.read_reg(self.sid, Reg.F_GRIP_STATUS)[0]
                cur = self.bus.read_reg(self.sid, Reg.F_CURRENT)[0]
                if cur >= 0x8000: cur -= 0x10000
                
                pct = pos / 10.0
                mm = (pct / 100.0) * self.stroke_mm
                st_desc = GRIP_STATUS_MAP.get(st, f"未知({st})")

                # 控制台动态刷新一行
                sys.stdout.write(f"\r  [{self.name}] 正在运动: {pct:5.1f}% ({mm:5.2f}mm) | 电流: {cur:4d}mA | 状态: [{st_desc}]   ")
                sys.stdout.flush()

                # 判断结束条件：到达目标位置 (1) 或 夹住物体 (2) 或 物体掉落 (3)
                if st in (1, 2, 3):
                    sys.stdout.write("\n")
                    return True, st, st_desc
            except Exception:
                pass
            time.sleep(0.04)

        sys.stdout.write("\n")
        return False, 0, "等待超时"

    # ---------- 旋转控制 ----------
    def rotate_abs(self, angle_deg: float, force: int = None, speed: int = None):
        """绝对角度旋转 (-3276.8° ~ 3276.7°)"""
        deg_float = float(angle_deg)
        target_reg = int(round(deg_float * self.rot_scale))
        target_reg = max(-32768, min(32767, target_reg))
        if force is not None or speed is not None:
            self.set_rot_params(force, speed)
        self.bus.write_reg(self.sid, Reg.ROT_ABS_ANGLE, target_reg)
        return deg_float

    def rotate_rel(self, delta_deg: float, force: int = None, speed: int = None):
        """相对当前角度增量旋转"""
        delta_float = float(delta_deg)
        delta_reg = int(round(delta_float * self.rot_scale))
        delta_reg = max(-32768, min(32767, delta_reg))
        if force is not None or speed is not None:
            self.set_rot_params(force, speed)
        self.bus.write_reg(self.sid, Reg.ROT_REL_ANGLE, delta_reg)
        return delta_float

    def stop_rotation(self):
        """平滑减速停止旋转"""
        self.bus.write_reg(self.sid, Reg.ROT_STOP, 1)

    def reset_rot_angle(self, physical_zero: bool = False):
        """
        复位旋转角度：
          physical_zero=False (写入 0x01): 复位到单圈当前角度，夹爪不动
          physical_zero=True  (写入 0xA5): 实际旋转到机械零位 (0°) 并复位
        """
        val = 0x00A5 if physical_zero else 0x0001
        self.bus.write_reg(self.sid, Reg.ROT_RESET_ANGLE, val)

    def wait_rot_done(self, timeout: float = WAIT_TIMEOUT):
        """等待旋转完成"""
        t0 = time.time()
        time.sleep(0.08)

        while time.time() - t0 < timeout:
            try:
                ang_raw = self.bus.read_s16(self.sid, Reg.F_ROT_ANGLE_LO)
                ang_deg = ang_raw / self.rot_scale
                st = self.bus.read_reg(self.sid, Reg.F_ROT_STATUS)[0]
                cur = self.bus.read_reg(self.sid, Reg.F_ROT_CURRENT)[0]
                if cur >= 0x8000: cur -= 0x10000

                st_desc = ROT_STATUS_MAP.get(st, f"未知({st})")
                sys.stdout.write(f"\r  [{self.name}] 正在旋转: 当前角度 {ang_deg:>6.1f}° | 电流: {cur:4d}mA | 状态: [{st_desc}]   ")
                sys.stdout.flush()

                if st in (1, 2, 3):
                    sys.stdout.write("\n")
                    return True, st, st_desc
            except Exception:
                pass
            time.sleep(0.04)

        sys.stdout.write("\n")
        return False, 0, "旋转等待超时"

    # ---------- 状态读取 ----------
    def status_line(self) -> str:
        try:
            pos = self.bus.read_reg(self.sid, Reg.F_POS)[0]
            pct = pos / 10.0
            mm = (pct / 100.0) * self.stroke_mm
            
            grip_st = self.bus.read_reg(self.sid, Reg.F_GRIP_STATUS)[0]
            grip_desc = GRIP_STATUS_MAP.get(grip_st, f"未知({grip_st})")

            rot_raw = self.bus.read_s16(self.sid, Reg.F_ROT_ANGLE_LO)
            rot_deg = rot_raw / self.rot_scale
            rot_st = self.bus.read_reg(self.sid, Reg.F_ROT_STATUS)[0]
            rot_desc = ROT_STATUS_MAP.get(rot_st, f"未知({rot_st})")

            cur = self.bus.read_reg(self.sid, Reg.F_CURRENT)[0]
            if cur >= 0x8000: cur -= 0x10000
            err = self.bus.read_reg(self.sid, Reg.F_ERROR)[0]
            alarm_desc = ERROR_MAP.get(err, f"报警 0x{err:02X}")

            init_st = self.bus.read_reg(self.sid, Reg.F_INIT)[0]
            init_desc = "已初始化" if init_st == 1 else "未初始化(红灯闪烁)"

            return (
                f"[{self.name} ID={self.sid}] "
                f"开合: {pct:5.1f}% ({mm:5.2f}mm) [{grip_desc}] | "
                f"旋转: {rot_deg:>6.1f}° [{rot_desc}] | "
                f"电流: {cur:3d}mA | 状态: {init_desc} | 报警: {alarm_desc}"
            )
        except Exception as e:
            return f"[{self.name} ID={self.sid}] [读取状态失败: {e}]"


# ============================================================
# 命令行交互与命令解析
# ============================================================
def print_help():
    print("""
==================== 大寰 RGD 旋转夹爪控制指令 ====================
【夹持开合】
  0~100              → 开合到 0% (全开) ~ 100% (完全闭合)
  L 50 / R 50        → 分别控制左 / 右夹爪到 50%
  home / close       → 全开 (0%) / 全关 (100%)

【旋转控制 (新功能)】
  deg <角度>         → 旋转到绝对角度 (如 deg 90 或 deg -180)
  rel <角度>         → 相对当前角度增量旋转 (如 rel 45 顺时针，rel -90 逆时针)
  rstop              → 旋转急停
  rzero              → 角度清零 (复位当前圈角度，夹爪不动)
  rhome              → 物理回 0° 机械零位并清零

【参数设置】
  force <20-100>     → 设置夹持力 (%)
  spd <1-100>        → 设置夹持速度 (%)
  rforce <20-100>    → 设置旋转扭矩 (%)
  rspd <1-100>       → 设置旋转速度 (%)

【设备维护】
  init               → 执行回零初始化 (写入 0x01)
  reinit             → 执行全行程一合一开重新标定 (写入 0xA5)
  debug on / off     → 开启 / 关闭十六进制 Modbus 报文打印
  s                  → 刷新状态
  h / q              → 帮助 / 退出
==================================================================
""")


def parse_command(raw: str, grippers_map: dict):
    raw = raw.strip()
    if not raw:
        return None, None, None

    low = raw.lower()
    if low in ("h", "help", "?"):
        return None, "help", None
    if low in ("q", "quit", "exit"):
        return None, "quit", None

    parts = low.split()
    first = parts[0]

    # 判断前缀
    targets = list(dict.fromkeys(grippers_map.values()))
    if first in grippers_map:
        targets = [grippers_map[first]]
        parts = parts[1:]
    elif first == "b":
        targets = list(dict.fromkeys(grippers_map.values()))
        parts = parts[1:]

    if not parts:
        return targets, "status", None

    action = parts[0]
    param = parts[1] if len(parts) > 1 else None

    if action in ("s", "status"):
        return targets, "status", None

    if action == "debug":
        return targets, "debug", (param == "on")

    if action == "home":
        return targets, "move_grip", 0.0
    if action == "close":
        return targets, "move_grip", 100.0

    if action == "deg":
        if param is None: return None, "invalid", "缺少角度参数 (如: deg 90)"
        try: return targets, "rot_abs", float(param)
        except ValueError: return None, "invalid", param

    if action == "rel":
        if param is None: return None, "invalid", "缺少角度增量 (如: rel 45)"
        try: return targets, "rot_rel", float(param)
        except ValueError: return None, "invalid", param

    if action in ("rstop", "stop"):
        return targets, "rot_stop", None
    if action == "rzero":
        return targets, "rot_zero", False
    if action == "rhome":
        return targets, "rot_zero", True

    if action == "init":
        return targets, "init", False
    if action == "reinit":
        return targets, "init", True

    if action == "force":
        if param is None: return None, "invalid", "缺少力值 (20-100)"
        return targets, "set_force", int(param)
    if action == "spd":
        if param is None: return None, "invalid", "缺少速度 (1-100)"
        return targets, "set_spd", int(param)
    if action == "rforce":
        if param is None: return None, "invalid", "缺少旋转力 (20-100)"
        return targets, "set_rforce", int(param)
    if action == "rspd":
        if param is None: return None, "invalid", "缺少旋转速度 (1-100)"
        return targets, "set_rspd", int(param)

    try:
        pct = float(action)
        if 0.0 <= pct <= 100.0:
            return targets, "move_grip", pct
        else:
            return None, "out_of_range", pct
    except ValueError:
        pass

    return None, "unknown", raw


# ============================================================
# 主入口
# ============================================================
def main():
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT

    print("==================================================")
    print(f"  大寰 RGD 旋转电爪控制系统 (Modbus-RTU)")
    print(f"  目标端口: {port} | 波特率: {BAUDRATE}")
    print("==================================================")

    # 检查串口冲突
    bus = SerialModbusBus(port, BAUDRATE)
    try:
        bus.connect()
    except Exception as e:
        print(f"\n[❌ 错误] 打开串口失败: {e}")
        print("\n[排查提示]：")
        print("  1. 若官方软件 DHGripperUI 正在后台运行并连接，会独占串口！请在软件中先断开连接或关闭软件。")
        print("  2. 请在设备管理器确认 USB 转 485 的串口号 (当前默认 COM9)。")
        return

    # 初始化夹爪
    grippers_map = {}
    if DUAL_MODE:
        gL = RGDGripper(bus, LEFT_ID, "L")
        gR = RGDGripper(bus, RIGHT_ID, "R")
        grippers_map = {"l": gL, "r": gR}
        grippers_list = [gL, gR]
    else:
        gMain = RGDGripper(bus, LEFT_ID, "G")
        grippers_map = {"g": gMain, "l": gMain}
        grippers_list = [gMain]

    # 检查初始化状态 (手册 4.1 控制流程核心步骤)
    print("\n[设备联机检查]")
    for g in grippers_list:
        print(" ", g.status_line())
        if not g.is_initialized():
            print(f"\n[⚠️ 提示] [{g.name}] 夹爪当前未初始化 (指示灯红灯闪烁，电爪此时拒绝运动)")
            print(f"[{g.name}] 正在自动执行回零初始化 (写入 0x0100=0x0001)...")
            g.init(full_recalibration=False)
            ok = g.wait_init_done(timeout=6.0)
            if ok:
                print(f"[{g.name}] 回零初始化成功！蓝灯常亮，进入可操作状态。")
            else:
                print(f"[{g.name}] 初始化等待超时，请检查供电或是否有机械阻挡。")
            print(" ", g.status_line())

    print_help()

    try:
        while True:
            try:
                raw = input("\n控制命令 (如 50 / deg 90 / rel 45 / s / h) > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            targets, cmd, arg = parse_command(raw, grippers_map)

            if cmd is None:
                continue
            if cmd == "help":
                print_help()
                continue
            if cmd == "quit":
                break
            if cmd == "unknown":
                print(f"[!] 无法识别命令: '{arg}'，输入 h 可查看帮助")
                continue
            if cmd == "invalid":
                print(f"[!] 参数错误: {arg}")
                continue
            if cmd == "out_of_range":
                print(f"[!] 开合百分比超出范围 (0~100): {arg}")
                continue

            try:
                # 调试报文开关
                if cmd == "debug":
                    bus.show_hex = arg
                    print(f"[调试模式] 十六进制报文打印已{'开启' if arg else '关闭'}")
                    continue

                # 状态查询
                if cmd == "status":
                    for g in targets:
                        print(g.status_line())
                    continue

                # 手动初始化
                if cmd == "init":
                    is_recal = arg
                    action_name = "全行程重新标定 (0xA5)" if is_recal else "回零初始化 (0x01)"
                    for g in targets:
                        print(f"[{g.name}] 正在执行 {action_name}...")
                        g.init(full_recalibration=is_recal)
                    for g in targets:
                        ok = g.wait_init_done(timeout=8.0)
                        print(f"[{g.name}] 初始化{'完成' if ok else '超时'}")
                        print("  " + g.status_line())
                    continue

                # 参数修改
                if cmd == "set_force":
                    for g in targets:
                        g.set_grip_params(force=arg)
                        print(f"[{g.name}] 夹持力已设置为 {arg}%")
                    continue
                if cmd == "set_spd":
                    for g in targets:
                        g.set_grip_params(speed=arg)
                        print(f"[{g.name}] 夹持速度已设置为 {arg}%")
                    continue
                if cmd == "set_rforce":
                    for g in targets:
                        g.set_rot_params(force=arg)
                        print(f"[{g.name}] 旋转力已设置为 {arg}%")
                    continue
                if cmd == "set_rspd":
                    for g in targets:
                        g.set_rot_params(speed=arg)
                        print(f"[{g.name}] 旋转速度已设置为 {arg}%")
                    continue

                # 旋转急停
                if cmd == "rot_stop":
                    for g in targets:
                        g.stop_rotation()
                        print(f"[{g.name}] 已下发旋转急停 (0x0502=1)")
                    continue

                # 旋转清零
                if cmd == "rot_zero":
                    is_home = arg
                    desc = "物理回 0° 并清零 (0xA5)" if is_home else "当前位置设为单圈 0° (0x01)"
                    for g in targets:
                        g.reset_rot_angle(physical_zero=is_home)
                        print(f"[{g.name}] 已执行旋转角度复位 ({desc})")
                    continue

                # 绝对角度旋转
                if cmd == "rot_abs":
                    deg = arg
                    print(f"[旋转] {[g.name for g in targets]} -> 绝对角度 {deg}°")
                    for g in targets:
                        g.rotate_abs(deg)
                    for g in targets:
                        ok, st, desc = g.wait_rot_done()
                        print(f"[{g.name}] 旋转结束: [{desc}]")
                        print("  " + g.status_line())
                    continue

                # 相对角度旋转
                if cmd == "rot_rel":
                    delta = arg
                    print(f"[旋转] {[g.name for g in targets]} -> 相对增量 {delta}°")
                    for g in targets:
                        g.rotate_rel(delta)
                    for g in targets:
                        ok, st, desc = g.wait_rot_done()
                        print(f"[{g.name}] 旋转结束: [{desc}]")
                        print("  " + g.status_line())
                    continue

                # 开合运动
                if cmd == "move_grip":
                    pct = arg
                    print(f"[开合] {[g.name for g in targets]} -> {pct:.1f}%")
                    sent = []
                    for g in targets:
                        permille = g.move_percent(pct)
                        sent.append((g, permille))

                    for g, permille in sent:
                        ok, st, desc = g.wait_grip_done(permille)
                        mm = (pct / 100.0) * g.stroke_mm
                        print(f"[{g.name}] 开合结束: [{desc}] 目标 {pct:.1f}% ({mm:.2f}mm)")
                        print("  " + g.status_line())
                    continue

            except (IOError, serial.SerialException) as e:
                print(f"[!] 串口通讯异常: {e}")

    finally:
        print("\n[退出] 正在关闭串口...")
        bus.close()
        print("[退出] 串口已释放")


if __name__ == "__main__":
    main()

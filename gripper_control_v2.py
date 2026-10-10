# -*- coding: utf-8 -*-
"""
命令：
    0~100 数字   → 运动到对应开合度（0=张开 100=闭合，同 CRT）
    s            → 显示当前状态
    c            → 重新从设备读一次行程映射参数
    calmax       → 把"当前位置反馈值"记为全闭位置（先在 CRT 里
                   或用足够大的百分比走到全闭，再执行本命令）
    calmin       → 把"当前位置反馈值"记为全开位置
    h            → 帮助
    q            → 退出
"""

import json
import os
import sys
import time

from pymodbus.client import ModbusSerialClient

# ============================================================
# 串口配置
# ============================================================
PORT      = "COM9"
BAUDRATE  = 115200
SLAVE_ID  = 1

# 夹爪型号行程(mm)，仅用于把位置值换算成 mm 显示
STROKE_MM = 40

UNITS_PER_MM = 100          # 1mm = 100 单位（0.01mm/单位，官方 SDK 确认）

# 兜底默认值：设备参数读不到时使用
FALLBACK_POS_MIN = 0
FALLBACK_POS_MAX = STROKE_MM * UNITS_PER_MM

# ------------------------------------------------------------
# 设备参数区地址（反汇编 CRT Controller 1.4.6.3 确认，并可用 probe_params.py 验证）
#   0x0404-0x0405 : Stroke Mapping Min（32位，行程% 的 0% 参考位置）
#   0x0406-0x0407 : Stroke Mapping Max（32位，行程% 的 100% 参考位置 = 全闭）
#   0x0305 最大速度 / 0x0306 最大力矩 / 0x0307 最大加速度 / 0x0308 最大减速度
#   0x0402 回零
#   CRT Controller 的 100% 就对应 Stroke Mapping Max，因此用它换算即可复现
#   软件里"100% 完全闭合"的行为。
# ------------------------------------------------------------
PARAM_STROKE_MAP_MIN = 0x0404   # 32位：高字 0x0404，低字 0x0405
PARAM_STROKE_MAP_MAX = 0x0406   # 32位：高字 0x0406，低字 0x0407

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "gripper_config.json")

# 默认运动参数
SPEED        = 100
TORQUE       = 60
ACC          = 2000
DEC          = 2000
WAIT_TIMEOUT = 8.0


class R:
    ENABLE       = 0x0100
    POS_H        = 0x0102
    POS_L        = 0x0103
    SPEED        = 0x0104
    TORQUE       = 0x0105
    ACC          = 0x0106
    DEC          = 0x0107
    GO           = 0x0108

    F_TORQUE_ARRIVED = 0x0601
    F_POS_ARRIVED    = 0x0602
    F_READY          = 0x0604
    F_POS_H          = 0x0609
    F_POS_L          = 0x060A
    F_SPEED          = 0x060B
    F_CURRENT        = 0x060C
    F_ALARM          = 0x0612

ALARM_BITS = {
    0x01: "过温", 0x02: "堵转", 0x04: "超速",
    0x08: "初始化故障", 0x10: "超限位", 0x20: "夹取掉落",
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Gripper:
    def __init__(self, port, baud, sid):
        self.sid = sid
        self._port = port
        self._baud = baud
        self.client = ModbusSerialClient(
            port=port, baudrate=baud,
            bytesize=8, parity="N", stopbits=1, timeout=1.0)
        # 有效位置范围（0.01mm 单位）
        self.pos_min = FALLBACK_POS_MIN
        self.pos_max = FALLBACK_POS_MAX
        self.pos_source = "默认值"

    # ---------- 底层 ----------
    def _kw(self):
        return {"device_id": self.sid}

    def _wr(self, a, v):
        r = self.client.write_register(a, v, **self._kw())
        if r.isError():
            raise IOError(f"写 0x{a:04X}={v} 失败: {r}")

    def _wr32(self, ah, v):
        v &= 0xFFFFFFFF
        r = self.client.write_registers(
            ah, [(v >> 16) & 0xFFFF, v & 0xFFFF], **self._kw())
        if r.isError():
            raise IOError(f"写32位 0x{ah:04X}={v} 失败: {r}")

    def _rd(self, a, n=1):
        r = self.client.read_holding_registers(a, count=n, **self._kw())
        if r.isError():
            raise IOError(f"读 0x{a:04X} 失败: {r}")
        return list(r.registers)

    def _rd32(self, ah):
        hi, lo = self._rd(ah, 2)
        v = (hi << 16) | lo
        return v - 0x100000000 if v >= 0x80000000 else v

    # ---------- 连接 / 使能 ----------
    def connect(self):
        if not self.client.connect():
            raise IOError(f"串口打开失败: {self._port}@{self._baud}")
        print(f"[OK] 已连接 {self._port} @ {self._baud}, 从站ID={self.sid}")

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    def enable(self, on=True):
        self._wr(R.ENABLE, 1 if on else 0)
        print(f"[OK] 执行器{'使能' if on else '失能'}")

    # ---------- 行程范围标定 ----------
    def refresh_range(self):
        """优先读设备参数区（与 CRT Controller 同源），其次用本地标定文件。"""
        cfg = load_config()
        try:
            lo = self._rd32(PARAM_STROKE_MAP_MIN)
            hi = self._rd32(PARAM_STROKE_MAP_MAX)
            if hi > lo:
                self.pos_min, self.pos_max = lo, hi
                self.pos_source = "设备参数区 0x0404/0x0406"
                return
            print(f"[!] 设备行程映射参数异常: Min={lo}, Max={hi}，改用备用来源")
        except Exception as e:
            print(f"[!] 读设备行程映射参数失败: {e}")
        if "pos_min" in cfg and "pos_max" in cfg and cfg["pos_max"] > cfg["pos_min"]:
            self.pos_min, self.pos_max = cfg["pos_min"], cfg["pos_max"]
            self.pos_source = "本地标定(gripper_config.json)"
        else:
            self.pos_min, self.pos_max = FALLBACK_POS_MIN, FALLBACK_POS_MAX
            self.pos_source = "默认值(未标定)"

    def calibrate_here(self, which):
        v = self.pos()
        cfg = load_config()
        cfg[f"pos_{which}"] = v
        save_config(cfg)
        print(f"[OK] 已把当前位置 {v} ({v/UNITS_PER_MM:.2f}mm) 记为 pos_{which}，"
              f"保存在 {os.path.basename(CONFIG_FILE)}")
        self.refresh_range()

    # ---------- 状态读取 ----------
    def pos(self):
        return self._rd32(R.F_POS_H)

    def speed(self):
        return self._rd(R.F_SPEED)[0]

    def current(self):
        return self._rd(R.F_CURRENT)[0]

    def pos_arrived(self):
        return self._rd(R.F_POS_ARRIVED)[0] == 1

    def torque_arrived(self):
        return self._rd(R.F_TORQUE_ARRIVED)[0] == 1

    def alarms(self):
        v = self._rd(R.F_ALARM)[0]
        return [n for b, n in ALARM_BITS.items() if v & b]

    # ---------- 运动 ----------
    def move_percent(self, percent, speed=SPEED, torque=TORQUE, acc=ACC, dec=DEC):
        """percent: 0=全开 100=全闭（与 CRT Controller 一致）"""
        span = self.pos_max - self.pos_min
        target = int(round(self.pos_min + percent / 100.0 * span))
        self._wr32(R.POS_H, target)
        self._wr(R.SPEED, speed)
        self._wr(R.TORQUE, torque)
        self._wr(R.ACC, acc)
        self._wr(R.DEC, dec)
        self._wr(R.GO, 1)
        return target

    def wait_done(self, timeout=WAIT_TIMEOUT):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.pos_arrived() or self.torque_arrived():
                return True
            time.sleep(0.02)
        return False


def fmt_status(g):
    try:
        pos_units = g.pos()
        mm = pos_units / UNITS_PER_MM
        span = g.pos_max - g.pos_min
        percent = (pos_units - g.pos_min) / span * 100 if span else 0
        spd = g.speed()
        cur = g.current()
        al = g.alarms()
        pa = "Y" if g.pos_arrived() else "-"
        ta = "Y" if g.torque_arrived() else "-"
        al_str = ",".join(al) if al else "无"
        return (f"  位置 = {pos_units:>7d} ({mm:6.2f} mm, 开合 {percent:5.1f}%, "
                f"0%=开/100%=合)  速度={spd:>3d}  电流={cur:>4d}  "
                f"位置到达={pa}  力矩到达={ta}  报警={al_str}")
    except Exception as e:
        return f"  [读状态失败] {e}"


def print_help(g):
    print(f"""
=================== 使用说明 ==================
  输入 0 ~ 100 的数字   → 夹爪运动到对应开合度
                          0    = 完全张开
                          100  = 完全闭合（同 CRT Controller）
  s                     → 显示当前状态
  c                     → 重新读取行程范围（当前来源: {g.pos_source}）
                          范围 = {g.pos_min} ~ {g.pos_max} (0.01mm)
  calmax                → 当前位置记为"全闭"（先走到全闭再执行）
  calmin                → 当前位置记为"全开"
  h                     → 显示本帮助
  q / quit / exit       → 退出程序
================================================""")
    print(f"[配置] 行程 {STROKE_MM} mm, 1mm={UNITS_PER_MM} 单位, "
          f"范围来源: {g.pos_source}")
    print(f"[默认] 速度 {SPEED}%  力矩 {TORQUE}%  加速度 {ACC}  减速度 {DEC}\n")


def main():
    g = Gripper(PORT, BAUDRATE, SLAVE_ID)
    try:
        g.connect()
        g.enable(True)
        time.sleep(0.2)
        g.refresh_range()
    except Exception as e:
        print(f"[错误] {e}")
        sys.exit(1)

    print(f"[行程范围] {g.pos_min} ~ {g.pos_max} (0.01mm)，来源: {g.pos_source}")
    print("[当前状态]")
    print(fmt_status(g))
    print_help(g)

    try:
        while True:
            try:
                raw = input("\n输入开合度0-100(0=开 100=合) 或命令 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if raw == "":
                continue

            low = raw.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "h":
                print_help(g)
                continue
            if low == "s":
                print("[当前状态]")
                print(fmt_status(g))
                continue
            if low == "c":
                g.refresh_range()
                print(f"[OK] 行程范围 {g.pos_min} ~ {g.pos_max}，"
                      f"来源: {g.pos_source}")
                continue
            if low == "calmax":
                g.calibrate_here("max")
                continue
            if low == "calmin":
                g.calibrate_here("min")
                continue

            try:
                percent = float(raw)
            except ValueError:
                print(f"[!] 无法识别的输入: {raw!r}，请输入 0~100 或 h 查看帮助")
                continue
            if percent < 0 or percent > 100:
                print(f"[!] 开合度超出范围: {percent}，应在 0~100 之间")
                continue

            target = g.move_percent(percent)
            mm = target / UNITS_PER_MM
            print(f"[运动] 开合 {percent:.1f}%  →  位置 {target} ({mm:.2f} mm)")

            try:
                t0 = time.time()
                ok = g.wait_done()
                dt = time.time() - t0
                if ok:
                    if g.torque_arrived() and not g.pos_arrived():
                        print(f"[完成] 力矩到达（{dt:.2f}s）—— 可能夹到物体/受阻")
                    else:
                        print(f"[完成] 位置到达（{dt:.2f}s）")
                else:
                    print(f"[超时] {WAIT_TIMEOUT:.1f}s 内未到达目标位置")
                print(fmt_status(g))
            except Exception as e:
                print(f"[错误] 运动失败: {e}")

    finally:
        print("\n[退出] 正在失能并断开...")
        try:
            g.enable(False)
        except Exception:
            pass
        g.close()
        print("[退出] 已断开")


if __name__ == "__main__":
    main()

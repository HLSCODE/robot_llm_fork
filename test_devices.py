"""
加粉装置硬件联调脚本
===================
基于 device_control_sdk 控制同一条 RS-485 总线上的三个设备：
  1. 加粉夹爪   (Electric Gripper)
  2. 针升降电机 (Stepper Motor, M系列)
  3. 针旋转电机 (Stepper Motor, M系列)

使用方法:
  python test_devices.py -i            # 交互模式（推荐）
  python test_devices.py                # 自动化序列
  python test_devices.py --scan         # 扫描总线设备
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

# ============================================================
# 配置 —— 按实际硬件修改这里
# ============================================================

SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 115200

# 夹爪
GRIPPER_ADDRESS = 9

# 两个步进电机
LIFT_ADDRESS = 7      # 针升降
ROTATION_ADDRESS = 6  # 针旋转

# 运动参数
STEPS = 5000
SPEED_RPM = 60
ACCEL_MS = 200        # 加速度 ms，必须 > 0 电机才会动

# ============================================================
# SDK 引用
# ============================================================

from src.device_control_sdk import (
    SerialTransport,
    StepperBus,
    ElectricGripper,
)
from src.device_control_sdk.devices.stepper_motor import (
    MSeriesRegister,
    MotorStatus,
)
from src.device_control_sdk.devices.stepper_motor.registers import (
    register_to_speed,
    register_to_int16,
    registers_to_int32,
)
from src.device_control_sdk.devices.electric_gripper.registers import (
    GripperRegister,
    InitializationStatus,
    MotionStatus,
    EmergencyStopStatus,
    ExcitationState,
    SaveStatus,
)
from src.device_control_sdk.core.exceptions import TransportError


# ============================================================
# 辅助函数
# ============================================================

def heading(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def sub(title: str) -> None:
    print(f"  --- {title}")


def make_bus(port: str) -> tuple[SerialTransport, StepperBus]:
    """创建共享串口和总线对象。"""
    transport = SerialTransport(port, baudrate=BAUDRATE, timeout=0.5)
    bus = StepperBus(transport)
    return transport, bus


# ============================================================
# 夹爪诊断 — 直接读寄存器
# ============================================================

def gripper_dump(transport: SerialTransport, address: int) -> None:
    """读取夹爪的关键寄存器并打印诊断信息。"""
    from src.device_control_sdk.devices.electric_gripper.client import ElectricGripper

    g = ElectricGripper(transport, address=address)
    print(f"  夹爪 {address} 状态诊断:")

    # 读命令/状态寄存器 (0x0100~0x0109)
    cmd_regs = [0x0100, 0x0102, 0x0103, 0x0104, 0x0105, 0x0109]
    cmd_names = ["INITIALIZE", "EMERGENCY_STOP", "FORCE", "SPEED", "TARGET_POS", "GRIP_RELEASE"]
    for reg, name in zip(cmd_regs, cmd_names):
        try:
            val = g.read_register(reg)
            print(f"    {name}(0x{reg:04X}):    {val:#06x} ({val})")
        except Exception as e:
            print(f"    {name}(0x{reg:04X}):    读取失败 - {type(e).__name__}")

    # 读状态寄存器 (0x0200~0x0204)
    state_regs = [0x0200, 0x0202, 0x0204]
    state_names = ["INIT_STATUS", "MOTION_STATUS", "CURRENT_POS"]
    for reg, name in zip(state_regs, state_names):
        try:
            val = g.read_register(reg)
            print(f"    {name}(0x{reg:04X}):  {val:#06x} ({val})")
        except Exception as e:
            print(f"    {name}(0x{reg:04X}):   读取失败 - {type(e).__name__}")

    # 读配置寄存器 (0x0300~0x0304)
    cfg_regs = [0x0300, 0x0302, 0x0303, 0x0304]
    cfg_names = ["HOME_DIR", "SAVE", "DEVICE_ID", "EXCITATION"]
    for reg, name in zip(cfg_regs, cfg_names):
        try:
            val = g.read_register(reg)
            print(f"    {name}(0x{reg:04X}):     {val:#06x} ({val})")
        except Exception as e:
            print(f"    {name}(0x{reg:04X}):     读取失败 - {type(e).__name__}")


# ============================================================
# 电机诊断 — 直接读寄存器，不依赖任何猜测
# ============================================================

def motor_dump(bus: StepperBus, address: int, label: str = "") -> None:
    """读取电机的关键寄存器并打印诊断信息。"""
    tag = f"电机 {address}" + (f" ({label})" if label else "")
    m = bus.motor(address)

    try:
        # 连续读 7 个寄存器 (0x00 ~ 0x06)
        regs = m.read_registers(0x00, 7)
        status_raw = regs[0]
        position = registers_to_int32(regs[1], regs[2])
        speed_raw = register_to_int16(regs[3])
        speed_rpm = register_to_speed(regs[3], scale=10)
        emergency_stop = regs[4]
        current_ma = regs[5]
        enable = regs[6]

        status_name = MotorStatus(status_raw).name if status_raw <= 4 else f"未知({status_raw})"

        print(f"  {tag} 状态诊断:")
        print(f"    状态(0x00):       {status_raw:#06x} ({status_name})")
        print(f"    实际位置(0x01-2): {position} steps")
        print(f"    实际速度(0x03):   {speed_rpm:.1f} RPM  (原始值: {speed_raw})")
        print(f"    急停(0x04):       {emergency_stop:#06x}")
        print(f"    电流(0x05):       {current_ma} mA")
        print(f"    使能(0x06):       {enable:#06x}")
    except Exception as e:
        print(f"  {tag} 读取失败: {e}")


def motor_enable(bus: StepperBus, address: int) -> None:
    """使能电机 (写 ENABLE 寄存器 0x06 = 1)。"""
    m = bus.motor(address)
    m.write_register(MSeriesRegister.ENABLE, 0x0001)
    print(f"  电机 {address} 已使能")


def motor_disable(bus: StepperBus, address: int) -> None:
    """关闭电机使能。"""
    m = bus.motor(address)
    m.write_register(MSeriesRegister.ENABLE, 0x0000)
    print(f"  电机 {address} 已关闭使能")


def motor_stop(bus: StepperBus, address: int) -> None:
    """停止电机。"""
    bus.motor(address).stop()
    print(f"  电机 {address} 已停止")


def motor_move_to(
    bus: StepperBus,
    address: int,
    steps: int,
    *,
    rpm: float = SPEED_RPM,
    accel: int = ACCEL_MS,
) -> None:
    """向目标位置运动，并使用显式的加速度参数。"""
    m = bus.motor(address)
    m.move_to(steps, rpm=rpm, acceleration_ms=accel)
    print(f"  电机 {address}: 运动到 {steps} steps (rpm={rpm}, accel={accel}ms)")


def wait_idle(bus: StepperBus, address: int, timeout: float = 15.0) -> bool:
    """等待电机到达目标位置。"""
    deadline = time.monotonic() + timeout
    last_pos = None
    while time.monotonic() < deadline:
        m = bus.motor(address)
        status = m.read_status()
        pos = m.read_actual_position()
        if last_pos is not None and pos != last_pos:
            pass  # 还在动
        last_pos = pos
        if status == MotorStatus.IDLE_OR_ARRIVED:
            print(f"    电机 {address}: 到达 ✓ (位置: {pos} steps)")
            return True
        time.sleep(0.2)
    print(f"    电机 {address}: 超时 ✗ (状态: {status.name}, 位置: {pos} steps)")
    return False


# ============================================================
# 交互模式
# ============================================================

def run_interactive(port: str) -> None:
    """交互模式，逐条输入指令。"""
    transport, bus = make_bus(port)

    # 构造电机对象（在命令中按需使用）
    gripper = ElectricGripper(transport, address=GRIPPER_ADDRESS)
    lift_motor = bus.motor(LIFT_ADDRESS)
    rot_motor = bus.motor(ROTATION_ADDRESS)

    _last_cmd_time = 0.0

    def _cmd_gap():
        nonlocal _last_cmd_time
        now = time.monotonic()
        gap = now - _last_cmd_time
        if gap < 0.5:
            time.sleep(0.5 - gap)
        _last_cmd_time = time.monotonic()

    def _gripper_dump():
        gripper_dump(transport, GRIPPER_ADDRESS)

    def _full_dump():
        motor_dump(bus, ROTATION_ADDRESS, "旋转")
        motor_dump(bus, LIFT_ADDRESS, "升降")
        _gripper_dump()

    def _rot_motor(label: str, steps: int):
        _cmd_gap()
        motor_move_to(bus, ROTATION_ADDRESS, steps)
        print(f"  旋转: {label} {steps} 步")

    def _lift_motor(label: str, steps: int):
        _cmd_gap()
        motor_move_to(bus, LIFT_ADDRESS, steps)
        print(f"  升降: {label} {steps} 步")

    def _confirm(msg: str) -> bool:
        return input(f"  {msg} (y/n): ").strip().lower() == "y"

    def _scan_gripper(_bus: StepperBus) -> None:
        """扫描夹爪地址 (读 0x0204 寄存器)。"""
        from src.device_control_sdk.protocols.modbus_rtu import ModbusRTUProtocol
        from src.device_control_sdk.core.strategies import ModbusRTUStrategy

        proto = ModbusRTUProtocol()
        print("  扫描夹爪 (地址 1-32, 读 0x0204)...")
        found = []
        for addr in range(1, 33):
            request = proto.build_read_registers(addr, 0x0204, 1)
            try:
                response = transport.transact_with_strategy(ModbusRTUStrategy(request, proto.expected_read_response_size(1)))
                proto.parse_read_registers(response, addr, 1)
                found.append(addr)
                print(f"    ✓ 地址 {addr}")
            except:
                pass
        if found:
            print(f"  找到夹爪地址: {found}")
        else:
            print("  未找到夹爪。请检查: 1) 是否上电 2) 是否接在485总线上 3) 地址是否在1-32范围内")

    commands = {
        # ---- 夹爪 (此型号不支持 0x0109 寄存器，用 move_to 替代 grip/release) ----
        "grip":         lambda: (gripper.move_to(100), print("  夹爪: 夹取 (完全闭合)")),
        "release":      lambda: (gripper.move_to(0), print("  夹爪: 释放 (完全张开)")),
        "gripper_open": lambda: (gripper.move_to(80), print(f"  夹爪: 闭合到 80%")),
        "gripper_init": lambda: (gripper.initialize(), print("  夹爪: 初始化")),
        "gripper_pos":  lambda: print(f"  夹爪位置: {gripper.read_position()}%"),
        "gripper_dump": lambda: _gripper_dump(),
        "scan_gripper": lambda: _scan_gripper(bus),
        # ---- 旋转电机 ----
        "rot_enable":   lambda: motor_enable(bus, ROTATION_ADDRESS),
        "rot_disable":  lambda: motor_disable(bus, ROTATION_ADDRESS),
        "rot_dump":     lambda: motor_dump(bus, ROTATION_ADDRESS, "旋转"),
        "rot_cw":       lambda: _rot_motor("正转", STEPS),
        "rot_ccw":      lambda: _rot_motor("反转", -STEPS),
        "rot_pos":      lambda: print(f"  旋转位置: {rot_motor.read_actual_position()} steps"),
        "rot_status":   lambda: print(f"  旋转状态: {rot_motor.read_status().name}"),
        "rot_stop":     lambda: (motor_stop(bus, ROTATION_ADDRESS), _cmd_gap()),
        "rot_home":     lambda: (rot_motor.set_actual_position_zero(), print("  旋转: 当前位置设为零")),
        # ---- 升降电机 ----
        "lift_enable":  lambda: motor_enable(bus, LIFT_ADDRESS),
        "lift_disable": lambda: motor_disable(bus, LIFT_ADDRESS),
        "lift_dump":    lambda: motor_dump(bus, LIFT_ADDRESS, "升降"),
        "lift_up":      lambda: _lift_motor("上升", STEPS),
        "lift_down":    lambda: _lift_motor("下降", -STEPS),
        "lift_pos":     lambda: print(f"  升降位置: {lift_motor.read_actual_position()} steps"),
        "lift_status":  lambda: print(f"  升降状态: {lift_motor.read_status().name}"),
        "lift_stop":    lambda: (motor_stop(bus, LIFT_ADDRESS), _cmd_gap()),
        "lift_home":    lambda: (lift_motor.set_actual_position_zero(), print("  升降: 当前位置设为零")),
        # ---- 通用 ----
        "dump":
            lambda: _full_dump(),
        "enable":
            lambda: (
                motor_enable(bus, ROTATION_ADDRESS),
                motor_enable(bus, LIFT_ADDRESS),
            ),
        "disable":
            lambda: (
                motor_disable(bus, ROTATION_ADDRESS),
                motor_disable(bus, LIFT_ADDRESS),
            ),
        "help": lambda: _print_help(),
        "exit": lambda: (_ := "exit"),
    }

    def _print_help() -> None:
        print()
        print("  ── 诊断 ──")
        print("    dump           读取三个设备全部状态")
        print("    rot_dump       读取旋转电机状态")
        print("    lift_dump      读取升降电机状态")
        print("    gripper_dump   读取夹爪状态")
        print("  ── 使能 ──")
        print("    enable         使能两个电机")
        print("    disable        关闭两个电机使能")
        print("    rot_enable     使能旋转电机")
        print("    lift_enable    使能升降电机")
        print("  ── 运动 (旋转) ──")
        print("    rot_cw         正转")
        print("    rot_ccw        反转")
        print("    rot_stop       停止")
        print("    rot_home       当前位置设为零")
        print("    rot_pos        读取位置")
        print("    rot_status     读取状态")
        print("  ── 运动 (升降) ──")
        print("    lift_up        上升")
        print("    lift_down      下降")
        print("    lift_stop      停止")
        print("    lift_home      当前位置设为零")
        print("    lift_pos       读取位置")
        print("    lift_status    读取状态")
        print("  ── 夹爪 (注意: grip/release 实际为完全闭合/张开) ──")
        print("    gripper_init   初始化夹爪")
        print("    gripper_open   张开到 80%")
        print("    grip           夹取")
        print("    release        释放")
        print("    gripper_pos    读取位置")
        print("    scan_gripper   扫描夹爪地址")
        print("  ── 其他 ──")
        print("    help           显示帮助")
        print("    exit           退出")

    try:
        print("\n连接成功。先看一下所有设备当前状态：")
        motor_dump(bus, ROTATION_ADDRESS, "旋转")
        motor_dump(bus, LIFT_ADDRESS, "升降")
        _gripper_dump()
        print("\n提示: 电机先执行 enable 使能再发运动指令。输入 help 查看全部命令。")

        while True:
            try:
                cmd = input("\n> ").strip().lower()
                if cmd == "exit":
                    break
                if cmd in commands:
                    commands[cmd]()
                elif cmd:
                    print(f"  未知命令: {cmd}，输入 help 查看可用命令")
            except KeyboardInterrupt:
                print()
                break
            except Exception as e:
                print(f"  ✗ 错误: {e}")
    finally:
        transport.close()
        print("已断开连接。")


# ============================================================
# 自动化序列
# ============================================================

def run_sequence(port: str) -> None:
    """自动化测试序列。"""
    transport, bus = make_bus(port)
    gripper = ElectricGripper(transport, address=GRIPPER_ADDRESS)

    try:
        # 第一步：诊断
        heading("设备诊断")
        motor_dump(bus, ROTATION_ADDRESS, "旋转")
        motor_dump(bus, LIFT_ADDRESS, "升降")

        # 第二步：使能电机
        heading("使能电机")
        motor_enable(bus, ROTATION_ADDRESS)
        motor_enable(bus, LIFT_ADDRESS)
        time.sleep(0.5)

        # 第三步：旋转电机测试
        heading("旋转电机测试")
        motor_move_to(bus, ROTATION_ADDRESS, STEPS)
        wait_idle(bus, ROTATION_ADDRESS)
        motor_move_to(bus, ROTATION_ADDRESS, 0)
        wait_idle(bus, ROTATION_ADDRESS)

        # 第四步：升降电机测试
        heading("升降电机测试")
        motor_move_to(bus, LIFT_ADDRESS, STEPS)
        wait_idle(bus, LIFT_ADDRESS)
        motor_move_to(bus, LIFT_ADDRESS, 0)
        wait_idle(bus, LIFT_ADDRESS)

        heading("全部测试完成 ✓")

    except Exception as e:
        print(f"\n  ✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        transport.close()


# ============================================================
# 扫描
# ============================================================

def scan_bus(port: str) -> None:
    """扫描总线上的设备。"""
    from src.device_control_sdk.protocols.modbus_rtu import ModbusRTUProtocol

    baudrates = [115200]
    addresses = list(range(1, 33))
    proto = ModbusRTUProtocol()

    print(f"扫描 {port} @ {baudrates[0]} baud ...")
    print(f"地址范围: 1-32")

    # 先扫电机 (reg 0x00)，再专门扫夹爪 (reg 0x0204 当前位置)
    stepper_found = []
    gripper_found = []

    transport = SerialTransport(port, baudrate=baudrates[0], timeout=0.3)

    for addr in addresses:
        # 电机: 读 0x00
        request = proto.build_read_registers(addr, 0x00, 1)
        try:
            from src.device_control_sdk.core.strategies import ModbusRTUStrategy
            response = transport.transact_with_strategy(ModbusRTUStrategy(request, proto.expected_read_response_size(1)))
            proto.parse_read_registers(response, addr, 1)
            stepper_found.append(addr)
        except:
            pass

    for addr in addresses:
        # 夹爪: 读 0x0204 (CURRENT_POSITION_PERCENT) — 电机没有这个寄存器
        request = proto.build_read_registers(addr, 0x0204, 1)
        try:
            response = transport.transact_with_strategy(ModbusRTUStrategy(request, proto.expected_read_response_size(1)))
            proto.parse_read_registers(response, addr, 1)
            gripper_found.append(addr)
        except:
            pass

    transport.close()

    for addr in stepper_found:
        print(f"  ✓ 地址 {addr:3d}  步进电机 (STATUS 寄存器有响应)")
    for addr in gripper_found:
        print(f"  ✓ 地址 {addr:3d}  夹爪 (位置寄存器 0x0204 有响应)")

    if not stepper_found and not gripper_found:
        print("  (无设备响应)")
    print("扫描完成。")


# ============================================================
# 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="加粉装置硬件联调脚本")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式（推荐）")
    parser.add_argument("--scan", action="store_true", help="扫描总线设备")
    parser.add_argument("--port", default=SERIAL_PORT, help=f"串口 (默认: {SERIAL_PORT})")
    args = parser.parse_args()

    if args.scan:
        scan_bus(args.port)
    elif args.interactive:
        run_interactive(args.port)
    else:
        run_sequence(args.port)


if __name__ == "__main__":
    main()

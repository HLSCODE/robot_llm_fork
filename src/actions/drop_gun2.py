# -*- coding: utf-8 -*-
"""
退枪头2 - 将枪头退到枪头储存位2
轨迹: 1shang → 2shang → 2zhong → 2shang → 1shang
"""
import sys
import os
import time
from Robotic_Arm.rm_robot_interface import *

from ..arm_sdk.config import ROBOT2_CONFIG, GUN1_POSITIONS, GUN2_POSITIONS, MOVE_CONFIG
from ..devices import yiyeqiang_out


def drop_gun2(robot):
    """退枪头2动作"""
    print("=" * 40)
    print("退枪头2 (Gun2)")
    print("=" * 40)

    # pos_1shang = GUN1_POSITIONS["1shang"]
    # pos_2shang = GUN2_POSITIONS["2shang"]
    # pos_2zhong = GUN2_POSITIONS["2zhong"]
    pos_1shang= [-0.521046,-0.307949,-0.209037,-3.054,-0.002,2.992]
    pos_2shang = [-0.521046,-0.307949,-0.209037,-3.054,-0.002,2.992]
    pos_2zhong = [-0.521046,-0.307949,-0.269037,-3.054,-0.002,2.992]

    # 1. 移动到 1shang
    print(f"\n[1] 移动到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"移动到 1shang 失败，错误码: {ret}")
        return False
    print("已到达 1shang")

    # 2. movel 移动到 2shang
    print(f"\n[2] movel 移动到 2shang...")
    ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 2shang 失败，错误码: {ret}")
        return False
    print("已到达 2shang")

    # 3. movel 下降到 2zhong
    print(f"\n[3] movel 下降到 2zhong...")
    ret = robot.rm_movel(pos_2zhong, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 2zhong 失败，错误码: {ret}")
        return False
    print("已到达 2zhong")

    # 4. 退枪头（弹出枪头）
    print(f"\n[4] 退枪头（弹出枪头）...")
    result = yiyeqiang_out.eject_tip(port='/dev/hand')
    if result:
        print("退枪头成功!")
    else:
        print("退枪头失败!")
        return False

    time.sleep(1)

    # 5. movel 回到 2shang
    print(f"\n[5] movel 回到 2shang...")
    ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 2shang 失败，错误码: {ret}")
        return False

    # 6. movel 回到 1shang
    print(f"\n[6] movel 回到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 1shang 失败，错误码: {ret}")
        return False

    print("\n退枪头2完成!")
    return True

def main():
    print("=" * 50)
    print("退枪头2工具")
    print("=" * 50)

    print("\n正在连接机械臂...")
    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot_handle = robot.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"机械臂已连接: {ROBOT2_CONFIG['ip']}")

    success = drop_gun2(robot)

    if success:
        print("\n退枪头2成功!")
    else:
        print("\n退枪头2失败!")


if __name__ == "__main__":
    main()

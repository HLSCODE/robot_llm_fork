# -*- coding: utf-8 -*-
"""
退枪头1 - 将枪头退到枪头储存位1
轨迹: 1shang → 1zhong → 1shang
"""
import sys
import os
import time
from Robotic_Arm.rm_robot_interface import *

from ..arm_sdk.config import ROBOT2_CONFIG, GUN1_POSITIONS, MOVE_CONFIG
from ..devices import yiyeqiang_out


def drop_gun1(robot):
    """退枪头1动作"""
    print("=" * 40)
    print("退枪头1 (Gun1)")
    print("=" * 40)

    # pos_1shang = GUN1_POSITIONS["1shang"]
    # pos_1zhong = GUN1_POSITIONS["1zhong"]
    pos_1shang = [-0.521046,-0.307949,-0.269037,-3.054,-0.002,2.992]
    pos_1zhong = [-0.521046,-0.307949,-0.269037,-3.054,-0.002,2.992]
    print(pos_1shang,pos_1zhong)
    # 1. 移动到 1shang
    print(f"\n[1] 移动到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"移动到 1shang 失败，错误码: {ret}")
        return False
    print("已到达 1shang")

    # 2. movel 下降到 1zhong
    print(f"\n[2] movel 下降到 1zhong...")
    ret = robot.rm_movel(pos_1zhong, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 1zhong 失败，错误码: {ret}")
        return False
    print("已到达 1zhong")

    # 3. 退枪头（弹出枪头）
    print(f"\n[3] 退枪头（弹出枪头）...")
    result = yiyeqiang_out.eject_tip(port='/dev/hand')
    if result:
        print("退枪头成功!")
    else:
        print("退枪头失败!")
        return False

    time.sleep(0.5)

    # 4. movel 回到 1shang
    print(f"\n[4] movel 回到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 1shang 失败，错误码: {ret}")
        return False

    print("\n退枪头1完成!")
    return True


def main():
    print("=" * 50)
    print("退枪头1工具")
    print("=" * 50)

    print("\n正在连接机械臂...")
    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot_handle = robot.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"机械臂已连接: {ROBOT2_CONFIG['ip']}")

    success = drop_gun1(robot)

    if success:
        print("\n退枪头1成功!")
    else:
        print("\n退枪头1失败!")


if __name__ == "__main__":
    main()

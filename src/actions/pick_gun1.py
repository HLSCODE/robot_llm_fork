# -*- coding: utf-8 -*-
"""
取枪头1 - 从枪头储存位1取下枪头
轨迹: 1shang → movel → 1xia → movel → 1shang
"""
import sys
import os
from Robotic_Arm.rm_robot_interface import *
from ..arm_sdk.config import ROBOT2_CONFIG, GUN1_POSITIONS, MOVE_CONFIG


def pick_gun1(robot):
    """取枪头1动作"""
    print("=" * 40)
    print("取枪头1 (Gun1)")
    print("=" * 40)

    pos_1shang = GUN1_POSITIONS["1shang"]
    pos_1xia = GUN1_POSITIONS["1xia"]

    # 1. 移动到 1shang
    print(f"\n[1] 移动到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"移动到 1shang 失败，错误码: {ret}")
        return False
    print("已到达 1shang")

    # 2. movel 下降到 1xia
    print(f"\n[2] movel 下降到 1xia...")
    ret = robot.rm_movel(pos_1xia, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 1xia 失败，错误码: {ret}")
        return False
    print("已到达 1xia，枪头已取下")

    # 等待
    import time
    time.sleep(0.5)

    # 3. movel 回到 1shang
    print(f"\n[3] movel 回到 1shang...")
    ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 1shang 失败，错误码: {ret}")
        return False

    print("\n取枪头1完成!")
    return True


def main():
    print("=" * 50)
    print("取枪头1工具")
    print("=" * 50)

    print("\n正在连接机械臂...")
    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot_handle = robot.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"机械臂已连接: {ROBOT2_CONFIG['ip']}")

    success = pick_gun1(robot)

    if success:
        print("\n取枪头1成功!")
    else:
        print("\n取枪头1失败!")


if __name__ == "__main__":
    main()

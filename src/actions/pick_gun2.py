# -*- coding: utf-8 -*-
"""
取枪头2 - 从枪头储存位2取下枪头
轨迹: 2shang → movel → 2xia → movel → 2shang
"""
import sys
import os
from Robotic_Arm.rm_robot_interface import *
from ..arm_sdk.config import ROBOT2_CONFIG, GUN2_POSITIONS, MOVE_CONFIG


def pick_gun2(robot):
    """取枪头2动作"""
    print("=" * 40)
    print("取枪头2 (Gun2)")
    print("=" * 40)

    pos_2shang = GUN2_POSITIONS["2shang"]
    pos_2xia = GUN2_POSITIONS["2xia"]

    # 1. 移动到 2shang
    print(f"\n[1] 移动到 2shang...")
    ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"移动到 2shang 失败，错误码: {ret}")
        return False
    print("已到达 2shang")

    # 2. movel 下降到 2xia
    print(f"\n[2] movel 下降到 2xia...")
    ret = robot.rm_movel(pos_2xia, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 2xia 失败，错误码: {ret}")
        return False
    print("已到达 2xia，枪头已取下")

    # 等待
    import time
    time.sleep(0.5)

    # 3. movel 回到 2shang
    print(f"\n[3] movel 回到 2shang...")
    ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
    if ret != 0:
        print(f"movel 到 2shang 失败，错误码: {ret}")
        return False

    print("\n取枪头2完成!")
    return True


def main():
    print("=" * 50)
    print("取枪头2工具")
    print("=" * 50)

    print("\n正在连接机械臂...")
    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot_handle = robot.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"机械臂已连接: {ROBOT2_CONFIG['ip']}")

    success = pick_gun2(robot)

    if success:
        print("\n取枪头2成功!")
    else:
        print("\n取枪头2失败!")


if __name__ == "__main__":
    main()

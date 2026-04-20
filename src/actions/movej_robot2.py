# -*- coding: utf-8 -*-
"""
机械臂 movej 移动到指定点位
"""
import os
import time
from Robotic_Arm.rm_robot_interface import *

from ..arm_sdk.config import ROBOT2_CONFIG, MOVE_CONFIG


# 目标位姿 (单位：米，x, y, z, rx, ry, rz)
TARGET_POSE = [-0.038902,-0.394279,-0.134937,-2.765000,0.311000,-2.668000]


def main(TARGET_POSE=None):
    if TARGET_POSE is None:
        TARGET_POSE = [-0.038902,-0.394279,-0.134937,-2.765000,0.311000,-2.668000]

    print("=" * 50)
    print("Robot2 movej 移动测试")
    print("=" * 50)

    print("\n正在连接机械臂...")
    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot_handle = robot.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"机械臂已连接: {ROBOT2_CONFIG['ip']}")
    print(f"目标点位: {TARGET_POSE}")

    # movej 移动到目标点位
    print("\n正在 movej 移动到目标点位...")
    ret = robot.rm_movel(
        TARGET_POSE,
        v=MOVE_CONFIG["velocity"],
        r=MOVE_CONFIG["radius"],
        connect=MOVE_CONFIG["connect"],
        block=MOVE_CONFIG["block"]
    )

    if ret == 0:
        print("移动成功!")
    else:
        print(f"移动失败，错误码: {ret}")


if __name__ == "__main__":
    main()

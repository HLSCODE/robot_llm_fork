# -*- coding: utf-8 -*-
"""
Robot1 夹爪打开
"""
import time
from Robotic_Arm.rm_robot_interface import *

from ..arm_sdk.config import ROBOT1_CONFIG, GRIPPER_CONFIG


def gripper_open(robot):
    """夹爪打开（松开）"""
    print("夹爪正在打开...")
    ret = robot.rm_set_gripper_release(
        speed=GRIPPER_CONFIG['release']['speed'],
        block=True,
        timeout=GRIPPER_CONFIG['release']['timeout']
    )

    if ret == 0:
        print("夹爪打开成功!")
    else:
        print(f"夹爪打开失败，错误码: {ret}")
        return False
    return True


def main():
    print("=" * 50)
    print("Robot1 夹爪打开")
    print("=" * 50)

    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot.rm_create_robot_arm(ROBOT1_CONFIG["ip"], ROBOT1_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"已连接: {ROBOT1_CONFIG['ip']}")
    gripper_open(robot)


if __name__ == "__main__":
    main()

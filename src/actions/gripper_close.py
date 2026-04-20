# -*- coding: utf-8 -*-
"""
Robot1 夹爪关闭
"""
import time
from Robotic_Arm.rm_robot_interface import *
from ..arm_sdk.config import ROBOT1_CONFIG, GRIPPER_CONFIG


def gripper_close(robot):
    """夹爪关闭（夹取）"""
    print("夹爪正在关闭...")
    ret = robot.rm_set_gripper_pick(
        speed=GRIPPER_CONFIG['pick']['speed'],
        force=GRIPPER_CONFIG['pick']['force'],
        block=True,
        timeout=GRIPPER_CONFIG['pick']['timeout']
    )

    if ret == 0:
        print("夹爪关闭成功!")
    else:
        print(f"夹爪关闭失败，错误码: {ret}")
        return False
    return True


def main():
    print("=" * 50)
    print("Robot1 夹爪关闭")
    print("=" * 50)

    robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    robot.rm_create_robot_arm(ROBOT1_CONFIG["ip"], ROBOT1_CONFIG["port"])

    ret, state = robot.rm_get_current_arm_state()
    if ret != 0:
        print(f"机械臂连接失败，错误码: {ret}")
        return

    print(f"已连接: {ROBOT1_CONFIG['ip']}")
    gripper_close(robot)


if __name__ == "__main__":
    main()

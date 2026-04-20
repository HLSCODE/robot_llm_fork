# -*- coding: utf-8 -*-
"""
瓶子/白桌抓取流程（与 GUI 深度相机 D435i 配合：RGB + 对齐 depth + 彩色内参）。
路径已改为基于本文件，可在任意工程目录运行。
"""
import os
import sys
import time
import cv2
import numpy as np

from ..vision.interface import vertical_catch
from ..arm_sdk.controller import RobotController
from ..arm_sdk.config import *

PICTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision", "pictures")


def detect_target(image, yolo_model, sam_model, process_mask_fn, width=640, height=480, conf_thresh=0.7):
    """统一的视觉处理：YOLO -> SAM -> GMM，返回 mask、bbox、detected"""
    mask = np.zeros((height, width), dtype=np.uint8)
    detected = False
    bbox = None

    yolo_results = yolo_model(image, verbose=False)
    for result in yolo_results:
        for box in result.boxes:
            confidence = float(box.conf)
            if confidence < conf_thresh:
                continue

            detected = True
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            bbox = [int(x1), int(y1), int(x2), int(y2)]

            sam_results = sam_model(image, bboxes=[bbox])
            if sam_results and len(sam_results) > 0:
                sam_mask = sam_results[0].masks.data[0].cpu().numpy()
                sam_mask = (sam_mask * 255).astype(np.uint8)
                improved_mask = process_mask_fn(image, sam_mask)
                mask = cv2.bitwise_or(mask, improved_mask)

            cv2.rectangle(image, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)

    return mask, bbox, detected


def execute_pick(robot, target_pose, drop_height):
    """动作原语：到上方 -> 下降 -> 夹取 -> 抬升"""

    below = target_pose.copy()
    below[2] = -408.5 / 1000
    ret = robot.rm_movel(below, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"下降到目标失败，错误码：{ret}")

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        ret = robot.rm_set_gripper_pick_on(
            speed=GRIPPER_CONFIG['pick']['speed'],
            block=True,
            timeout=GRIPPER_CONFIG['pick']['timeout'],
            force=GRIPPER_CONFIG['pick']['force']
        )
        if ret == 0:
            break
        attempts += 1
        time.sleep(1)
    if attempts == MAX_ATTEMPTS:
        raise Exception("达到最大尝试次数，夹取仍未成功")

    ret = robot.rm_movel(target_pose, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"抬升失败，错误码：{ret}")


def execute_place(robot, target_pose, drop_height):
    """动作原语：到上方 -> 下降 -> 松开 -> 抬升"""
    above = target_pose.copy()
    tem_pos = [424 / 1000, -92 / 1000, -439 / 1000, 3.15, 0, 1.618]
    ret = robot.rm_movej_p(tem_pos, v=MOVE_SPEED, r=0, connect=0, block=1)
    ret = robot.rm_movel(above, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"移动到放置上方失败，错误码：{ret}")

    below = above.copy()
    below[2] -= drop_height
    ret = robot.rm_movel(below, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"下降到放置高度失败，错误码：{ret}")

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        ret = robot.rm_set_gripper_release(
            speed=GRIPPER_CONFIG['release']['speed'],
            block=True,
            timeout=GRIPPER_CONFIG['release']['timeout']
        )
        if ret == 0:
            break
        attempts += 1
        time.sleep(1)
    if attempts == MAX_ATTEMPTS:
        raise Exception("达到最大尝试次数，释放仍未成功")

    ret = robot.rm_movel(above, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"抬升失败，错误码：{ret}")

    ret = robot.rm_movel(tem_pos, v=MOVE_SPEED, r=0, connect=0, block=1)

    ret = robot.rm_movej_p(PLACE_POSITION['pos2'].copy(), v=MOVE_SPEED, r=0, connect=0, block=1)


def place_at_fixed_position(robot):
    """使用动作原语完成放置"""
    execute_place(robot, PLACE_POSITION['above'].copy(), PLACE_POSITION['drop_height'])
    ret = robot.rm_movej_p(INITIAL_POSE, v=MOVE_SPEED, r=0, connect=0, block=1)
    if ret != 0:
        raise Exception(f"回到初始位姿失败，错误码：{ret}")


def capture_and_move(controller, robot, width=640, height=480):
    """获取一帧图像并执行抓取、放置"""
    try:
        controller.openclaw(robot)

        ret, initial_state = robot.rm_get_current_arm_state()
        if ret != 0:
            raise Exception(f"获取初始位姿失败，错误码：{ret}")
        initial_pose = initial_state['pose']

        color_image, depth_image, color_intr = controller.get_frames_from_gui()
        if color_image is None or depth_image is None or color_intr is None:
            raise Exception("无法获取图像或相机内参")

        os.makedirs(PICTURE_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(PICTURE_DIR, 'original_image.jpg'),
                    cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR))

        mask, bbox, detected = detect_target(
            color_image,
            controller.yolo_model,
            controller.sam_model,
            controller.process_mask_with_gmm,
            width,
            height
        )
        if not detected:
            cv2.imwrite(os.path.join(PICTURE_DIR, 'failed_detection.jpg'),
                        cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR))
            raise Exception("未检测到目标")

        cv2.imwrite(os.path.join(PICTURE_DIR, 'mask_result.jpg'), mask)

        ret, state_dict = robot.rm_get_current_arm_state()
        if ret != 0:
            raise Exception(f"获取机械臂状态失败，错误码：{ret}")
        pose = state_dict['pose']

        above_object_pose, _, _ = vertical_catch(
            mask,
            depth_image,
            color_intr,
            pose,
            150,
            controller.gripper_offset,
            controller.rotation_matrix,
            controller.translation_vector
        )

        camera_above_pose = above_object_pose.copy()
        camera_above_pose[0] -= 0.07

        ret = robot.rm_movej_p(camera_above_pose, v=15, r=0, connect=0, block=1)
        if ret != 0:
            raise Exception(f"移动到预备位置失败，错误码：{ret}")
        time.sleep(1)

        color_image, depth_image, color_intr = controller.get_frames_from_gui()
        if color_image is None or depth_image is None or color_intr is None:
            raise Exception("二次检测：无法获取图像或相机内参")

        mask, bbox, detected = detect_target(
            color_image,
            controller.yolo_model,
            controller.sam_model,
            controller.process_mask_with_gmm,
            width,
            height
        )
        if not detected:
            raise Exception("二次检测：未检测到目标")

        ret, current_state = robot.rm_get_current_arm_state()
        if ret != 0:
            raise Exception(f"获取当前状态失败，错误码：{ret}")
        current_pose = current_state['pose']

        _, _, final_pose = vertical_catch(
            mask,
            depth_image,
            color_intr,
            current_pose,
            150,
            controller.gripper_offset,
            controller.rotation_matrix,
            controller.translation_vector
        )

        final_pose[3] = controller.gripper_offset[0]
        final_pose[4] = controller.gripper_offset[1]
        final_pose[5] = controller.gripper_offset[2]

        above_target_pose = final_pose.copy()

        above_target_pose[2] = current_pose[2]
        above_target_pose[0] -= 0.025
        above_target_pose[1] += 0.015

        ret = robot.rm_movel(above_target_pose, v=15, r=0, connect=0, block=1)
        if ret != 0:
            raise Exception(f"移动到目标物体上方失败，错误码：{ret}")

        execute_pick(robot, above_target_pose, drop_height=0.08)

        ret = robot.rm_movej_p(initial_pose, v=15, r=0, connect=0, block=1)
        if ret != 0:
            raise Exception(f"回到初始位姿失败，错误码：{ret}")

        place_at_fixed_position(robot)

        return True
    except Exception as exc:
        print(f"错误: {exc}")
        return False


if __name__ == "__main__":
    controller = RobotController()
    robot = controller.init_robot1()
    try:
        if robot is None:
            raise RuntimeError("robot1 初始化失败")

        success = capture_and_move(controller, robot)
        print("1")
        if success:
            print("抓取执行完成")
            place_at_fixed_position(robot)
        else:
            print("抓取流程失败")
    finally:
        controller.shutdown()

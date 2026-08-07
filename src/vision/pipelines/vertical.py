from collections.abc import Mapping, Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

from .coordinates import convert
from .poses import change_pose


def vertical_catch_main(
    mask: NDArray[np.uint8],
    depth_frame: NDArray[np.generic],
    color_intr: Mapping[str, float],
    current_pose: Sequence[float],
    arm_gripper_length: float,
    vertical_rx_ry_rz: Sequence[float],
    rotation_matrix: Sequence[Sequence[float]],
    translation_vector: Sequence[float],
    use_point_depth_or_mean: bool = True,
) -> tuple[list[float], list[float], list[float]]:
    """
    :param center:  抓取的中心点位
    :param mask:    抓取物体的轮廓信息
    :param depth_frame:     物体的深度值信息
    :param color_intr:      相机的内参
    :param current_pose:    当前的位姿信息
    :param arm_gripper_length:      夹爪的长度
    :param vertical_rx_ry_rz:       正确的夹爪偏移角度
    :param rotation_matrix:         手眼标定的旋转矩阵
    :param translation_vector:      手眼标定的平移矩阵
    :param use_point_depth_or_mean:     使用一个点位的深度信息还是整个物体的平均深度

    :return:
    above_object_pose：      垂直抓取物体上方的位姿
    correct_angle_pose：     垂直抓取物体正确的角度位姿
    finally_pose：           垂直抓取最终下爪的抓取位姿
    """
    # 开始凭着mask中心点位抓取``
    _, center = compute_angle_with_mask(mask)
    real_x, real_y = center[0], center[1]

    # 修改对抓取点位深度信息的获取方式由单点改为整个mask的深度信息
    if not use_point_depth_or_mean:
        distance_mm = float(depth_frame[real_y, real_x])
    else:
        # 获取物体深度信息
        depth_mask = depth_frame[mask == 255]  # 提取mask区域内的深度值
        non_zero_values = depth_mask[depth_mask != 0]  # 过滤掉深度值为0的无效点
        sorted_values = np.sort(non_zero_values)  # 将深度值从小到大排序
        top_20_percent_index = int(0.2 * len(sorted_values))  # 计算前20%的索引位置
        top_20_percent_values = sorted_values[:top_20_percent_index]  # 取最近的20%深度值
        if top_20_percent_values.size == 0:
            raise ValueError("mask does not contain valid depth values")
        distance_mm = float(
            np.mean(top_20_percent_values)
        )  # 计算这 20% 深度值的平均值作为目标距离

    x_mm = float(
        int(distance_mm * (real_x - color_intr["ppx"]) / color_intr["fx"])
    )
    y_mm = float(
        int(distance_mm * (real_y - color_intr["ppy"]) / color_intr["fy"])
    )
    distance_mm = float(int(distance_mm))
    
    x, y, z = x_mm * 0.001, y_mm * 0.001, distance_mm * 0.001

    # 计算物体位置，位置是物体中心点正上方10公分
    if len(current_pose) != 6:
        raise ValueError("current_pose must contain six values")
    obj_pose = convert(
        x,
        y,
        z,
        current_pose[0],
        current_pose[1],
        current_pose[2],
        current_pose[3],
        current_pose[4],
        current_pose[5],
        rotation_matrix,
        translation_vector,
    ).tolist()
    
    # 最终位置为物体上方 + 夹爪 + 10cm的距离
    obj_pose[2] = obj_pose.copy()[2] + 0.10 + arm_gripper_length * 0.001

    # 修改为垂直于桌面的RX,RY,RZ``
    obj_pose[3:] = vertical_rx_ry_rz

    above_object_pose = obj_pose.copy()

    # 计算偏转角度
    _angle = obj_pose[5] - vertical_rx_ry_rz[2]
    angle_joint, _ = compute_angle_with_mask(mask)
    angle = (angle_joint / 180) * 3.14 - _angle
    catch_pose = obj_pose.copy()

    # 移动到上方和旋转角度决定了抓取的流畅性

    # 计算偏转角
    if obj_pose[5] - angle > 0:
        catch_pose[5] = obj_pose[5] - angle
    else:
        catch_pose[5] = obj_pose[5] - angle

    correct_angle_pose = catch_pose.copy()

    # 计算需要下降的距离
    descent_distance = 0.15

    #计算最终位姿
    finally_pose = change_pose(catch_pose, descent_distance)

    finally_pose = finally_pose.copy()
    return above_object_pose, correct_angle_pose, finally_pose


def compute_angle_with_mask(
    mask: NDArray[np.uint8],
) -> tuple[float, tuple[int, int]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 初始化最大外接矩形
    min_rect = None
    max_area = 0.0

    for contour in contours:
        # 计算最小外接矩形
        center, (w, h), angle = cv2.minAreaRect(contour)
        area = w * h
        if area > max_area:
            max_area = area
            min_rect = center, (w, h), angle

    # 获取最小外接矩形的信息
    if min_rect is None:
        raise ValueError("mask does not contain a contour")
    center, (width, height), angle = min_rect

    if width > height:
        angle = -(90 - angle)
    center_pixel = (int(round(center[0])), int(round(center[1])))
    return float(angle), center_pixel

#!/usr/bin/env python
# !coding=utf-8
"""

在 机械臂 零位状态 通过设置 相机坐标系下物体得 位置 x y z 来验证 计算出来得 其次变换矩阵是否准确

"""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


def euler_angles_to_rotation_matrix(
    rx: float,
    ry: float,
    rz: float,
) -> NDArray[np.float64]:
    # 计算旋转矩阵
    rotation_x = np.array(
        [[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]]
    )

    rotation_y = np.array(
        [[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]]
    )

    rotation_z = np.array(
        [[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]]
    )

    return rotation_z @ rotation_y @ rotation_x  # 先 z 轴，再 y 轴，最后 x 轴


def pose_to_homogeneous_matrix(pose: Sequence[float]) -> NDArray[np.float64]:
    x, y, z, rx, ry, rz = pose
    rotation = euler_angles_to_rotation_matrix(rx, ry, rz)
    translation = np.array([x, y, z], dtype=np.float64).reshape(3, 1)

    homogeneous: NDArray[np.float64] = np.eye(4, dtype=np.float64)
    homogeneous[:3, :3] = rotation
    homogeneous[:3, 3] = translation[:, 0]

    return homogeneous


def change_pose(pose: Sequence[float], num: float) -> list[float]:
    """
    根据物体和基座的其次变换矩阵 求得 物体z轴 0 0 num 所在位置对应 基座标系的位姿
    y轴补偿6cm
    Args:
        pose:
        nums:

    Returns:
    pose:

    """

    matrix = pose_to_homogeneous_matrix(pose)

    obj_init = np.array([0, 0, num])

    obj_init = np.append(obj_init, [1])  # 将物体坐标转换为齐次坐标

    obj_base_init = matrix.dot(obj_init)

    return [float(value) for value in obj_base_init[:3]] + list(pose[3:])

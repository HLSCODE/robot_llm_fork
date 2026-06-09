from __future__ import annotations

import ast
import json
import math
import re
from typing import Iterable


POSE_LINEAR_UNITS_PER_UDP_CM = 0.01  # UDP localization x/y are cm, robot pose x/y/z are m.
POSE_LENGTH = 6
LOCALIZATION_ANGLE_SIGN = -1.0  # UDP angle is clockwise-positive; math yaw is counterclockwise-positive.

# Arm calibration from scripts/calibrate_pose_compensation.py.
# Coordinates are in the localization/body frame: +x forward, +y left.
LEFT_ARM_LOCATOR_TO_BASE_CM = {
    "x": 50.206008,
    "y": -24.282551,
    "z": 0.0,
}

RIGHT_ARM_LOCATOR_TO_BASE_CM = {
    "x": 48.708676,
    "y": -30.002009,
    "z": 0.0,
}

# Arm axes relative to the localization/body axes:
# body +X = forward, body +Y = left
# arm  +X = left,    arm  +Y = back
BODY_FROM_ARM_ROT = [
    [0.0, -1.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
]


def parse_pose(value) -> list[float]:
    """Parse a robot pose [x, y, z, rx, ry, rz] from list or text."""
    if isinstance(value, (list, tuple)):
        pose = [float(v) for v in value]
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[^\]]+\]", text)
            if not match:
                raise ValueError(f"Cannot find pose list in: {value}")
            parsed = ast.literal_eval(match.group(0))
        pose = [float(v) for v in parsed]
    else:
        raise TypeError(f"Unsupported pose value: {type(value).__name__}")

    if len(pose) != POSE_LENGTH:
        raise ValueError(f"Pose must contain {POSE_LENGTH} values, got {len(pose)}")
    return pose


def compensate_pose(
    taught_pose,
    teach_offset: dict,
    current_offset: dict,
    arm: str | None = None,
    locator_to_arm_base_cm: dict | Iterable[float] | None = None,
) -> list[float]:
    """Return pose corrected from current UDP offset back to the taught offset."""
    pose = parse_pose(taught_pose)
    locator_to_arm_base_cm = locator_to_arm_base_cm or get_arm_locator_to_base_cm(arm)

    corrected = corrected_arm_base_from_tcp_matrix(
        pose,
        teach_offset,
        current_offset,
        locator_to_arm_base_cm,
    )
    return matrix_to_pose(corrected)


def get_arm_locator_to_base_cm(arm: str | None) -> dict:
    arm_key = normalize_arm_name(arm)
    if arm_key == "left":
        return LEFT_ARM_LOCATOR_TO_BASE_CM
    return RIGHT_ARM_LOCATOR_TO_BASE_CM


def normalize_arm_name(arm: str | None) -> str:
    text = str(arm or "").strip().lower()
    if text in {"left", "l", "left_arm", "robot1", "r1", "1", "\u5de6", "\u5de6\u81c2"}:
        return "left"
    if text in {"right", "r", "right_arm", "robot2", "r2", "2", "\u53f3", "\u53f3\u81c2"}:
        return "right"
    return "right"


def corrected_arm_base_from_tcp_matrix(
    pose: list[float],
    teach_offset: dict,
    current_offset: dict,
    locator_to_arm_base_cm: dict | Iterable[float],
) -> list[list[float]]:
    teach_yaw = localization_yaw_rad(teach_offset)
    current_yaw = localization_yaw_rad(current_offset)
    body_rel_rot = matmul(transpose3(yaw_matrix(current_yaw)), yaw_matrix(teach_yaw))
    arm_from_body_rot = transpose3(BODY_FROM_ARM_ROT)
    current_arm_from_teach_arm = matmul(
        matmul(arm_from_body_rot, body_rel_rot),
        BODY_FROM_ARM_ROT,
    )
    corrected_rot = matmul(current_arm_from_teach_arm, euler_xyz_to_matrix(*pose[3:6]))

    arm_base_body = locator_to_arm_base_m(locator_to_arm_base_cm)
    taught_tcp_body = rotate_point(BODY_FROM_ARM_ROT, pose[:3])
    rotated_tcp_body = rotate_point(
        body_rel_rot,
        vector_add(arm_base_body, taught_tcp_body),
    )
    locator_delta_body = [
        (_offset_value(current_offset, "x") - _offset_value(teach_offset, "x"))
        * POSE_LINEAR_UNITS_PER_UDP_CM,
        (_offset_value(current_offset, "y") - _offset_value(teach_offset, "y"))
        * POSE_LINEAR_UNITS_PER_UDP_CM,
        0.0,
    ]
    corrected_position_body = vector_sub(
        vector_sub(rotated_tcp_body, arm_base_body),
        locator_delta_body,
    )
    corrected_position = rotate_point(arm_from_body_rot, corrected_position_body)

    return [
        [corrected_rot[0][0], corrected_rot[0][1], corrected_rot[0][2], corrected_position[0]],
        [corrected_rot[1][0], corrected_rot[1][1], corrected_rot[1][2], corrected_position[1]],
        [corrected_rot[2][0], corrected_rot[2][1], corrected_rot[2][2], corrected_position[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def world_from_arm_base_matrix(
    localization_offset: dict,
    locator_to_arm_base_cm: dict | Iterable[float],
) -> list[list[float]]:
    world_from_body_rot = yaw_matrix(localization_yaw_rad(localization_offset))
    world_from_arm_rot = matmul(world_from_body_rot, BODY_FROM_ARM_ROT)

    locator_world = [
        _offset_value(localization_offset, "x") * POSE_LINEAR_UNITS_PER_UDP_CM,
        _offset_value(localization_offset, "y") * POSE_LINEAR_UNITS_PER_UDP_CM,
        0.0,
    ]
    arm_base_body = locator_to_arm_base_m(locator_to_arm_base_cm)
    arm_base_world = [
        locator_world[i]
        + sum(world_from_body_rot[i][j] * arm_base_body[j] for j in range(3))
        for i in range(3)
    ]

    return [
        [world_from_arm_rot[0][0], world_from_arm_rot[0][1], world_from_arm_rot[0][2], arm_base_world[0]],
        [world_from_arm_rot[1][0], world_from_arm_rot[1][1], world_from_arm_rot[1][2], arm_base_world[1]],
        [world_from_arm_rot[2][0], world_from_arm_rot[2][1], world_from_arm_rot[2][2], arm_base_world[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def localization_yaw_rad(offset: dict) -> float:
    return LOCALIZATION_ANGLE_SIGN * math.radians(_offset_value(offset, "angle"))


def yaw_matrix(yaw_rad: float) -> list[list[float]]:
    c = math.cos(yaw_rad)
    s = math.sin(yaw_rad)
    return [
        [c, -s, 0.0],
        [s, c, 0.0],
        [0.0, 0.0, 1.0],
    ]


def rotate_point(rot: list[list[float]], point: Iterable[float]) -> list[float]:
    x, y, z = [float(v) for v in point]
    return [
        rot[0][0] * x + rot[0][1] * y + rot[0][2] * z,
        rot[1][0] * x + rot[1][1] * y + rot[1][2] * z,
        rot[2][0] * x + rot[2][1] * y + rot[2][2] * z,
    ]


def vector_add(a: Iterable[float], b: Iterable[float]) -> list[float]:
    a_values = [float(v) for v in a]
    b_values = [float(v) for v in b]
    return [a_values[i] + b_values[i] for i in range(3)]


def vector_sub(a: Iterable[float], b: Iterable[float]) -> list[float]:
    a_values = [float(v) for v in a]
    b_values = [float(v) for v in b]
    return [a_values[i] - b_values[i] for i in range(3)]


def locator_to_arm_base_m(offset_cm: dict | Iterable[float]) -> list[float]:
    if isinstance(offset_cm, dict):
        return [
            _optional_offset_value(offset_cm, ("x", "X", "x_cm", "x_forward"), 0.0)
            * POSE_LINEAR_UNITS_PER_UDP_CM,
            _optional_offset_value(offset_cm, ("y", "Y", "y_cm", "y_left"), 0.0)
            * POSE_LINEAR_UNITS_PER_UDP_CM,
            _optional_offset_value(offset_cm, ("z", "Z", "z_cm", "z_up"), 0.0)
            * POSE_LINEAR_UNITS_PER_UDP_CM,
        ]

    values = [float(v) for v in offset_cm]
    if len(values) == 2:
        values.append(0.0)
    if len(values) != 3:
        raise ValueError("locator_to_arm_base_cm must contain 2 or 3 values")
    return [v * POSE_LINEAR_UNITS_PER_UDP_CM for v in values]


def offset_to_matrix(offset: dict) -> list[list[float]]:
    x_cm = _offset_value(offset, "x")
    y_cm = _offset_value(offset, "y")
    angle_deg = _offset_value(offset, "angle")

    x_units = x_cm * POSE_LINEAR_UNITS_PER_UDP_CM
    y_units = y_cm * POSE_LINEAR_UNITS_PER_UDP_CM
    angle_rad = LOCALIZATION_ANGLE_SIGN * math.radians(angle_deg)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)

    return [
        [c, -s, 0.0, x_units],
        [s, c, 0.0, y_units],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def pose_to_matrix(pose: Iterable[float]) -> list[list[float]]:
    x, y, z, rx, ry, rz = [float(v) for v in pose]
    r = euler_xyz_to_matrix(rx, ry, rz)
    return [
        [r[0][0], r[0][1], r[0][2], x],
        [r[1][0], r[1][1], r[1][2], y],
        [r[2][0], r[2][1], r[2][2], z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def matrix_to_pose(matrix: list[list[float]]) -> list[float]:
    rot = [row[:3] for row in matrix[:3]]
    rx, ry, rz = matrix_to_euler_xyz(rot)
    pose = [matrix[0][3], matrix[1][3], matrix[2][3], rx, ry, rz]
    return [round(v, 6) for v in pose]


def euler_xyz_to_matrix(rx: float, ry: float, rz: float) -> list[list[float]]:
    """Convert RealMan pose Euler angles to a rotation matrix.

    The rest of the project uses scipy/vision convention R = Rz @ Ry @ Rx.
    """
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def matrix_to_euler_xyz(rot: list[list[float]]) -> list[float]:
    sy = max(-1.0, min(1.0, -rot[2][0]))
    ry = math.asin(sy)
    cy = math.cos(ry)

    if abs(cy) > 1e-9:
        rx = math.atan2(rot[2][1], rot[2][2])
        rz = math.atan2(rot[1][0], rot[0][0])
    else:
        rx = 0.0
        rz = math.atan2(-rot[0][1], rot[1][1])

    return [rx, ry, rz]


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    rows = len(a)
    cols = len(b[0])
    inner = len(b)
    return [
        [sum(a[i][k] * b[k][j] for k in range(inner)) for j in range(cols)]
        for i in range(rows)
    ]


def invert_transform(t: list[list[float]]) -> list[list[float]]:
    r = [row[:3] for row in t[:3]]
    rt = transpose3(r)
    p = [t[0][3], t[1][3], t[2][3]]
    inv_p = [-sum(rt[i][j] * p[j] for j in range(3)) for i in range(3)]
    return [
        [rt[0][0], rt[0][1], rt[0][2], inv_p[0]],
        [rt[1][0], rt[1][1], rt[1][2], inv_p[1]],
        [rt[2][0], rt[2][1], rt[2][2], inv_p[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def transpose3(m: list[list[float]]) -> list[list[float]]:
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _offset_value(offset: dict, key: str) -> float:
    aliases = {
        "x": ("x", "X", "x_cm"),
        "y": ("y", "Y", "y_cm"),
        "angle": ("angle", "Angle", "angel", "Angel", "angle_deg"),
    }
    for alias in aliases[key]:
        if alias in offset:
            return float(offset[alias])
    raise KeyError(f"Missing UDP offset field: {key}")


def _optional_offset_value(offset: dict, aliases: Iterable[str], default: float) -> float:
    for alias in aliases:
        if alias in offset:
            return float(offset[alias])
    return default

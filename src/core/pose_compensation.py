from __future__ import annotations

import ast
import json
import math
import re
from typing import Iterable


POSE_LINEAR_UNITS_PER_UDP_CM = 10.0
POSE_LENGTH = 6


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


def compensate_pose(taught_pose, teach_offset: dict, current_offset: dict) -> list[float]:
    """Return pose corrected from current UDP offset back to the taught offset."""
    t_pose = pose_to_matrix(parse_pose(taught_pose))
    t_teach = offset_to_matrix(teach_offset)
    t_current = offset_to_matrix(current_offset)
    corrected = matmul(matmul(invert_transform(t_current), t_teach), t_pose)
    return matrix_to_pose(corrected)


def offset_to_matrix(offset: dict) -> list[list[float]]:
    x = _offset_value(offset, "x")
    y = _offset_value(offset, "y")
    angle_deg = _offset_value(offset, "angle")

    x_units = x * POSE_LINEAR_UNITS_PER_UDP_CM
    y_units = y * POSE_LINEAR_UNITS_PER_UDP_CM
    angle_rad = math.radians(angle_deg)
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
    r = rotvec_to_matrix([rx, ry, rz])
    return [
        [r[0][0], r[0][1], r[0][2], x],
        [r[1][0], r[1][1], r[1][2], y],
        [r[2][0], r[2][1], r[2][2], z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def matrix_to_pose(matrix: list[list[float]]) -> list[float]:
    rot = [row[:3] for row in matrix[:3]]
    rx, ry, rz = matrix_to_rotvec(rot)
    pose = [matrix[0][3], matrix[1][3], matrix[2][3], rx, ry, rz]
    return [round(v, 6) for v in pose]


def rotvec_to_matrix(rotvec: Iterable[float]) -> list[list[float]]:
    rx, ry, rz = [float(v) for v in rotvec]
    theta = math.sqrt(rx * rx + ry * ry + rz * rz)
    if theta < 1e-12:
        return identity3()

    kx, ky, kz = rx / theta, ry / theta, rz / theta
    c = math.cos(theta)
    s = math.sin(theta)
    v = 1.0 - c

    return [
        [kx * kx * v + c, kx * ky * v - kz * s, kx * kz * v + ky * s],
        [ky * kx * v + kz * s, ky * ky * v + c, ky * kz * v - kx * s],
        [kz * kx * v - ky * s, kz * ky * v + kx * s, kz * kz * v + c],
    ]


def matrix_to_rotvec(rot: list[list[float]]) -> list[float]:
    trace = rot[0][0] + rot[1][1] + rot[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    theta = math.acos(cos_theta)
    if theta < 1e-12:
        return [0.0, 0.0, 0.0]

    if abs(math.pi - theta) < 1e-6:
        return _rotvec_near_pi(rot, theta)

    scale = theta / (2.0 * math.sin(theta))
    return [
        (rot[2][1] - rot[1][2]) * scale,
        (rot[0][2] - rot[2][0]) * scale,
        (rot[1][0] - rot[0][1]) * scale,
    ]


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


def identity3() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def transpose3(m: list[list[float]]) -> list[list[float]]:
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _rotvec_near_pi(rot: list[list[float]], theta: float) -> list[float]:
    axis = [
        math.sqrt(max(0.0, (rot[0][0] + 1.0) / 2.0)),
        math.sqrt(max(0.0, (rot[1][1] + 1.0) / 2.0)),
        math.sqrt(max(0.0, (rot[2][2] + 1.0) / 2.0)),
    ]

    if rot[0][1] < 0.0:
        axis[1] = -axis[1]
    if rot[0][2] < 0.0:
        axis[2] = -axis[2]

    norm = math.sqrt(sum(v * v for v in axis))
    if norm < 1e-12:
        return [theta, 0.0, 0.0]
    return [theta * v / norm for v in axis]


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

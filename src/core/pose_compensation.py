from __future__ import annotations

import ast
import json
import math
import re
from typing import Iterable


POSE_LINEAR_UNITS_PER_UDP_CM = 0.01  # UDP localization x/y are cm, robot pose x/y/z are m.
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
    pose = parse_pose(taught_pose)
    dx_cm = _offset_value(current_offset, "x") - _offset_value(teach_offset, "x")
    dy_cm = _offset_value(current_offset, "y") - _offset_value(teach_offset, "y")
    dangle_deg = _offset_value(current_offset, "angle") - _offset_value(teach_offset, "angle")

    # Localization/base axes mapped into the arm base frame:
    # base +X -> arm -Y, base +Y -> arm +X. To keep the world target fixed,
    # the commanded arm pose moves opposite to the chassis displacement.
    compensation = {
        "x": dy_cm,
        "y": -dx_cm,
        "angle": -dangle_deg,
    }

    t_pose = pose_to_matrix(pose)
    corrected = matmul(invert_transform(offset_to_matrix(compensation)), t_pose)
    return matrix_to_pose(corrected)


def offset_to_matrix(offset: dict) -> list[list[float]]:
    x_cm = _offset_value(offset, "x")
    y_cm = _offset_value(offset, "y")
    angle_deg = _offset_value(offset, "angle")

    x_units = x_cm * POSE_LINEAR_UNITS_PER_UDP_CM
    y_units = y_cm * POSE_LINEAR_UNITS_PER_UDP_CM
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

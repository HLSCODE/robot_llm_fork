from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np

from .detector import MARKER_ORDER, find_l_inner_corners


def get_pose_number(pose: dict, key: str) -> float:
    for candidate in (key, key.lower(), key.upper()):
        if candidate in pose:
            return float(pose[candidate])
    raise ValueError(f"pose is missing key: {key}")


def as_matrix4(value, name: str = "transform") -> np.ndarray:
    mat = np.asarray(value, dtype=np.float64)
    if mat.shape != (4, 4):
        raise ValueError(f"{name} must be a 4x4 matrix, got shape {mat.shape}")
    return mat


def invert_transform(transform) -> np.ndarray:
    transform = as_matrix4(transform)
    inv = np.eye(4, dtype=np.float64)
    r = transform[:3, :3]
    t = transform[:3, 3]
    inv[:3, :3] = r.T
    inv[:3, 3] = -r.T @ t
    return inv


def normalize_angle_values(values: np.ndarray, angle_unit: str) -> np.ndarray:
    if angle_unit.lower() in ("deg", "degree", "degrees"):
        return np.deg2rad(values)
    if angle_unit.lower() in ("rad", "radian", "radians"):
        return values
    raise ValueError(f"Unsupported angle unit: {angle_unit}")


def rotation_from_rpy(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cx, sx = np.cos(roll), np.sin(roll)
    cy, sy = np.cos(pitch), np.sin(pitch)
    cz, sz = np.cos(yaw), np.sin(yaw)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rz @ ry @ rx


def rpy_from_rotation(rotation: np.ndarray) -> np.ndarray:
    sy = np.sqrt(rotation[0, 0] * rotation[0, 0] + rotation[1, 0] * rotation[1, 0])
    if sy > 1e-9:
        roll = np.arctan2(rotation[2, 1], rotation[2, 2])
        pitch = np.arctan2(-rotation[2, 0], sy)
        yaw = np.arctan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = np.arctan2(-rotation[1, 2], rotation[1, 1])
        pitch = np.arctan2(-rotation[2, 0], sy)
        yaw = 0.0
    return np.array([roll, pitch, yaw], dtype=np.float64)


def pose_to_transform(
    pose: Iterable[float],
    rotation_type: str = "rpy",
    angle_unit: str = "rad",
) -> np.ndarray:
    values = [float(v) for v in pose]
    if len(values) != 6:
        raise ValueError(f"pose must contain 6 values, got {len(values)}")
    xyz = np.array(values[:3], dtype=np.float64)
    angles = normalize_angle_values(np.array(values[3:6], dtype=np.float64), angle_unit)
    rotation_type = rotation_type.lower()
    if rotation_type in ("rotvec", "rvec", "rodrigues", "axis_angle"):
        rot, _ = cv2.Rodrigues(angles.reshape(3, 1))
    elif rotation_type in ("rpy", "euler", "roll_pitch_yaw"):
        rot = rotation_from_rpy(angles[0], angles[1], angles[2])
    else:
        raise ValueError(f"Unsupported rotation type: {rotation_type}")

    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rot
    transform[:3, 3] = xyz
    return transform


def transform_to_pose(
    transform,
    rotation_type: str = "rpy",
    angle_unit: str = "rad",
) -> list[float]:
    transform = as_matrix4(transform)
    rotation_type = rotation_type.lower()
    if rotation_type in ("rotvec", "rvec", "rodrigues", "axis_angle"):
        angles, _ = cv2.Rodrigues(transform[:3, :3])
        angles = angles.reshape(3)
    elif rotation_type in ("rpy", "euler", "roll_pitch_yaw"):
        angles = rpy_from_rotation(transform[:3, :3])
    else:
        raise ValueError(f"Unsupported rotation type: {rotation_type}")

    if angle_unit.lower() in ("deg", "degree", "degrees"):
        angles = np.rad2deg(angles)
    elif angle_unit.lower() not in ("rad", "radian", "radians"):
        raise ValueError(f"Unsupported angle unit: {angle_unit}")

    values = [
        float(transform[0, 3]),
        float(transform[1, 3]),
        float(transform[2, 3]),
        float(angles[0]),
        float(angles[1]),
        float(angles[2]),
    ]
    return [round(v, 6) for v in values]


def transform_to_xyz_rvec(transform) -> dict[str, float]:
    transform = as_matrix4(transform)
    rvec, _ = cv2.Rodrigues(transform[:3, :3])
    return {
        "x": float(transform[0, 3]),
        "y": float(transform[1, 3]),
        "z": float(transform[2, 3]),
        "rx": float(rvec[0, 0]),
        "ry": float(rvec[1, 0]),
        "rz": float(rvec[2, 0]),
    }


def transform_to_robot_pose(
    transform,
    rotation_type: str = "rpy",
    angle_unit: str = "rad",
) -> dict[str, float]:
    pose = transform_to_pose(transform, rotation_type=rotation_type, angle_unit=angle_unit)
    return {
        "x": pose[0],
        "y": pose[1],
        "z": pose[2],
        "RX": pose[3],
        "RY": pose[4],
        "RZ": pose[5],
    }


def pose_mapping_to_transform(
    pose: dict,
    default_rotation_type: str = "rotvec",
    default_angle_unit: str = "rad",
) -> np.ndarray:
    values = [
        get_pose_number(pose, "x"),
        get_pose_number(pose, "y"),
        get_pose_number(pose, "z"),
        get_pose_number(pose, "rx"),
        get_pose_number(pose, "ry"),
        get_pose_number(pose, "rz"),
    ]
    return pose_to_transform(
        values,
        rotation_type=pose.get("rotation_type", default_rotation_type),
        angle_unit=pose.get("angle_unit", default_angle_unit),
    )


def get_pose_rotation_type(config: dict) -> str:
    return config.get("pose_rotation_type", "rotvec")


def get_pose_angle_unit(config: dict) -> str:
    return config.get("pose_angle_unit", "rad")


def get_transform(config: dict, key: str) -> np.ndarray:
    if key in config:
        value = config[key]
        if isinstance(value, dict):
            return pose_mapping_to_transform(
                value,
                default_rotation_type=get_pose_rotation_type(config),
                default_angle_unit=get_pose_angle_unit(config),
            )
        return as_matrix4(value, key)

    pose_key = f"{key}_xyz_rvec"
    if pose_key in config:
        return pose_mapping_to_transform(
            config[pose_key],
            default_rotation_type="rotvec",
            default_angle_unit=get_pose_angle_unit(config),
        )

    pose_key = f"{key}_pose"
    if pose_key in config:
        return pose_mapping_to_transform(
            config[pose_key],
            default_rotation_type=get_pose_rotation_type(config),
            default_angle_unit=get_pose_angle_unit(config),
        )

    raise KeyError(f"config must contain {key}, {key}_xyz_rvec, or {key}_pose")


def get_camera_matrix(config: dict, image_size: tuple[int, int] | None = None) -> np.ndarray:
    camera_matrix = np.asarray(config["camera_matrix"], dtype=np.float64)
    source_size = config.get("camera_matrix_resolution")
    if source_size is None or image_size is None:
        return camera_matrix

    source_w, source_h = float(source_size[0]), float(source_size[1])
    image_w, image_h = float(image_size[0]), float(image_size[1])
    if source_w <= 0 or source_h <= 0:
        raise ValueError("camera_matrix_resolution must be [width, height]")
    scaled = camera_matrix.copy()
    scaled[0, 0] *= image_w / source_w
    scaled[0, 2] *= image_w / source_w
    scaled[1, 1] *= image_h / source_h
    scaled[1, 2] *= image_h / source_h
    return scaled


def get_object_points(config: dict) -> np.ndarray:
    marker = config.get("marker", {})
    if "points" in marker:
        points = np.asarray(marker["points"], dtype=np.float64)
        if points.shape != (4, 3):
            raise ValueError("marker.points must be a 4x3 array")
        return points

    width = marker.get("width")
    height = marker.get("height")
    if width is None or height is None:
        raise KeyError("config.marker must contain width/height or points")
    return np.array(
        [
            [0.0, 0.0, 0.0],
            [float(width), 0.0, 0.0],
            [float(width), float(height), 0.0],
            [0.0, float(height), 0.0],
        ],
        dtype=np.float64,
    )


def get_detected_image_points(markers) -> np.ndarray:
    if markers is None or len(markers) != 4:
        count = 0 if markers is None else len(markers)
        raise ValueError(f"image must contain exactly 4 detected markers, got {count}")
    by_order = {m.get("order_name"): m for m in markers if "order_name" in m}
    if all(name in by_order for name in MARKER_ORDER):
        ordered = [by_order[name] for name in MARKER_ORDER]
    else:
        ordered = markers
    return np.asarray([m["inner_corner_refined"] for m in ordered], dtype=np.float64)


def solve_marker_pose(object_points, image_points, camera_matrix, dist_coeffs) -> np.ndarray:
    object_points = np.asarray(object_points, dtype=np.float64)
    image_points = np.asarray(image_points, dtype=np.float64)
    camera_matrix = np.asarray(camera_matrix, dtype=np.float64)
    dist_coeffs = np.asarray(dist_coeffs, dtype=np.float64).reshape(-1, 1)
    flags = cv2.SOLVEPNP_IPPE if len(object_points) == 4 else cv2.SOLVEPNP_ITERATIVE
    ok, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=flags,
    )
    if not ok:
        raise RuntimeError("cv2.solvePnP failed")
    rotation, _ = cv2.Rodrigues(rvec)
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = tvec.reshape(3)
    return transform


def compute_marker_in_base_from_points(
    config: dict,
    image_points,
    t_b_e,
    image_size: tuple[int, int] | None = None,
) -> dict:
    camera_matrix = get_camera_matrix(config, image_size=image_size)
    dist_coeffs = np.asarray(config.get("dist_coeffs", []), dtype=np.float64)
    object_points = get_object_points(config)
    t_c_m = solve_marker_pose(object_points, image_points, camera_matrix, dist_coeffs)
    t_e_c = get_transform(config, "T_E_C")
    t_b_e = as_matrix4(t_b_e, "T_B_E")
    t_b_c = t_b_e @ t_e_c
    t_b_m = t_b_c @ t_c_m
    return {
        "T_C_M": t_c_m,
        "T_B_C": t_b_c,
        "T_B_M": t_b_m,
        "image_points": image_points,
        "object_points": object_points,
    }


def compute_marker_in_base_from_image(
    config: dict,
    image,
    end_effector_pose,
    visualization_path: str | None = None,
) -> dict:
    h, w = image.shape[:2]
    markers = find_l_inner_corners(
        image,
        save_visualization=bool(visualization_path),
        verbose=False,
        vis_path=visualization_path,
    )
    image_points = get_detected_image_points(markers)
    t_b_e = pose_to_transform(
        end_effector_pose,
        rotation_type=config.get("pose_rotation_type", "rpy"),
        angle_unit=config.get("pose_angle_unit", "rad"),
    )
    result = compute_marker_in_base_from_points(
        config,
        image_points,
        t_b_e,
        image_size=(w, h),
    )
    result["markers"] = markers
    result["image_size"] = [w, h]
    return result


def yaw_from_rotation(rotation: np.ndarray) -> float:
    return float(np.arctan2(rotation[1, 0], rotation[0, 0]))


def marker_pose_to_planar_transform(marker_pose) -> np.ndarray:
    marker_pose = as_matrix4(marker_pose)
    yaw = yaw_from_rotation(marker_pose[:3, :3])
    cos_yaw = np.cos(yaw)
    sin_yaw = np.sin(yaw)
    planar = np.eye(4, dtype=np.float64)
    planar[:2, :2] = [[cos_yaw, -sin_yaw], [sin_yaw, cos_yaw]]
    planar[0, 3] = marker_pose[0, 3]
    planar[1, 3] = marker_pose[1, 3]
    return planar


def constrain_planar_delta(t_delta_planar: np.ndarray, constraint: str) -> np.ndarray:
    if constraint == "none":
        return t_delta_planar
    constrained = t_delta_planar.copy()
    if constraint in ("x-only", "y-only"):
        constrained[:2, :2] = np.eye(2)
    if constraint == "x-only":
        constrained[1, 3] = 0.0
    elif constraint == "y-only":
        constrained[0, 3] = 0.0
    elif constraint == "translation-only":
        constrained[:2, :2] = np.eye(2)
    else:
        raise ValueError(f"Unsupported planar constraint: {constraint}")
    return constrained


def apply_planar_compensation(
    t_b0_m,
    t_b1_m,
    t_b0_g,
    planar_constraint: str = "none",
) -> tuple[np.ndarray, np.ndarray]:
    t_b0_m_planar = marker_pose_to_planar_transform(t_b0_m)
    t_b1_m_planar = marker_pose_to_planar_transform(t_b1_m)
    t_delta_planar = t_b1_m_planar @ invert_transform(t_b0_m_planar)
    t_delta_planar = constrain_planar_delta(t_delta_planar, planar_constraint)
    t_b1_g_planar = t_delta_planar @ as_matrix4(t_b0_g, "T_B0_G")
    taught_pose = transform_to_pose(t_b0_g, rotation_type="rotvec", angle_unit="rad")
    planar_pose = transform_to_pose(t_b1_g_planar, rotation_type="rotvec", angle_unit="rad")
    planar_pose[2] = taught_pose[2]
    planar_pose[3] = taught_pose[3]
    planar_pose[4] = taught_pose[4]
    return pose_to_transform(planar_pose, rotation_type="rotvec", angle_unit="rad"), t_delta_planar


def compensate_taught_pose(
    taught_pose,
    teach_marker_pose,
    current_marker_pose,
    config: dict,
    mode: str = "planar",
    planar_constraint: str = "none",
) -> list[float]:
    rotation_type = config.get("pose_rotation_type", "rpy")
    angle_unit = config.get("pose_angle_unit", "rad")
    t_b0_m = as_matrix4(teach_marker_pose, "T_B0_M")
    t_b1_m = as_matrix4(current_marker_pose, "T_B1_M")
    t_b0_g = pose_to_transform(taught_pose, rotation_type=rotation_type, angle_unit=angle_unit)

    if mode == "planar":
        t_b1_g, _ = apply_planar_compensation(
            t_b0_m,
            t_b1_m,
            t_b0_g,
            planar_constraint=planar_constraint,
        )
    elif mode == "full-6d":
        t_m_g = invert_transform(t_b0_m) @ t_b0_g
        t_b1_g = t_b1_m @ t_m_g
    else:
        raise ValueError(f"Unsupported visual compensation mode: {mode}")

    return transform_to_pose(t_b1_g, rotation_type=rotation_type, angle_unit=angle_unit)

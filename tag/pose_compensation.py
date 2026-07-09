import argparse
import difflib
import json
from pathlib import Path

import cv2
import numpy as np

from detect_L_inner import find_l_inner_corners


DEFAULT_POINT_ORDER = ["top_left", "top_right", "bottom_right", "bottom_left"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def validate_image_path(image_path):
    path = Path(image_path)
    if path.is_file():
        return str(path)

    search_dir = path.parent if str(path.parent) not in ("", ".") else Path(".")
    candidates = []
    if search_dir.exists():
        candidates = [
            item.name
            for item in search_dir.iterdir()
            if item.is_file()
            and item.suffix.lower() in IMAGE_EXTENSIONS
            and "_L_inner_corners" not in item.stem
        ]

    matches = difflib.get_close_matches(path.name, candidates, n=3, cutoff=0.45)
    hint = f" Did you mean: {', '.join(matches)}?" if matches else ""
    raise FileNotFoundError(f"Image file not found: {image_path}.{hint}")


def read_image_size(image_path):
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")
    h, w = img.shape[:2]
    return (w, h)


def as_matrix4(value, name):
    mat = np.asarray(value, dtype=np.float64)
    if mat.shape != (4, 4):
        raise ValueError(f"{name} must be a 4x4 matrix, got shape {mat.shape}")
    return mat


def invert_transform(transform):
    transform = as_matrix4(transform, "transform")
    inv = np.eye(4, dtype=np.float64)
    r = transform[:3, :3]
    t = transform[:3, 3]
    inv[:3, :3] = r.T
    inv[:3, 3] = -r.T @ t
    return inv


def transform_to_xyz_rvec(transform):
    transform = as_matrix4(transform, "transform")
    rvec, _ = cv2.Rodrigues(transform[:3, :3])
    return {
        "x": float(transform[0, 3]),
        "y": float(transform[1, 3]),
        "z": float(transform[2, 3]),
        "rx": float(rvec[0, 0]),
        "ry": float(rvec[1, 0]),
        "rz": float(rvec[2, 0]),
    }


def get_pose_number(pose, key):
    for candidate in (key, key.lower(), key.upper()):
        if candidate in pose:
            return float(pose[candidate])
    raise ValueError(f"pose is missing key: {key}")


def normalize_angle_values(values, angle_unit):
    if angle_unit.lower() in ("deg", "degree", "degrees"):
        return np.deg2rad(values)
    if angle_unit.lower() in ("rad", "radian", "radians"):
        return values
    raise ValueError(f"Unsupported angle unit: {angle_unit}")


def rotation_from_rpy(roll, pitch, yaw):
    cx, sx = np.cos(roll), np.sin(roll)
    cy, sy = np.cos(pitch), np.sin(pitch)
    cz, sz = np.cos(yaw), np.sin(yaw)

    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rz @ ry @ rx


def rpy_from_rotation(rotation):
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


def pose_xyz_to_transform(pose, default_rotation_type="rotvec", default_angle_unit="rad"):
    xyz = np.array(
        [
            get_pose_number(pose, "x"),
            get_pose_number(pose, "y"),
            get_pose_number(pose, "z"),
        ],
        dtype=np.float64,
    )
    angles = np.array(
        [
            get_pose_number(pose, "rx"),
            get_pose_number(pose, "ry"),
            get_pose_number(pose, "rz"),
        ],
        dtype=np.float64,
    )

    rotation_type = pose.get("rotation_type", default_rotation_type).lower()
    angle_unit = pose.get("angle_unit", default_angle_unit).lower()
    angles = normalize_angle_values(angles, angle_unit)

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


def pose_xyz_rvec_to_transform(pose):
    return pose_xyz_to_transform(pose, default_rotation_type="rotvec", default_angle_unit="rad")


def get_pose_rotation_type(config):
    return config.get("pose_rotation_type", "rotvec")


def get_pose_angle_unit(config):
    return config.get("pose_angle_unit", "rad")


def transform_to_robot_pose(transform, rotation_type="rotvec", angle_unit="rad"):
    transform = as_matrix4(transform, "transform")
    rotation_type = rotation_type.lower()
    angle_unit = angle_unit.lower()

    if rotation_type in ("rotvec", "rvec", "rodrigues", "axis_angle"):
        angles, _ = cv2.Rodrigues(transform[:3, :3])
        angles = angles.reshape(3)
    elif rotation_type in ("rpy", "euler", "roll_pitch_yaw"):
        angles = rpy_from_rotation(transform[:3, :3])
    else:
        raise ValueError(f"Unsupported rotation type: {rotation_type}")

    if angle_unit in ("deg", "degree", "degrees"):
        angles = np.rad2deg(angles)
    elif angle_unit not in ("rad", "radian", "radians"):
        raise ValueError(f"Unsupported angle unit: {angle_unit}")

    return {
        "x": float(transform[0, 3]),
        "y": float(transform[1, 3]),
        "z": float(transform[2, 3]),
        "RX": float(angles[0]),
        "RY": float(angles[1]),
        "RZ": float(angles[2]),
    }


def yaw_from_rotation(rotation):
    return float(np.arctan2(rotation[1, 0], rotation[0, 0]))


def planarize_delta_transform(delta_transform):
    delta_transform = as_matrix4(delta_transform, "delta_transform")
    yaw = yaw_from_rotation(delta_transform[:3, :3])
    cos_yaw = np.cos(yaw)
    sin_yaw = np.sin(yaw)

    planar = np.eye(4, dtype=np.float64)
    planar[:2, :2] = [
        [cos_yaw, -sin_yaw],
        [sin_yaw, cos_yaw],
    ]
    planar[0, 3] = delta_transform[0, 3]
    planar[1, 3] = delta_transform[1, 3]
    return planar


def marker_pose_to_planar_transform(marker_pose):
    marker_pose = as_matrix4(marker_pose, "marker_pose")
    yaw = yaw_from_rotation(marker_pose[:3, :3])
    cos_yaw = np.cos(yaw)
    sin_yaw = np.sin(yaw)

    planar = np.eye(4, dtype=np.float64)
    planar[:2, :2] = [
        [cos_yaw, -sin_yaw],
        [sin_yaw, cos_yaw],
    ]
    planar[0, 3] = marker_pose[0, 3]
    planar[1, 3] = marker_pose[1, 3]
    return planar


def constrain_planar_delta(t_delta_planar, constraint):
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


def apply_planar_compensation(t_b0_m, t_b1_m, t_b0_g, planar_constraint="none"):
    t_b0_m_planar = marker_pose_to_planar_transform(t_b0_m)
    t_b1_m_planar = marker_pose_to_planar_transform(t_b1_m)
    t_delta_planar = t_b1_m_planar @ invert_transform(t_b0_m_planar)
    t_delta_planar = constrain_planar_delta(t_delta_planar, planar_constraint)
    t_b1_g_planar = t_delta_planar @ t_b0_g

    taught_pose = transform_to_xyz_rvec(t_b0_g)
    planar_pose = transform_to_xyz_rvec(t_b1_g_planar)
    planar_pose["z"] = taught_pose["z"]
    planar_pose["rx"] = taught_pose["rx"]
    planar_pose["ry"] = taught_pose["ry"]

    return pose_xyz_rvec_to_transform(planar_pose), t_delta_planar


def get_transform(config, key):
    if key in config:
        value = config[key]
        if isinstance(value, dict):
            return pose_xyz_to_transform(
                value,
                default_rotation_type=get_pose_rotation_type(config),
                default_angle_unit=get_pose_angle_unit(config),
            )
        return as_matrix4(value, key)

    pose_key = f"{key}_xyz_rvec"
    if pose_key in config:
        return pose_xyz_to_transform(
            config[pose_key],
            default_rotation_type="rotvec",
            default_angle_unit=get_pose_angle_unit(config),
        )

    pose_key = f"{key}_pose"
    if pose_key in config:
        return pose_xyz_to_transform(
            config[pose_key],
            default_rotation_type=get_pose_rotation_type(config),
            default_angle_unit=get_pose_angle_unit(config),
        )

    raise KeyError(f"config must contain {key}, {key}_xyz_rvec, or {key}_pose")


def get_object_points(config):
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


def get_marker_extent(config):
    marker = config.get("marker", {})
    if "points" in marker:
        points = np.asarray(marker["points"], dtype=np.float64)
        return float(np.max(np.ptp(points[:, :2], axis=0)))
    if "width" in marker and "height" in marker:
        return max(abs(float(marker["width"])), abs(float(marker["height"])))
    return 0.0


def get_config_pose_translation_extent(config, key):
    if key not in config:
        return None
    value = config[key]
    if isinstance(value, dict):
        return max(
            abs(get_pose_number(value, "x")),
            abs(get_pose_number(value, "y")),
            abs(get_pose_number(value, "z")),
        )
    matrix = as_matrix4(value, key)
    return float(np.max(np.abs(matrix[:3, 3])))


def get_camera_matrix(config, image_size=None):
    camera_matrix = np.asarray(config["camera_matrix"], dtype=np.float64)
    source_size = config.get("camera_matrix_resolution")
    if source_size is None or image_size is None:
        return camera_matrix

    source_w, source_h = float(source_size[0]), float(source_size[1])
    image_w, image_h = float(image_size[0]), float(image_size[1])
    if source_w <= 0 or source_h <= 0:
        raise ValueError("camera_matrix_resolution must be [width, height]")

    scale_x = image_w / source_w
    scale_y = image_h / source_h
    scaled = camera_matrix.copy()
    scaled[0, 0] *= scale_x
    scaled[0, 2] *= scale_x
    scaled[1, 1] *= scale_y
    scaled[1, 2] *= scale_y
    return scaled


def warn_if_camera_matrix_mismatch(config, image_size):
    if "camera_matrix_resolution" in config:
        return

    camera_matrix = np.asarray(config["camera_matrix"], dtype=np.float64)
    image_w, image_h = image_size
    cx_ratio = camera_matrix[0, 2] / image_w
    cy_ratio = camera_matrix[1, 2] / image_h
    if not (0.4 <= cx_ratio <= 0.6 and 0.4 <= cy_ratio <= 0.6):
        print(
            "WARNING: camera_matrix principal point is far from the image center. "
            f"image_size={image_size}, cx={camera_matrix[0, 2]:.3f}, "
            f"cy={camera_matrix[1, 2]:.3f}. If the intrinsics were calibrated "
            "at another resolution, add camera_matrix_resolution to the config."
        )


def validate_config_units(config):
    marker_extent = get_marker_extent(config)
    pose_extents = [
        extent
        for extent in (
            get_config_pose_translation_extent(config, "T_B_E_fixed"),
            get_config_pose_translation_extent(config, "T_B_E_teach"),
            get_config_pose_translation_extent(config, "T_B_E_run"),
            get_config_pose_translation_extent(config, "T_B_G"),
        )
        if extent is not None
    ]
    if not pose_extents:
        return

    pose_extent = max(pose_extents)
    if marker_extent > 10.0 and pose_extent < 10.0:
        raise ValueError(
            "Unit mismatch: robot poses look like meters, but marker width/height "
            f"is {marker_extent}. If the marker is 158 mm wide, write 0.158 when "
            "robot poses are in meters."
        )


def get_image_points(corners_data, image_name):
    if image_name not in corners_data:
        available = ", ".join(corners_data.keys())
        raise KeyError(f"{image_name!r} not found in corners file. Available: {available}")

    markers = corners_data[image_name]["markers"]
    if len(markers) != 4:
        raise ValueError(f"{image_name} must contain exactly 4 markers, got {len(markers)}")

    by_order = {m.get("order"): m for m in markers if "order" in m}
    if all(name in by_order for name in DEFAULT_POINT_ORDER):
        ordered = [by_order[name] for name in DEFAULT_POINT_ORDER]
    else:
        ordered = markers

    return np.asarray([m["inner_corner"] for m in ordered], dtype=np.float64)


def get_detected_image_points(markers, image_name):
    if markers is None or len(markers) != 4:
        count = 0 if markers is None else len(markers)
        raise ValueError(f"{image_name} must contain exactly 4 detected markers, got {count}")

    by_order = {m.get("order_name"): m for m in markers if "order_name" in m}
    if all(name in by_order for name in DEFAULT_POINT_ORDER):
        ordered = [by_order[name] for name in DEFAULT_POINT_ORDER]
    else:
        ordered = markers

    return np.asarray([m["inner_corner_refined"] for m in ordered], dtype=np.float64)


def solve_marker_pose(object_points, image_points, camera_matrix, dist_coeffs):
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


def compute_marker_in_base_from_points(config, image_points, t_b_e, image_size=None):
    camera_matrix = get_camera_matrix(config, image_size=image_size)
    dist_coeffs = np.asarray(config.get("dist_coeffs", []), dtype=np.float64)
    object_points = get_object_points(config)

    t_c_m = solve_marker_pose(object_points, image_points, camera_matrix, dist_coeffs)
    t_e_c = get_transform(config, "T_E_C")
    t_b_c = t_b_e @ t_e_c
    t_b_m = t_b_c @ t_c_m

    return {
        "T_C_M": t_c_m,
        "T_B_C": t_b_c,
        "T_B_M": t_b_m,
        "image_points": image_points,
        "object_points": object_points,
    }


def compute_marker_in_base(config, corners_data, image_name, t_b_e_key="T_B_E_fixed"):
    image_points = get_image_points(corners_data, image_name)
    image_size = corners_data[image_name].get("image_size")
    t_b_e = get_transform(config, t_b_e_key)
    return compute_marker_in_base_from_points(config, image_points, t_b_e, image_size=image_size)


def has_transform(config, key):
    return key in config or f"{key}_xyz_rvec" in config


def get_photo_transform(config, preferred_key):
    if has_transform(config, preferred_key):
        return get_transform(config, preferred_key)
    return get_transform(config, "T_B_E_fixed")


def get_grasp_transform(config):
    if has_transform(config, "T_B_G") or "T_B_G_pose" in config:
        return get_transform(config, "T_B_G")
    raise KeyError("teach config must contain T_B_G, T_B_G_xyz_rvec, or T_B_G_pose")


def teach(args):
    config = load_json(args.config)
    validate_config_units(config)
    corners = load_json(args.corners)
    marker_pose = compute_marker_in_base(config, corners, args.image)
    t_b_g = get_grasp_transform(config)
    t_m_g = invert_transform(marker_pose["T_B_M"]) @ t_b_g

    result = {
        "source_image": args.image,
        "point_order": DEFAULT_POINT_ORDER,
        "T_B0_M": marker_pose["T_B_M"].tolist(),
        "T_B0_G": t_b_g.tolist(),
        "T_M_G": t_m_g.tolist(),
        "T_M_G_xyz_rvec": transform_to_xyz_rvec(t_m_g),
        "T_C0_M": marker_pose["T_C_M"].tolist(),
    }
    save_json(args.out, result)
    print(f"Saved taught grasp relation: {args.out}")
    print(json.dumps({"T_M_G_xyz_rvec": result["T_M_G_xyz_rvec"]}, indent=2))


def run(args):
    config = load_json(args.config)
    validate_config_units(config)
    corners = load_json(args.corners)
    taught = load_json(args.teach_file)
    marker_pose = compute_marker_in_base(config, corners, args.image)
    t_m_g = as_matrix4(taught["T_M_G"], "T_M_G")

    if args.mode == "planar":
        t_b0_m = as_matrix4(taught["T_B0_M"], "T_B0_M")
        t_b0_g = as_matrix4(taught["T_B0_G"], "T_B0_G")
        t_b_g, t_delta_planar = apply_planar_compensation(
            t_b0_m,
            marker_pose["T_B_M"],
            t_b0_g,
            planar_constraint=args.planar_constraint,
        )
    else:
        t_b_g = marker_pose["T_B_M"] @ t_m_g
        t_delta_planar = None

    result = {
        "source_image": args.image,
        "compensation_mode": args.mode,
        "planar_constraint": args.planar_constraint,
        "T_B_M": marker_pose["T_B_M"].tolist(),
        "T_B_G": t_b_g.tolist(),
        "T_B_G_xyz_rvec": transform_to_xyz_rvec(t_b_g),
        "T_B_G_robot_pose": transform_to_robot_pose(
            t_b_g,
            get_pose_rotation_type(config),
            get_pose_angle_unit(config),
        ),
        "T_C_M": marker_pose["T_C_M"].tolist(),
    }
    if t_delta_planar is not None:
        result["T_delta_planar"] = t_delta_planar.tolist()
    save_json(args.out, result)
    print(f"Saved compensated grasp pose: {args.out}")
    print(json.dumps({"T_B_G_robot_pose": result["T_B_G_robot_pose"]}, indent=2))


def from_images(args):
    config = load_json(args.config)
    validate_config_units(config)
    teach_image = validate_image_path(args.teach_image)
    run_image = validate_image_path(args.run_image)
    teach_image_size = read_image_size(teach_image)
    run_image_size = read_image_size(run_image)
    warn_if_camera_matrix_mismatch(config, teach_image_size)
    warn_if_camera_matrix_mismatch(config, run_image_size)

    teach_markers = find_l_inner_corners(
        teach_image,
        save_visualization=not args.no_vis,
        verbose=not args.quiet,
    )
    run_markers = find_l_inner_corners(
        run_image,
        save_visualization=not args.no_vis,
        verbose=not args.quiet,
    )

    teach_image_points = get_detected_image_points(teach_markers, teach_image)
    run_image_points = get_detected_image_points(run_markers, run_image)

    t_b_e_teach = get_photo_transform(config, "T_B_E_teach")
    t_b_e_run = get_photo_transform(config, "T_B_E_run")

    teach_marker_pose = compute_marker_in_base_from_points(
        config,
        teach_image_points,
        t_b_e_teach,
        image_size=teach_image_size,
    )
    run_marker_pose = compute_marker_in_base_from_points(
        config,
        run_image_points,
        t_b_e_run,
        image_size=run_image_size,
    )

    t_b0_g = get_grasp_transform(config)
    t_m_g = invert_transform(teach_marker_pose["T_B_M"]) @ t_b0_g

    if args.mode == "planar":
        t_b1_g, t_delta_planar = apply_planar_compensation(
            teach_marker_pose["T_B_M"],
            run_marker_pose["T_B_M"],
            t_b0_g,
            planar_constraint=args.planar_constraint,
        )
    else:
        t_b1_g = run_marker_pose["T_B_M"] @ t_m_g
        t_delta_planar = None

    result = {
        "teach_image": str(teach_image),
        "run_image": str(run_image),
        "teach_image_size": list(teach_image_size),
        "run_image_size": list(run_image_size),
        "compensation_mode": args.mode,
        "planar_constraint": args.planar_constraint,
        "point_order": DEFAULT_POINT_ORDER,
        "teach_image_points": teach_image_points.tolist(),
        "run_image_points": run_image_points.tolist(),
        "T_B0_M": teach_marker_pose["T_B_M"].tolist(),
        "T_C0_M": teach_marker_pose["T_C_M"].tolist(),
        "T_B0_G": t_b0_g.tolist(),
        "T_M_G": t_m_g.tolist(),
        "T_B1_M": run_marker_pose["T_B_M"].tolist(),
        "T_C1_M": run_marker_pose["T_C_M"].tolist(),
        "T_B1_G": t_b1_g.tolist(),
        "T_B1_G_xyz_rvec": transform_to_xyz_rvec(t_b1_g),
        "T_B1_G_robot_pose": transform_to_robot_pose(
            t_b1_g,
            get_pose_rotation_type(config),
            get_pose_angle_unit(config),
        ),
    }
    if t_delta_planar is not None:
        result["T_delta_planar"] = t_delta_planar.tolist()
    save_json(args.out, result)

    print(f"Saved compensated grasp pose: {args.out}")
    print(json.dumps({"T_B1_G_robot_pose": result["T_B1_G_robot_pose"]}, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Compute marker-based robot grasp pose compensation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    teach_parser = subparsers.add_parser("teach", help="Save T_M_G from first teaching.")
    teach_parser.add_argument("--config", required=True, help="Calibration and grasp config JSON.")
    teach_parser.add_argument("--corners", default="L_inner_corners.json", help="Detected corner JSON.")
    teach_parser.add_argument("--image", required=True, help="Image key in the corner JSON.")
    teach_parser.add_argument("--out", default="taught_grasp.json", help="Output taught grasp JSON.")
    teach_parser.set_defaults(func=teach)

    run_parser = subparsers.add_parser("run", help="Compute compensated T_B_G for a new image.")
    run_parser.add_argument("--config", required=True, help="Calibration config JSON.")
    run_parser.add_argument("--corners", default="L_inner_corners.json", help="Detected corner JSON.")
    run_parser.add_argument("--image", required=True, help="Image key in the corner JSON.")
    run_parser.add_argument("--teach-file", default="taught_grasp.json", help="Taught grasp JSON.")
    run_parser.add_argument("--out", default="compensated_grasp.json", help="Output compensated pose JSON.")
    run_parser.add_argument(
        "--mode",
        choices=["planar", "full-6d"],
        default="planar",
        help="planar keeps taught z/rx/ry and only compensates x/y/rz.",
    )
    run_parser.add_argument(
        "--planar-constraint",
        choices=["none", "x-only", "y-only", "translation-only"],
        default="none",
        help="Optional constraint for planar mode. y-only keeps x/rz unchanged.",
    )
    run_parser.set_defaults(func=run)

    images_parser = subparsers.add_parser(
        "from-images",
        help="Detect both images and output the compensated grasp pose in one step.",
    )
    images_parser.add_argument("--config", required=True, help="Calibration and teaching config JSON.")
    images_parser.add_argument("--teach-image", required=True, help="First teaching image.")
    images_parser.add_argument("--run-image", required=True, help="Image captured after the robot moved.")
    images_parser.add_argument("--out", default="compensated_grasp.json", help="Output compensated pose JSON.")
    images_parser.add_argument(
        "--mode",
        choices=["planar", "full-6d"],
        default="planar",
        help="planar keeps taught z/rx/ry and only compensates x/y/rz.",
    )
    images_parser.add_argument(
        "--planar-constraint",
        choices=["none", "x-only", "y-only", "translation-only"],
        default="none",
        help="Optional constraint for planar mode. y-only keeps x/rz unchanged.",
    )
    images_parser.add_argument("--no-vis", action="store_true", help="Do not write corner visualization images.")
    images_parser.add_argument("--quiet", action="store_true", help="Only print the final compensated pose.")
    images_parser.set_defaults(func=from_images)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, ValueError, KeyError, RuntimeError) as exc:
        raise SystemExit(f"ERROR: {exc}") from None


if __name__ == "__main__":
    main()

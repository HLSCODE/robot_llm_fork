from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

import cv2
import numpy as np

from .detector import IMAGE_EXTENSIONS, detect_images, default_image_paths, expand_image_paths, find_l_inner_corners
from .geometry import (
    apply_planar_compensation,
    as_matrix4,
    compute_marker_in_base_from_points,
    get_detected_image_points,
    get_pose_angle_unit,
    get_pose_rotation_type,
    get_transform,
    invert_transform,
    transform_to_robot_pose,
    transform_to_xyz_rvec,
)


DEFAULT_POINT_ORDER = ["top_left", "top_right", "bottom_right", "bottom_left"]


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


def get_image_points(corners_data, image_name):
    if image_name not in corners_data:
        available = ", ".join(corners_data.keys())
        raise KeyError(f"{image_name!r} not found in corners file. Available: {available}")

    markers = corners_data[image_name]["markers"]
    if len(markers) != 4:
        raise ValueError(f"{image_name} must contain exactly 4 markers, got {len(markers)}")

    by_order = {marker.get("order"): marker for marker in markers if "order" in marker}
    if all(name in by_order for name in DEFAULT_POINT_ORDER):
        ordered = [by_order[name] for name in DEFAULT_POINT_ORDER]
    else:
        ordered = markers

    return np.asarray([marker["inner_corner"] for marker in ordered], dtype=np.float64)


def has_transform(config, key):
    return key in config or f"{key}_xyz_rvec" in config or f"{key}_pose" in config


def get_photo_transform(config, preferred_key):
    if has_transform(config, preferred_key):
        return get_transform(config, preferred_key)
    return get_transform(config, "T_B_E_fixed")


def get_grasp_transform(config):
    if has_transform(config, "T_B_G"):
        return get_transform(config, "T_B_G")
    raise KeyError("teach config must contain T_B_G, T_B_G_xyz_rvec, or T_B_G_pose")


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
        values = []
        for axis in ("x", "y", "z"):
            for candidate in (axis, axis.upper()):
                if candidate in value:
                    values.append(abs(float(value[candidate])))
                    break
        return max(values) if values else None
    matrix = as_matrix4(value, key)
    return float(np.max(np.abs(matrix[:3, 3])))


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


def compute_marker_in_base(config, corners_data, image_name, t_b_e_key="T_B_E_fixed"):
    image_points = get_image_points(corners_data, image_name)
    image_size = corners_data[image_name].get("image_size")
    t_b_e = get_transform(config, t_b_e_key)
    return compute_marker_in_base_from_points(config, image_points, t_b_e, image_size=image_size)


def detect(args):
    image_paths = expand_image_paths(args.paths) if args.paths else default_image_paths()
    if not image_paths:
        raise SystemExit("No image files found. Pass an image path or folder path.")
    detect_images(
        image_paths,
        output_json=args.out,
        save_visualization=not args.no_vis,
        vis_dir=args.vis_dir,
    )


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

    teach_image_points = get_detected_image_points(teach_markers)
    run_image_points = get_detected_image_points(run_markers)

    teach_marker_pose = compute_marker_in_base_from_points(
        config,
        teach_image_points,
        get_photo_transform(config, "T_B_E_teach"),
        image_size=teach_image_size,
    )
    run_marker_pose = compute_marker_in_base_from_points(
        config,
        run_image_points,
        get_photo_transform(config, "T_B_E_run"),
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
    parser = argparse.ArgumentParser(description="Vision relocalization tag tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect", help="Detect inner corners of blue L markers.")
    detect_parser.add_argument("paths", nargs="*", help="Image files or folders to process.")
    detect_parser.add_argument("--out", default="L_inner_corners.json", help="Output corner JSON.")
    detect_parser.add_argument("--vis-dir", default=None, help="Optional folder for visualization images.")
    detect_parser.add_argument("--no-vis", action="store_true", help="Do not write visualization images.")
    detect_parser.set_defaults(func=detect)

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
    run_parser.add_argument("--mode", choices=["planar", "full-6d"], default="planar")
    run_parser.add_argument(
        "--planar-constraint",
        choices=["none", "x-only", "y-only", "translation-only"],
        default="none",
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
    images_parser.add_argument("--mode", choices=["planar", "full-6d"], default="planar")
    images_parser.add_argument(
        "--planar-constraint",
        choices=["none", "x-only", "y-only", "translation-only"],
        default="none",
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

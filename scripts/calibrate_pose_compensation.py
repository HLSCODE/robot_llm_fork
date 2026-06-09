from __future__ import annotations

import argparse
import ast
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CM_TO_M = 0.01
POSE_LENGTH = 6

# Arm axes relative to the robot body/localization axes:
# body +X = forward, body +Y = left
# arm  +X = left,    arm  +Y = back
BODY_FROM_ARM_3 = [
    [0.0, -1.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
]
ARM_FROM_BODY_3 = [
    [0.0, 1.0, 0.0],
    [-1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
]

TEMPLATE = """{
  "taught_pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "teach_offset": {"x": 0.0, "y": 0.0, "angle": 0.0},
  "samples": [
    {
      "name": "rotate_cw_10deg",
      "current_offset": {"x": 0.0, "y": 0.0, "angle": 10.0},
      "correct_pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    },
    {
      "name": "move_and_rotate",
      "current_offset": {"x": 10.0, "y": -5.0, "angle": 8.0},
      "correct_pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    }
  ]
}
"""


@dataclass(frozen=True)
class Sample:
    name: str
    taught_pose: list[float]
    teach_offset: dict
    current_offset: dict
    correct_pose: list[float]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fit locator-to-arm-base offset from manual pose-compensation samples."
        )
    )
    parser.add_argument(
        "samples",
        nargs="?",
        default=Path(r"C:\HLE\Work\robot_llm-fork\scripts\data.json"),
        type=Path,
        help="JSON file containing taught pose and calibration samples.",
)
    parser.add_argument(
        "--template",
        action="store_true",
        help="Print an input JSON template and exit.",
    )
    parser.add_argument(
        "--angle-sign",
        choices=("both", "cw-positive", "ccw-positive"),
        default="cw-positive",
        help=(
            "How UDP angle maps to math rotation. Use both to compare both directions."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    args = parser.parse_args()

    if args.template:
        print(TEMPLATE)
        return 0

    if args.samples is None:
        parser.error("provide a samples JSON file, or use --template")

    samples = load_samples(args.samples)
    sign_options = angle_sign_options(args.angle_sign)
    results = [fit_samples(samples, sign, label) for sign, label in sign_options]
    best = min(results, key=lambda item: item["rms_error_cm"])

    if args.json:
        print(json.dumps({"best": best, "all_results": results}, indent=2))
    else:
        print_report(best, results)
    return 0


def load_samples(path: Path) -> list[Sample]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        entries = raw
        default_taught_pose = None
        default_teach_offset = None
    else:
        entries = raw.get("samples", [])
        default_taught_pose = raw.get("taught_pose")
        default_teach_offset = raw.get("teach_offset")

    samples: list[Sample] = []
    for index, entry in enumerate(entries, start=1):
        taught_pose = entry.get("taught_pose", default_taught_pose)
        teach_offset = entry.get("teach_offset", default_teach_offset)
        current_offset = entry.get("current_offset")
        correct_pose = (
            entry.get("correct_pose")
            or entry.get("manual_pose")
            or entry.get("target_pose")
        )

        if taught_pose is None:
            raise ValueError(f"sample {index}: missing taught_pose")
        if teach_offset is None:
            raise ValueError(f"sample {index}: missing teach_offset")
        if current_offset is None:
            raise ValueError(f"sample {index}: missing current_offset")
        if correct_pose is None:
            raise ValueError(f"sample {index}: missing correct_pose")

        samples.append(
            Sample(
                name=str(entry.get("name", f"sample_{index}")),
                taught_pose=parse_pose(taught_pose),
                teach_offset=dict(teach_offset),
                current_offset=dict(current_offset),
                correct_pose=parse_pose(correct_pose),
            )
        )

    if not samples:
        raise ValueError("no samples found")
    return samples


def angle_sign_options(value: str) -> list[tuple[float, str]]:
    if value == "cw-positive":
        return [(-1.0, "cw-positive")]
    if value == "ccw-positive":
        return [(1.0, "ccw-positive")]
    return [(-1.0, "cw-positive"), (1.0, "ccw-positive")]


def fit_samples(samples: list[Sample], angle_sign: float, label: str) -> dict:
    rows: list[tuple[float, float]] = []
    values: list[float] = []

    for sample in samples:
        a_rows, rhs = sample_equations(sample, angle_sign)
        rows.extend(a_rows)
        values.extend(rhs)

    offset_m = solve_2d_least_squares(rows, values)
    residuals = [
        sample_residual(sample, offset_m, angle_sign) for sample in samples
    ]
    rms_error_cm = math.sqrt(
        sum(item["position_error_cm"] ** 2 for item in residuals)
        / len(residuals)
    )

    return {
        "angle_sign": label,
        "math_theta": "-angle" if angle_sign < 0.0 else "+angle",
        "locator_to_arm_base_cm": {
            "x_forward": round(offset_m[0] / CM_TO_M, 6),
            "y_left": round(offset_m[1] / CM_TO_M, 6),
        },
        "rms_error_cm": round(rms_error_cm, 6),
        "samples": residuals,
    }


def sample_equations(
    sample: Sample,
    angle_sign: float,
) -> tuple[list[tuple[float, float]], list[float]]:
    theta_t = localization_theta_rad(sample.teach_offset, angle_sign)
    theta_c = localization_theta_rad(sample.current_offset, angle_sign)
    rot_t = rot2(theta_t)
    rot_c = rot2(theta_c)
    body_rel = mat2_mul(transpose2(rot_c), rot_t)
    taught_tcp_body = arm_xy_to_body_xy(sample.taught_pose[:2])
    correct_tcp_body = arm_xy_to_body_xy(sample.correct_pose[:2])
    locator_delta_body = (
        (_offset_value(sample.current_offset, "x") - _offset_value(sample.teach_offset, "x"))
        * CM_TO_M,
        (_offset_value(sample.current_offset, "y") - _offset_value(sample.teach_offset, "y"))
        * CM_TO_M,
    )

    # current_tcp_body = R_current^-1 * R_teach * (base_offset + taught_tcp_body)
    #                    - base_offset - locator_delta_body
    # (body_rel - I) * base_offset =
    # current_tcp_body - body_rel * taught_tcp_body + locator_delta_body
    a = mat2_sub(body_rel, [[1.0, 0.0], [0.0, 1.0]])
    rhs = vec2_add(
        vec2_sub(correct_tcp_body, mat2_vec(body_rel, taught_tcp_body)),
        locator_delta_body,
    )

    return [(a[0][0], a[0][1]), (a[1][0], a[1][1])], [rhs[0], rhs[1]]


def sample_residual(sample: Sample, offset_m: tuple[float, float], angle_sign: float) -> dict:
    predicted_pose = predict_current_pose(sample, offset_m, angle_sign)
    dx_cm = (predicted_pose[0] - sample.correct_pose[0]) / CM_TO_M
    dy_cm = (predicted_pose[1] - sample.correct_pose[1]) / CM_TO_M
    dz_cm = (predicted_pose[2] - sample.correct_pose[2]) / CM_TO_M
    dr = [
        wrap_angle(predicted_pose[i] - sample.correct_pose[i])
        for i in range(3, POSE_LENGTH)
    ]
    position_error_cm = math.sqrt(dx_cm * dx_cm + dy_cm * dy_cm)

    return {
        "name": sample.name,
        "predicted_pose": [round(v, 6) for v in predicted_pose],
        "correct_pose": [round(v, 6) for v in sample.correct_pose],
        "position_error_cm": round(position_error_cm, 6),
        "dx_cm": round(dx_cm, 6),
        "dy_cm": round(dy_cm, 6),
        "dz_cm": round(dz_cm, 6),
        "orientation_delta_rad": [round(v, 6) for v in dr],
    }


def predict_current_pose(
    sample: Sample,
    offset_m: tuple[float, float],
    angle_sign: float,
) -> list[float]: 
    theta_t = localization_theta_rad(sample.teach_offset, angle_sign)
    theta_c = localization_theta_rad(sample.current_offset, angle_sign)
    rot_t = rot2(theta_t)
    rot_c = rot2(theta_c)
    body_rel = mat2_mul(transpose2(rot_c), rot_t)

    taught_tcp_body = arm_xy_to_body_xy(sample.taught_pose[:2])
    locator_delta_body = (
        (_offset_value(sample.current_offset, "x") - _offset_value(sample.teach_offset, "x"))
        * CM_TO_M,
        (_offset_value(sample.current_offset, "y") - _offset_value(sample.teach_offset, "y"))
        * CM_TO_M,
    )
    current_tcp_body = vec2_sub(
        vec2_sub(mat2_vec(body_rel, vec2_add(offset_m, taught_tcp_body)), offset_m),
        locator_delta_body,
    )
    current_tcp_arm = body_xy_to_arm_xy(current_tcp_body)

    current_euler = predict_current_euler(sample, theta_t, theta_c)
    return [
        current_tcp_arm[0],
        current_tcp_arm[1],
        sample.taught_pose[2],
        current_euler[0],
        current_euler[1],
        current_euler[2],
    ]


def predict_current_euler(sample: Sample, theta_t: float, theta_c: float) -> list[float]:
    world_from_body_t = zrot3(theta_t)
    world_from_body_c = zrot3(theta_c)
    arm_tcp_t = euler_xyz_to_matrix(*sample.taught_pose[3:6])

    world_tcp = mat3_mul(
        mat3_mul(world_from_body_t, BODY_FROM_ARM_3),
        arm_tcp_t,
    )
    arm_tcp_c = mat3_mul(
        mat3_mul(ARM_FROM_BODY_3, transpose3(world_from_body_c)),
        world_tcp,
    )
    return matrix_to_euler_xyz(arm_tcp_c)


def solve_2d_least_squares(
    rows: list[tuple[float, float]],
    values: list[float],
) -> tuple[float, float]:
    s00 = sum(row[0] * row[0] for row in rows)
    s01 = sum(row[0] * row[1] for row in rows)
    s11 = sum(row[1] * row[1] for row in rows)
    b0 = sum(row[0] * value for row, value in zip(rows, values))
    b1 = sum(row[1] * value for row, value in zip(rows, values))
    det = s00 * s11 - s01 * s01

    if abs(det) < 1e-12:
        raise ValueError(
            "cannot solve offset: samples need at least one non-zero angle change"
        )

    return ((b0 * s11 - b1 * s01) / det, (s00 * b1 - s01 * b0) / det)


def print_report(best: dict, results: list[dict]) -> None:
    print("Best result")
    print(f"  angle_sign: {best['angle_sign']} (math theta = {best['math_theta']})")
    print(
        "  locator_to_arm_base_cm: "
        f"x_forward={best['locator_to_arm_base_cm']['x_forward']:.6f}, "
        f"y_left={best['locator_to_arm_base_cm']['y_left']:.6f}"
    )
    print(f"  rms_error_cm: {best['rms_error_cm']:.6f}")
    print()

    if len(results) > 1:
        print("All angle-sign trials")
        for result in results:
            print(
                f"  {result['angle_sign']}: rms_error_cm={result['rms_error_cm']:.6f}, "
                f"offset_cm=({result['locator_to_arm_base_cm']['x_forward']:.6f}, "
                f"{result['locator_to_arm_base_cm']['y_left']:.6f})"
            )
        print()

    print("Samples")
    for item in best["samples"]:
        print(
            f"  {item['name']}: error={item['position_error_cm']:.6f}cm, "
            f"dx={item['dx_cm']:.6f}cm, dy={item['dy_cm']:.6f}cm"
        )
        print(f"    predicted_pose={item['predicted_pose']}")
        print(f"    correct_pose=  {item['correct_pose']}")
        print(f"    orientation_delta_rad={item['orientation_delta_rad']}")


def parse_pose(value) -> list[float]:
    if isinstance(value, (list, tuple)):
        pose = [float(v) for v in value]
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = ast.literal_eval(text)
        pose = [float(v) for v in parsed]
    else:
        raise TypeError(f"unsupported pose value: {type(value).__name__}")

    if len(pose) != POSE_LENGTH:
        raise ValueError(f"pose must contain {POSE_LENGTH} values, got {len(pose)}")
    return pose


def localization_xy_m(offset: dict) -> tuple[float, float]:
    return (_offset_value(offset, "x") * CM_TO_M, _offset_value(offset, "y") * CM_TO_M)


def localization_theta_rad(offset: dict, angle_sign: float) -> float:
    return angle_sign * math.radians(_offset_value(offset, "angle"))


def _offset_value(offset: dict, key: str) -> float:
    aliases = {
        "x": ("x", "X", "x_cm"),
        "y": ("y", "Y", "y_cm"),
        "angle": ("angle", "Angle", "angel", "Angel", "angle_deg"),
    }
    for alias in aliases[key]:
        if alias in offset:
            return float(offset[alias])
    raise KeyError(f"missing offset field: {key}")


def arm_xy_to_body_xy(point: Iterable[float]) -> tuple[float, float]:
    x_arm, y_arm = [float(v) for v in point]
    return (-y_arm, x_arm)


def body_xy_to_arm_xy(point: Iterable[float]) -> tuple[float, float]:
    x_body, y_body = [float(v) for v in point]
    return (y_body, -x_body)


def rot2(theta: float) -> list[list[float]]:
    c = math.cos(theta)
    s = math.sin(theta)
    return [[c, -s], [s, c]]


def transpose2(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[0][0], matrix[1][0]], [matrix[0][1], matrix[1][1]]]


def mat2_vec(matrix: list[list[float]], vector: tuple[float, float]) -> tuple[float, float]:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1],
    )


def mat2_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [
            a[0][0] * b[0][0] + a[0][1] * b[1][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1],
        ],
        [
            a[1][0] * b[0][0] + a[1][1] * b[1][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1],
        ],
    ]


def mat2_sub(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [a[0][0] - b[0][0], a[0][1] - b[0][1]],
        [a[1][0] - b[1][0], a[1][1] - b[1][1]],
    ]


def vec2_add(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0], a[1] + b[1])


def vec2_sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def zrot3(theta: float) -> list[list[float]]:
    c = math.cos(theta)
    s = math.sin(theta)
    return [
        [c, -s, 0.0],
        [s, c, 0.0],
        [0.0, 0.0, 1.0],
    ]


def mat3_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def transpose3(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[j][i] for j in range(3)] for i in range(3)]


def euler_xyz_to_matrix(rx: float, ry: float, rz: float) -> list[list[float]]:
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


def wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


if __name__ == "__main__":
    raise SystemExit(main())


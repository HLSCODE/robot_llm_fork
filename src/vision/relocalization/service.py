from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ...core.config_loader import Config
from ...core.execution_context import ExecutionContext, VisionRelocalizationState
from ...core.pose_compensation import parse_pose
from ...core.vision_station_storage import (
    VisionStationStorage,
    arm_display_name,
    normalize_arm_name,
)
from ...device_runtime import (
    ArmId,
    CartesianPose,
    MotionMode,
    RobotSystem,
)
from .geometry import compensate_taught_pose, compute_marker_in_base_from_image


LogFn = Callable[[str], None]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _project_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return _PROJECT_ROOT / value


def _matrix_to_list(matrix) -> list[list[float]]:
    return np.asarray(matrix, dtype=np.float64).tolist()


def _default_log(message: str) -> None:
    print(message)


_MARKER_WIDTH_KEYS = ("marker_width", "标定宽度", "marker宽度", "L型marker宽度")
_MARKER_HEIGHT_KEYS = ("marker_height", "标定高度", "marker高度", "L型marker高度")


def _read_float(mapping: dict | None, keys: tuple[str, ...]) -> float | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is None or value == "":
            continue
        return float(value)
    return None


def _normalize_marker(marker: dict | None) -> dict | None:
    if not isinstance(marker, dict):
        return None
    width = _read_float(marker, ("width", "w", *_MARKER_WIDTH_KEYS))
    height = _read_float(marker, ("height", "h", *_MARKER_HEIGHT_KEYS))
    if width is None or height is None:
        return None
    if width <= 0 or height <= 0:
        raise ValueError("L 型 marker 真实宽高必须大于 0")
    return {"width": width, "height": height}


def _marker_from_params(params: dict, fallback: dict | None = None) -> dict | None:
    marker = _normalize_marker(params.get("marker"))
    if marker is not None:
        return marker
    marker = _normalize_marker(params)
    if marker is not None:
        return marker
    return _normalize_marker(fallback)


def _marker_for_action(params: dict, arm: str, fallback: dict | None = None) -> dict:
    marker = _marker_from_params(params, fallback=fallback)
    if marker is None:
        raise ValueError("缺少 L 型 marker 真实宽高")
    return marker


def _config_for_marker(arm: str, marker: dict | None) -> dict:
    cfg = dict(Config.get_instance().get_vision_relocalization_config(arm))
    if marker is not None:
        cfg["marker"] = marker
    return cfg


def _camera_name_for_arm(arm: str, override: str | None = None) -> str:
    if override:
        return override
    config = Config.get_instance().get_vision_relocalization_config(arm)
    return config.get("camera_name", "")


def get_current_arm_pose(robot_system: RobotSystem, arm: str) -> list[float]:
    arm_id = ArmId.parse(normalize_arm_name(arm))
    return robot_system.read_arm_state(arm_id).pose.to_list()


def move_arm_to_pose(
    robot_system: RobotSystem,
    arm: str,
    pose: list[float],
    mode: str = "move_j",
) -> bool:
    robot_system.move_to_pose(
        ArmId.parse(normalize_arm_name(arm)),
        CartesianPose.from_iterable(pose),
        MotionMode.parse(mode),
    )
    return True


def _decode_jpeg(jpeg_bytes: bytes):
    data = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def capture_color_frame(
    camera,
    camera_name: str | None = None,
    timeout_seconds: float = 10.0,
):
    if camera is None:
        raise RuntimeError("相机管理器未启动")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if hasattr(camera, "get_latest_raw_frames"):
            raw = camera.get_latest_raw_frames(camera_name or None)
            if raw is not None:
                color, _depth, _intr = raw
                if color is not None:
                    return color

        if hasattr(camera, "get_latest_jpegs"):
            jpegs = camera.get_latest_jpegs()
            for serial, name, jpeg in jpegs:
                if camera_name and camera_name not in {serial, name}:
                    continue
                frame = _decode_jpeg(jpeg)
                if frame is not None:
                    return frame
            if jpegs and not camera_name:
                frame = _decode_jpeg(jpegs[0][2])
                if frame is not None:
                    return frame
        time.sleep(0.2)

    raise RuntimeError(f"相机取帧超时: {camera_name or '(auto)'}")


def _debug_paths(station_id: str, arm: str, suffix: str) -> tuple[Path | None, Path | None]:
    cfg = Config.get_instance().get_vision_relocalization_config(arm)
    if not cfg.get("save_debug_images", True):
        return None, None
    debug_dir = _project_path(cfg.get("debug_dir", "data/vision_stations/debug"))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = debug_dir / f"{station_id}_{normalize_arm_name(arm)}_{suffix}_{stamp}"
    return base.with_suffix(".jpg"), Path(f"{base}_corners.jpg")


def capture_marker_pose(
    controller,
    camera,
    station_id: str,
    arm: str,
    camera_name: str,
    suffix: str,
    marker: dict | None = None,
    log_fn: LogFn | None = None,
) -> dict:
    log = log_fn or _default_log
    cfg = _config_for_marker(arm, marker)
    image_path, vis_path = _debug_paths(station_id, arm, suffix)

    marker_size = cfg.get("marker", {})
    log(
        f"视觉重定位取帧: 工位={station_id}, {arm_display_name(arm)}, "
        f"相机={camera_name or '(auto)'}, marker={marker_size.get('width')} x {marker_size.get('height')}"
    )
    image = capture_color_frame(camera, camera_name or None)
    if image_path is not None:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(image_path), image)

    current_pose = get_current_arm_pose(controller, arm)
    marker_pose = compute_marker_in_base_from_image(
        cfg,
        image,
        current_pose,
        visualization_path=str(vis_path) if vis_path else None,
    )
    return {
        "T_B_M": _matrix_to_list(marker_pose["T_B_M"]),
        "T_C_M": _matrix_to_list(marker_pose["T_C_M"]),
        "T_B_C": _matrix_to_list(marker_pose["T_B_C"]),
        "image_points": np.asarray(marker_pose["image_points"]).tolist(),
        "image_size": marker_pose.get("image_size", []),
        "photo_pose": current_pose,
        "image_path": str(image_path) if image_path else "",
        "visualization_path": str(vis_path) if vis_path else "",
        "camera_name": camera_name,
        "marker": marker_size,
    }


def record_teach_profile(
    controller,
    camera,
    params: dict,
    log_fn: LogFn | None = None,
) -> dict:
    log = log_fn or _default_log
    raw_station_id = str(params.get("station_id") or params.get("工位ID") or "").strip()
    station_name = str(params.get("station_name") or params.get("工位名称") or raw_station_id).strip()
    if not station_name:
        raise ValueError("缺少工位名称")
    station_id = raw_station_id or station_name
    arm = normalize_arm_name(params.get("arm") or params.get("臂") or "left")
    camera_name = _camera_name_for_arm(arm, params.get("camera_name") or params.get("相机名称"))
    move_mode = params.get("move_mode") or params.get("移动模式") or "move_j"
    marker_size = _marker_for_action(params, arm)

    raw_pose = params.get("photo_pose") or params.get("拍照位姿") or ""
    photo_pose = parse_pose(raw_pose) if raw_pose else []
    if photo_pose:
        log(f"移动到示教拍照位: {station_name} / {arm_display_name(arm)}")
        if not move_arm_to_pose(controller, arm, photo_pose, mode=move_mode):
            raise RuntimeError("移动到示教拍照位失败")
    else:
        log("未填写拍照位姿，将使用当前机械臂位姿作为示教拍照位")

    marker = capture_marker_pose(
        controller,
        camera,
        station_id,
        arm,
        camera_name,
        "teach",
        marker_size,
        log,
    )
    profile = {
        "station_id": station_id,
        "station_name": station_name,
        "arm": arm,
        "camera_name": camera_name,
        "marker": marker_size,
        "photo_pose": photo_pose or marker["photo_pose"],
        "T_B0_M": marker["T_B_M"],
        "T_C0_M": marker["T_C_M"],
        "teach_image": marker["image_path"],
        "teach_visualization": marker["visualization_path"],
    }
    saved = VisionStationStorage.upsert_profile(profile)
    log(f"已保存视觉示教基准: {station_name} / {arm_display_name(arm)}")
    return saved


def execute_vision_relocalization(
    controller,
    camera,
    params: dict,
    context: ExecutionContext,
    log_fn: LogFn | None = None,
) -> bool:
    log = log_fn or _default_log
    action_mode = params.get("action_mode") or params.get("动作模式") or "run"
    station_id = str(
        params.get("station_id")
        or params.get("工位ID")
        or params.get("station_name")
        or params.get("工位名称")
        or ""
    ).strip()
    arm = normalize_arm_name(params.get("arm") or params.get("臂") or "left")

    if action_mode == "teach":
        profile = record_teach_profile(controller, camera, params, log)
        station_id = profile["station_id"]
        camera_name = profile.get("camera_name", "")
        context.set_vision_state(
            VisionRelocalizationState(
                station_id=station_id,
                arm=arm,
                marker_pose=profile["T_B0_M"],
                camera_name=camera_name,
                image_path=profile.get("teach_image", ""),
                metadata={"teach_profile": True, "marker": profile.get("marker")},
            )
        )
        return True
    else:
        profile = VisionStationStorage.get_profile(station_id, arm)
        if profile is None:
            raise RuntimeError(f"找不到视觉示教基准: 工位={station_id}, {arm_display_name(arm)}")
        camera_name = profile.get("camera_name") or _camera_name_for_arm(arm)
        marker_size = _normalize_marker(profile.get("marker"))
        if marker_size is None:
            raise RuntimeError(
                f"示教基准缺少 L 型 marker 真实宽高: "
                f"{profile.get('station_name', station_id)} / {arm_display_name(arm)}，请重新采集示教基准"
            )
        photo_pose = profile.get("photo_pose") or []
        if photo_pose:
            log(f"移动到重定位拍照位: {profile.get('station_name', station_id)} / {arm_display_name(arm)}")
            if not move_arm_to_pose(controller, arm, [float(v) for v in photo_pose], mode=params.get("move_mode", "move_j")):
                raise RuntimeError("移动到重定位拍照位失败")

    marker = capture_marker_pose(
        controller,
        camera,
        station_id,
        arm,
        camera_name,
        "run",
        marker_size,
        log,
    )
    context.set_vision_state(
        VisionRelocalizationState(
            station_id=station_id,
            arm=arm,
            marker_pose=marker["T_B_M"],
            camera_name=camera_name,
            image_path=marker["image_path"],
            metadata={
                "image_points": marker["image_points"],
                "image_size": marker["image_size"],
                "visualization_path": marker["visualization_path"],
                "marker": marker["marker"],
            },
        )
    )
    log(f"视觉重定位完成: 工位={station_id}, {arm_display_name(arm)}")
    return True


def compensate_pose_with_context(
    taught_pose,
    station_id: str,
    arm: str,
    context: ExecutionContext,
    mode: str | None = None,
    planar_constraint: str | None = None,
) -> list[float]:
    arm_key = normalize_arm_name(arm)
    profile = VisionStationStorage.get_profile(station_id, arm_key)
    if profile is None:
        raise RuntimeError(f"找不到视觉示教基准: 工位={station_id}, {arm_display_name(arm_key)}")

    state = context.get_vision_state(station_id, arm_key)
    if state is None:
        raise RuntimeError(
            f"缺少当前视觉定位结果: 请先执行视觉重定位动作 "
            f"({profile.get('station_name', station_id)} / {arm_display_name(arm_key)})"
        )

    cfg = Config.get_instance().get_vision_relocalization_config(arm_key)
    target_pose = parse_pose(taught_pose)
    return compensate_taught_pose(
        target_pose,
        profile["T_B0_M"],
        state.marker_pose,
        cfg,
        mode=mode or cfg.get("mode", "planar"),
        planar_constraint=planar_constraint or cfg.get("planar_constraint", "none"),
    )

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from ...domain.arm_names import normalize_arm_name
from ...domain.execution_context import ExecutionContext, VisionRelocalizationState
from ...geometry.pose_compensation import parse_pose
from ...configuration.settings import VisionSettings
from ...persistence.vision_station_storage import (
    VisionStationStorage,
    arm_display_name,
)
from ...devices import (
    ArmId,
    CameraSource,
    CartesianPose,
    DepthCameraSource,
    MotionMode,
    RobotSystem,
)
from ..models import VisionPipelineResult, vision_configuration
from .geometry import (
    as_matrix4,
    compensate_taught_pose,
    compute_marker_in_base_from_image,
)


LogFn = Callable[[str], None]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _project_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return _PROJECT_ROOT / value


def _matrix_to_list(matrix: object) -> list[list[float]]:
    value = as_matrix4(matrix)
    return [[float(item) for item in row] for row in value]


def _default_log(message: str) -> None:
    print(message)


_MARKER_WIDTH_KEYS = ("marker_width", "标定宽度", "marker宽度", "L型marker宽度")
_MARKER_HEIGHT_KEYS = ("marker_height", "标定高度", "marker高度", "L型marker高度")


def _read_float(
    mapping: Mapping[str, object] | None,
    keys: tuple[str, ...],
) -> float | None:
    if mapping is None:
        return None
    for key in keys:
        value = mapping.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError(f"{key} must be numeric")
        return float(value)
    return None


def _normalize_marker(marker: object) -> dict[str, float] | None:
    if not isinstance(marker, Mapping):
        return None
    normalized = _string_mapping(marker, "marker")
    width = _read_float(normalized, ("width", "w", *_MARKER_WIDTH_KEYS))
    height = _read_float(normalized, ("height", "h", *_MARKER_HEIGHT_KEYS))
    if width is None or height is None:
        return None
    if width <= 0 or height <= 0:
        raise ValueError("L 型 marker 真实宽高必须大于 0")
    return {"width": width, "height": height}


def _marker_from_params(
    params: Mapping[str, object],
    fallback: object = None,
) -> dict[str, float] | None:
    marker = _normalize_marker(params.get("marker"))
    if marker is not None:
        return marker
    marker = _normalize_marker(params)
    if marker is not None:
        return marker
    return _normalize_marker(fallback)


def _marker_for_action(
    params: Mapping[str, object],
    fallback: object = None,
) -> dict[str, float]:
    marker = _marker_from_params(params, fallback=fallback)
    if marker is None:
        raise ValueError("缺少 L 型 marker 真实宽高")
    return marker


def _config_for_marker(
    settings: VisionSettings,
    arm: str,
    marker: Mapping[str, float] | None,
    camera_name: str = "",
) -> dict[str, object]:
    cfg = settings.relocalization_config(arm, camera_name=camera_name)
    if marker is not None:
        cfg["marker"] = marker
    return cfg


def _camera_name_for_arm(
    settings: VisionSettings,
    arm: str,
    override: object = None,
) -> str:
    if override:
        return str(override).strip()
    config = settings.relocalization_config(arm)
    return _optional_text(config.get("camera_name"))


def get_current_arm_pose(robot_system: RobotSystem, arm: str) -> list[float]:
    arm_id = ArmId.parse(normalize_arm_name(arm))
    return robot_system.read_arm_state(arm_id).pose.to_list()


def move_arm_to_pose(
    robot_system: RobotSystem,
    arm: str,
    pose: Sequence[float],
    mode: str = "move_j",
) -> bool:
    robot_system.move_to_pose(
        ArmId.parse(normalize_arm_name(arm)),
        CartesianPose.from_iterable(pose),
        MotionMode.parse(mode),
    )
    return True


def _decode_jpeg(jpeg_bytes: bytes) -> NDArray[np.uint8] | None:
    data = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def capture_color_frame(
    camera: CameraSource,
    camera_name: str | None = None,
    timeout_seconds: float = 10.0,
) -> NDArray[np.uint8]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if isinstance(camera, DepthCameraSource):
            depth_frame = camera.get_latest_depth_frame(camera_name)
            if depth_frame is not None:
                return depth_frame.color_bgr
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


def _debug_paths(
    settings: VisionSettings,
    station_id: str,
    arm: str,
    suffix: str,
    debug_directory: str | Path,
) -> tuple[Path | None, Path | None]:
    if not settings.vision_relocalization_save_debug_images:
        return None, None
    debug_dir = _project_path(debug_directory)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = debug_dir / f"{station_id}_{normalize_arm_name(arm)}_{suffix}_{stamp}"
    return base.with_suffix(".jpg"), Path(f"{base}_corners.jpg")


def capture_marker_pose(
    controller: RobotSystem,
    camera: CameraSource,
    settings: VisionSettings,
    station_id: str,
    arm: str,
    camera_name: str,
    suffix: str,
    debug_directory: str | Path,
    marker: Mapping[str, float] | None = None,
    log_fn: LogFn | None = None,
) -> dict[str, object]:
    log = log_fn or _default_log
    cfg = _config_for_marker(settings, arm, marker, camera_name)
    image_path, vis_path = _debug_paths(
        settings,
        station_id,
        arm,
        suffix,
        debug_directory,
    )

    marker_size = _optional_mapping(cfg.get("marker"), "marker")
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
    controller: RobotSystem,
    camera: CameraSource,
    settings: VisionSettings,
    params: dict[str, object],
    station_storage: VisionStationStorage,
    debug_directory: str | Path,
    log_fn: LogFn | None = None,
) -> dict[str, Any]:
    log = log_fn or _default_log
    raw_station_id = str(params.get("station_id") or params.get("工位ID") or "").strip()
    station_name = str(
        params.get("station_name") or params.get("工位名称") or raw_station_id
    ).strip()
    if not station_name:
        raise ValueError("缺少工位名称")
    station_id = raw_station_id or station_name
    arm = normalize_arm_name(
        _optional_text(params.get("arm") or params.get("臂"), default="left")
    )
    camera_name = _camera_name_for_arm(
        settings,
        arm,
        params.get("camera_name") or params.get("相机名称"),
    )
    move_mode = params.get("move_mode") or params.get("移动模式") or "move_j"
    marker_size = _marker_for_action(params)

    raw_pose = params.get("photo_pose") or params.get("拍照位姿") or ""
    photo_pose = parse_pose(raw_pose) if raw_pose else []
    if photo_pose:
        log(f"移动到示教拍照位: {station_name} / {arm_display_name(arm)}")
        if not move_arm_to_pose(
            controller,
            arm,
            photo_pose,
            mode=_optional_text(move_mode, default="move_j"),
        ):
            raise RuntimeError("移动到示教拍照位失败")
    else:
        log("未填写拍照位姿，将使用当前机械臂位姿作为示教拍照位")

    marker = capture_marker_pose(
        controller,
        camera,
        settings,
        station_id,
        arm,
        camera_name,
        "teach",
        debug_directory,
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
    saved = station_storage.upsert_profile(profile)
    log(f"已保存视觉示教基准: {station_name} / {arm_display_name(arm)}")
    return saved


def execute_vision_relocalization(
    robot_system: RobotSystem,
    camera: CameraSource,
    parameters: dict[str, object],
    execution_context: ExecutionContext,
    settings: VisionSettings,
    station_storage: VisionStationStorage,
    debug_directory: str | Path,
    log: LogFn | None = None,
) -> VisionPipelineResult:
    log_fn = log or _default_log
    action_mode = (
        parameters.get("action_mode")
        or parameters.get("动作模式")
        or "run"
    )
    station_id = str(
        parameters.get("station_id")
        or parameters.get("工位ID")
        or parameters.get("station_name")
        or parameters.get("工位名称")
        or ""
    ).strip()
    arm = normalize_arm_name(
        _optional_text(
            parameters.get("arm") or parameters.get("臂"),
            default="left",
        )
    )

    if action_mode == "teach":
        profile = record_teach_profile(
            robot_system,
            camera,
            settings,
            parameters,
            station_storage,
            debug_directory,
            log_fn,
        )
        station_id = profile["station_id"]
        camera_name = profile.get("camera_name", "")
        execution_context.set_vision_state(
            VisionRelocalizationState(
                station_id=_optional_text(station_id),
                arm=arm,
                marker_pose=_matrix_to_list(profile["T_B0_M"]),
                camera_name=_optional_text(camera_name),
                image_path=_optional_text(profile.get("teach_image")),
                metadata={"teach_profile": True, "marker": profile.get("marker")},
            )
        )
        return VisionPipelineResult(True, frames_processed=1, inference_count=1)
    else:
        stored_profile = station_storage.get_profile(station_id, arm)
        if stored_profile is None:
            raise RuntimeError(f"找不到视觉示教基准: 工位={station_id}, {arm_display_name(arm)}")
        profile = stored_profile
        camera_name = _optional_text(profile.get("camera_name")) or _camera_name_for_arm(
            settings,
            arm,
        )
        marker_size = _normalize_marker(profile.get("marker"))
        if marker_size is None:
            raise RuntimeError(
                f"示教基准缺少 L 型 marker 真实宽高: "
                f"{profile.get('station_name', station_id)} / {arm_display_name(arm)}，请重新采集示教基准"
            )
        photo_pose = profile.get("photo_pose") or []
        if photo_pose:
            log_fn(
                f"移动到重定位拍照位: {profile.get('station_name', station_id)} / {arm_display_name(arm)}"
            )
            if not move_arm_to_pose(
                robot_system,
                arm,
                parse_pose(photo_pose),
                mode=_optional_text(
                    parameters.get("move_mode"),
                    default="move_j",
                ),
            ):
                raise RuntimeError("移动到重定位拍照位失败")

    marker = capture_marker_pose(
        robot_system,
        camera,
        settings,
        station_id,
        arm,
        camera_name,
        "run",
        debug_directory,
        marker_size,
        log_fn,
    )
    execution_context.set_vision_state(
        VisionRelocalizationState(
            station_id=station_id,
            arm=arm,
            marker_pose=_matrix_to_list(marker["T_B_M"]),
            camera_name=camera_name,
            image_path=_optional_text(marker["image_path"]),
            metadata={
                "image_points": marker["image_points"],
                "image_size": marker["image_size"],
                "visualization_path": marker["visualization_path"],
                "marker": marker["marker"],
            },
        )
    )
    log_fn(f"视觉重定位完成: 工位={station_id}, {arm_display_name(arm)}")
    return VisionPipelineResult(True, frames_processed=1, inference_count=1)


def compensate_pose_with_context(
    taught_pose: object,
    station_id: str,
    arm: str,
    context: ExecutionContext,
    settings: VisionSettings,
    mode: str | None = None,
    planar_constraint: str | None = None,
) -> list[float]:
    arm_key = normalize_arm_name(arm)
    storage = VisionStationStorage(
        settings.vision_relocalization_stations_file,
        configuration=vision_configuration(settings),
    )
    profile = storage.get_profile(station_id, arm_key)
    if profile is None:
        raise RuntimeError(f"找不到视觉示教基准: 工位={station_id}, {arm_display_name(arm_key)}")

    state = context.get_vision_state(station_id, arm_key)
    if state is None:
        raise RuntimeError(
            f"缺少当前视觉定位结果: 请先执行视觉重定位动作 "
            f"({profile.get('station_name', station_id)} / {arm_display_name(arm_key)})"
        )

    cfg = settings.relocalization_config(
        arm_key,
        camera_name=state.camera_name,
    )
    target_pose = parse_pose(taught_pose)
    return compensate_taught_pose(
        target_pose,
        profile["T_B0_M"],
        state.marker_pose,
        cfg,
        mode=mode or _optional_text(cfg.get("mode"), default="planar"),
        planar_constraint=planar_constraint
        or _optional_text(cfg.get("planar_constraint"), default="none"),
    )


def _string_mapping(
    value: Mapping[object, object],
    field: str,
) -> Mapping[str, object]:
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{field} must use string keys")
    return cast(Mapping[str, object], value)


def _optional_mapping(value: object, field: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return _string_mapping(value, field)


def _optional_text(value: object, *, default: str = "") -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise TypeError("expected a string value")
    return value

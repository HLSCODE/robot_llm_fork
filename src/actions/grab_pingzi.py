from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

import cv2
import numpy as np

from ..core.config_loader import Config
from ..device_runtime import (
    ArmId,
    CartesianPose,
    DepthCameraSource,
    MotionMode,
    MotionOptions,
    RobotSystem,
)
from ..vision.capture import (
    load_sam_model,
    load_yolo_model,
    process_mask_with_gmm,
)
from ..vision.interface import vertical_catch


PICTURE_DIR = Path(__file__).resolve().parents[1] / "vision" / "pictures"


def detect_target(
    image,
    yolo_model,
    sam_model,
    process_mask_fn: Callable,
    *,
    width: int,
    height: int,
    confidence_threshold: float,
) -> tuple[np.ndarray, list[int] | None, bool]:
    """Run YOLO, SAM and mask cleanup for the bottle workflow."""
    mask = np.zeros((height, width), dtype=np.uint8)
    detected = False
    bounding_box: list[int] | None = None

    for result in yolo_model(image, verbose=False):
        for box in result.boxes:
            confidence = float(box.conf)
            if confidence < confidence_threshold:
                continue
            detected = True
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            bounding_box = [int(x1), int(y1), int(x2), int(y2)]
            sam_results = sam_model(image, bboxes=[bounding_box])
            if sam_results:
                sam_mask = sam_results[0].masks.data[0].cpu().numpy()
                improved = process_mask_fn(
                    image,
                    (sam_mask * 255).astype(np.uint8),
                )
                mask = cv2.bitwise_or(mask, improved)
            cv2.rectangle(
                image,
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                (0, 255, 0),
                2,
            )

    return mask, bounding_box, detected


def capture_and_move(
    robot_system: RobotSystem,
    camera: DepthCameraSource,
    arm: ArmId,
    width: int = 640,
    height: int = 480,
) -> bool:
    """Capture a bottle, grasp it and place it through project capabilities."""
    config = Config.get_instance()
    calibration = config.get_vision_calibration()
    motion_options = MotionOptions(
        velocity_percent=config.VISION_DEFAULT_VELOCITY,
    )

    def move(pose: list[float], mode: MotionMode) -> None:
        robot_system.move_to_pose(
            arm,
            CartesianPose.from_iterable(pose),
            mode,
            motion_options,
        )

    try:
        yolo_model = load_yolo_model(config.YOLO_MODEL_PATH)
        sam_model = load_sam_model(config.SAM_MODEL_PATH)
        robot_system.open_gripper(arm)
        initial_pose = robot_system.read_arm_state(arm).pose.to_list()

        color_image, depth_image, color_intrinsics = _wait_for_frames(
            camera,
            config.VISION_CAMERA_NAME or None,
        )
        PICTURE_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(PICTURE_DIR / "original_image.jpg"), color_image)

        mask, _bounding_box, detected = detect_target(
            color_image,
            yolo_model,
            sam_model,
            process_mask_with_gmm,
            width=width,
            height=height,
            confidence_threshold=config.VISION_DEFAULT_CONFIDENCE,
        )
        if not detected:
            cv2.imwrite(
                str(PICTURE_DIR / "failed_detection.jpg"),
                color_image,
            )
            raise RuntimeError("未检测到目标")
        cv2.imwrite(str(PICTURE_DIR / "mask_result.jpg"), mask)

        current_pose = robot_system.read_arm_state(arm).pose.to_list()
        above_object_pose, _, _ = vertical_catch(
            mask,
            depth_image,
            color_intrinsics,
            current_pose,
            config.VISION_DEFAULT_GRIPPER_LENGTH,
            calibration["gripper_offset"],
            calibration["rotation_matrix"],
            calibration["translation_vector"],
        )
        camera_above_pose = above_object_pose.copy()
        camera_above_pose[0] += config.VISION_PREP_OFFSET_X
        move(camera_above_pose, MotionMode.JOINT)
        time.sleep(1)

        color_image, depth_image, color_intrinsics = _wait_for_frames(
            camera,
            config.VISION_CAMERA_NAME or None,
        )
        mask, _bounding_box, detected = detect_target(
            color_image,
            yolo_model,
            sam_model,
            process_mask_with_gmm,
            width=width,
            height=height,
            confidence_threshold=config.VISION_DEFAULT_CONFIDENCE,
        )
        if not detected:
            raise RuntimeError("二次检测未发现目标")

        current_pose = robot_system.read_arm_state(arm).pose.to_list()
        _, _, final_pose = vertical_catch(
            mask,
            depth_image,
            color_intrinsics,
            current_pose,
            config.VISION_DEFAULT_GRIPPER_LENGTH,
            calibration["gripper_offset"],
            calibration["rotation_matrix"],
            calibration["translation_vector"],
        )
        final_pose[3:6] = calibration["gripper_offset"]

        above_target = final_pose.copy()
        above_target[2] = current_pose[2]
        above_target[0] += config.VISION_BOTTLE_TARGET_OFFSET_X
        above_target[1] += config.VISION_BOTTLE_TARGET_OFFSET_Y
        move(above_target, MotionMode.LINEAR)

        grasp_pose = above_target.copy()
        grasp_pose[2] = config.VISION_GRASP_Z
        move(grasp_pose, MotionMode.LINEAR)
        robot_system.close_gripper(arm)
        move(above_target, MotionMode.LINEAR)

        move(initial_pose, MotionMode.JOINT)
        _place_at_fixed_position(
            robot_system,
            arm,
            config,
            motion_options,
        )
        return True
    except Exception as exc:
        print(f"瓶子抓取失败: {exc}")
        return False


def _place_at_fixed_position(
    robot_system: RobotSystem,
    arm: ArmId,
    config,
    motion_options: MotionOptions,
) -> None:
    def move(pose: list[float], mode: MotionMode) -> None:
        robot_system.move_to_pose(
            arm,
            CartesianPose.from_iterable(pose),
            mode,
            motion_options,
        )

    move(config.PLACE_TRANSFER_POSE, MotionMode.JOINT)
    above = list(config.PLACE_ABOVE)
    move(above, MotionMode.LINEAR)
    below = above.copy()
    below[2] -= config.PLACE_DROP_HEIGHT
    move(below, MotionMode.LINEAR)
    robot_system.open_gripper(arm)
    move(above, MotionMode.LINEAR)
    move(config.PLACE_TRANSFER_POSE, MotionMode.LINEAR)
    move(config.PLACE_POS2, MotionMode.JOINT)
    move(config.INITIAL_POSE, MotionMode.JOINT)


def _wait_for_frames(
    camera: DepthCameraSource,
    camera_name: str | None,
    timeout_seconds: float = 10.0,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        frames = camera.get_latest_raw_frames(camera_name)
        if frames is not None and all(value is not None for value in frames):
            return frames
        time.sleep(0.2)
    raise TimeoutError("等待深度相机帧超时")

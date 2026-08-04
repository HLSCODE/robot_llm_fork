from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

from ..configuration.settings import VisionSettings
from ..devices import (
    ArmId,
    CartesianPose,
    DepthCameraSource,
    MotionMode,
    MotionOptions,
    RobotSystem,
)
from .capture import (
    load_sam_model,
    load_yolo_model,
    process_mask_with_gmm,
)
from .interface import vertical_catch


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
    settings: VisionSettings,
    debug_directory: str,
    save_debug_images: bool,
    width: int = 640,
    height: int = 480,
) -> bool:
    """Capture a bottle, grasp it and place it through project capabilities."""
    calibration = settings.calibration_config()
    motion_options = MotionOptions(
        velocity_percent=settings.vision_default_velocity,
    )

    def move(pose: list[float], mode: MotionMode) -> None:
        robot_system.move_to_pose(
            arm,
            CartesianPose.from_iterable(pose),
            mode,
            motion_options,
        )

    try:
        yolo_model = load_yolo_model(settings.yolo_model_path)
        sam_model = load_sam_model(settings.sam_model_path)
        robot_system.open_gripper(arm)
        initial_pose = robot_system.read_arm_state(arm).pose.to_list()

        color_image, depth_image, color_intrinsics = _wait_for_frames(
            camera,
            settings.vision_camera_name or None,
        )
        if save_debug_images:
            cv2.imwrite(f"{debug_directory}/original_image.jpg", color_image)

        mask, _bounding_box, detected = detect_target(
            color_image,
            yolo_model,
            sam_model,
            process_mask_with_gmm,
            width=width,
            height=height,
            confidence_threshold=settings.vision_default_confidence,
        )
        if not detected:
            if save_debug_images:
                cv2.imwrite(f"{debug_directory}/failed_detection.jpg", color_image)
            raise RuntimeError("未检测到目标")
        if save_debug_images:
            cv2.imwrite(f"{debug_directory}/mask_result.jpg", mask)

        current_pose = robot_system.read_arm_state(arm).pose.to_list()
        above_object_pose, _, _ = vertical_catch(
            mask,
            depth_image,
            color_intrinsics,
            current_pose,
            settings.vision_default_gripper_length,
            calibration["gripper_offset"],
            calibration["rotation_matrix"],
            calibration["translation_vector"],
        )
        camera_above_pose = above_object_pose.copy()
        camera_above_pose[0] += settings.vision_prep_offset_x
        move(camera_above_pose, MotionMode.JOINT)
        time.sleep(1)

        color_image, depth_image, color_intrinsics = _wait_for_frames(
            camera,
            settings.vision_camera_name or None,
        )
        mask, _bounding_box, detected = detect_target(
            color_image,
            yolo_model,
            sam_model,
            process_mask_with_gmm,
            width=width,
            height=height,
            confidence_threshold=settings.vision_default_confidence,
        )
        if not detected:
            raise RuntimeError("二次检测未发现目标")

        current_pose = robot_system.read_arm_state(arm).pose.to_list()
        _, _, final_pose = vertical_catch(
            mask,
            depth_image,
            color_intrinsics,
            current_pose,
            settings.vision_default_gripper_length,
            calibration["gripper_offset"],
            calibration["rotation_matrix"],
            calibration["translation_vector"],
        )
        final_pose[3:6] = calibration["gripper_offset"]

        above_target = final_pose.copy()
        above_target[2] = current_pose[2]
        above_target[0] += settings.vision_bottle_target_offset_x
        above_target[1] += settings.vision_bottle_target_offset_y
        move(above_target, MotionMode.LINEAR)

        grasp_pose = above_target.copy()
        grasp_pose[2] = settings.vision_grasp_z
        move(grasp_pose, MotionMode.LINEAR)
        robot_system.close_gripper(arm)
        move(above_target, MotionMode.LINEAR)

        move(initial_pose, MotionMode.JOINT)
        _place_at_fixed_position(
            robot_system,
            arm,
            settings,
            motion_options,
        )
        return True
    except Exception as exc:
        print(f"瓶子抓取失败: {exc}")
        return False


def _place_at_fixed_position(
    robot_system: RobotSystem,
    arm: ArmId,
    settings: VisionSettings,
    motion_options: MotionOptions,
) -> None:
    def move(pose: list[float], mode: MotionMode) -> None:
        robot_system.move_to_pose(
            arm,
            CartesianPose.from_iterable(pose),
            mode,
            motion_options,
        )

    move(settings.place_transfer_pose, MotionMode.JOINT)
    above = list(settings.place_above)
    move(above, MotionMode.LINEAR)
    below = above.copy()
    below[2] -= settings.place_drop_height
    move(below, MotionMode.LINEAR)
    robot_system.open_gripper(arm)
    move(above, MotionMode.LINEAR)
    move(settings.place_transfer_pose, MotionMode.LINEAR)
    move(settings.place_pos2, MotionMode.JOINT)
    move(settings.initial_pose, MotionMode.JOINT)


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

# -*- coding: utf-8 -*-
"""
视觉抓取动作模块 (Vision Capture Action)

功能：通过深度相机 + YOLO + SAM 实现目标检测、分割、三维定位与机械臂抓取。

深度相机与机械臂都必须由 DeviceRuntime 注入。视觉流程只依赖项目级
CameraSource 和 RobotSystem 能力，不接触厂商 SDK。

典型调用流程：
    action = VisionCaptureAction(
        robot_system=robot_system,
        camera=camera,
        workflow="bottle",
    )
    action.execute()
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Literal, cast

import cv2
import numpy as np
from numpy.typing import NDArray
from sklearn.mixture import GaussianMixture
from ultralytics import SAM, YOLO

from ...configuration.settings import VisionSettings
from ...devices import (
    ArmId,
    CartesianPose,
    DepthCameraSource,
    MotionMode,
    MotionOptions,
    GrippingRobotSystem,
)

# ---------------------------------------------------------------
# 从统一配置加载默认值
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# 路径与导入
# ---------------------------------------------------------------
from .vertical import vertical_catch_main as vertical_catch

# ---------------------------------------------------------------
# 调试图片保存根目录（可用 VisionCaptureAction(debug_save_root=...) 覆盖）
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# 模型缓存（避免重复加载）
# ---------------------------------------------------------------
_model_cache: dict[str, YOLO | SAM] = {}
_cache_lock = threading.Lock()

MaskProcessor = Callable[
    [NDArray[np.generic], NDArray[np.uint8]],
    NDArray[np.uint8],
]


def load_yolo_model(path: str) -> YOLO:
    if not os.path.exists(path):
        raise FileNotFoundError(f"YOLO 模型权重文件不存在: {path}")
    with _cache_lock:
        if "yolo" not in _model_cache:
            _model_cache["yolo"] = YOLO(path)
        return cast(YOLO, _model_cache["yolo"])


def load_sam_model(path: str) -> SAM:
    if not os.path.exists(path):
        raise FileNotFoundError(f"SAM 模型权重文件不存在: {path}")
    with _cache_lock:
        if "sam" not in _model_cache:
            _model_cache["sam"] = SAM(path)
        return cast(SAM, _model_cache["sam"])


# ---------------------------------------------------------------
# 核心算法
# ---------------------------------------------------------------


def process_mask_with_gmm(
    image: NDArray[np.generic],
    mask: NDArray[np.uint8],
    n_components: int = 1,
) -> NDArray[np.uint8]:
    """
    使用高斯混合模型(GMM)处理SAM分割掩码，过滤噪声并保留最大连通区域。

    Args:
        image: BGR/RGB 原始图像
        mask:  单通道二值掩码 (0/255)
        n_components: GMM 分量数

    Returns:
        改进后的二值掩码 (0/255)
    """
    masked_image = cv2.bitwise_and(image, image, mask=mask)
    y_coords, x_coords = np.nonzero(mask)
    pixels = masked_image[y_coords, x_coords]

    if len(pixels) == 0:
        return mask

    features = np.column_stack((x_coords, y_coords, pixels))
    gmm = GaussianMixture(n_components=n_components, random_state=42)
    labels = gmm.fit_predict(features)

    new_mask = np.zeros_like(mask)
    for i in range(n_components):
        component_mask = np.zeros_like(mask)
        component_indices = labels == i
        component_mask[y_coords[component_indices], x_coords[component_indices]] = 255

        num_labels, labels_im = cv2.connectedComponents(component_mask)
        if num_labels > 1:
            largest_label = 1 + np.argmax([np.sum(labels_im == j) for j in range(1, num_labels)])
            component_mask = (labels_im == largest_label).astype(np.uint8) * 255

        new_mask = cv2.bitwise_or(new_mask, component_mask)

    kernel: NDArray[np.uint8] = np.ones((5, 5), np.uint8)
    new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_CLOSE, kernel)
    new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_OPEN, kernel)
    return new_mask


def detect_and_segment(
    color_image: NDArray[np.generic],
    yolo_model: YOLO,
    sam_model: SAM,
    width: int = 640,
    height: int = 480,
    confidence_threshold: float = 0.7,
    apply_gmm: bool = True,
    debug_save_path: str | None = None,
    process_mask_fn: MaskProcessor | None = None,
) -> tuple[bool, NDArray[np.uint8]]:
    """
    对单帧图像执行 YOLO 检测 + SAM 分割，返回合并掩码。

    Args:
        color_image:       BGR 图像
        yolo_model:        YOLO 模型实例
        sam_model:         SAM 模型实例
        width/height:      图像分辨率（用于初始化掩码画布）
        confidence_threshold: YOLO 置信度阈值
        apply_gmm:         是否使用 GMM 改进掩码
        debug_save_path:   若非 None，保存 debug 图片到该路径
        process_mask_fn:   可选 (image, sam_mask) -> mask；默认用本模块 process_mask_with_gmm

    Returns:
        (detected: bool, mask: np.ndarray)
    """
    if process_mask_fn is None:
        process_mask_fn = process_mask_with_gmm

    mask: NDArray[np.uint8] = np.zeros((height, width), dtype=np.uint8)
    yolo_results = yolo_model(color_image, verbose=False)

    detected = False
    for result in yolo_results:
        for box in result.boxes:
            confidence = float(box.conf)
            if confidence < confidence_threshold:
                continue

            detected = True
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            bbox = [int(x1), int(y1), int(x2), int(y2)]

            sam_results = sam_model(color_image, bboxes=[bbox])
            if sam_results and len(sam_results) > 0:
                sam_mask = sam_results[0].masks.data[0].cpu().numpy()
                sam_mask = (sam_mask * 255).astype(np.uint8)

                if apply_gmm:
                    sam_mask = process_mask_fn(color_image, sam_mask)

                mask = cv2.bitwise_or(mask, sam_mask)

            if debug_save_path is not None:
                dbg = color_image.copy()
                cv2.rectangle(dbg, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.imwrite(os.path.join(debug_save_path, "detection.jpg"), dbg)
                cv2.imwrite(os.path.join(debug_save_path, "mask.jpg"), mask)

    return detected, mask


# ---------------------------------------------------------------
# 标定 / 运动参数（由 VisionSettings 注入）
# ---------------------------------------------------------------
def run_pingzi_capture(
    robot_system: GrippingRobotSystem,
    camera: DepthCameraSource,
    arm: ArmId,
    settings: VisionSettings,
    debug_directory: str,
    save_debug_images: bool,
    width: int = 640,
    height: int = 480,
) -> bool:
    """
    执行瓶子检测、抓取和固定位置放置流程。
    """
    from .bottle import capture_and_move

    return bool(
        capture_and_move(
            robot_system,
            camera,
            arm,
            settings,
            debug_directory,
            save_debug_images,
            width,
            height,
        )
    )


# ---------------------------------------------------------------
# 主类：VisionCaptureAction
# ---------------------------------------------------------------


class VisionCaptureAction:
    """
    视觉抓取动作。

    使用方式：

        action = VisionCaptureAction(
            robot_system=runtime.require(ROBOT_SYSTEM, GrippingRobotSystem),
            camera=runtime.require(CAMERA, DepthCameraSource),
            workflow="bottle",
        )
        action.execute()
    """

    def __init__(
        self,
        robot_system: GrippingRobotSystem,
        camera: DepthCameraSource,
        settings: VisionSettings,
        yolo_model_path: str | None = None,
        sam_model_path: str | None = None,
        target_robot: Literal["robot1", "robot2"] = "robot1",
        workflow: Literal["vertical", "bottle"] = "bottle",
        gripper_offset: Sequence[float] | None = None,
        rotation_matrix: Sequence[Sequence[float]] | None = None,
        translation_vector: Sequence[float] | None = None,
        gripper_length: float | None = None,
        confidence_threshold: float | None = None,
        move_velocity: int | None = None,
        image_width: int = 640,
        image_height: int = 480,
        save_debug_images: bool = True,
        debug_save_root: str | None = None,
        raise_on_error: bool = True,
    ) -> None:
        # ── 从不可变设置快照加载默认值 ──
        self.settings = settings
        self.yolo_model_path = yolo_model_path or settings.yolo_model_path
        self.sam_model_path = sam_model_path or settings.sam_model_path
        self.robot_system = robot_system
        self.camera = camera
        self.arm = ArmId.parse(target_robot)
        self.target_robot = target_robot
        self.workflow = workflow

        self.gripper_offset = list(gripper_offset or settings.vision_gripper_offset)
        self.rotation_matrix = normalize_rotation_matrix(
            rotation_matrix or settings.vision_rotation_matrix
        )
        self.translation_vector = list(
            translation_vector or settings.vision_translation_vector
        )
        self.gripper_length = (
            gripper_length if gripper_length is not None else settings.vision_default_gripper_length
        )
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.vision_default_confidence
        )
        self.move_velocity = (
            move_velocity if move_velocity is not None else settings.vision_default_velocity
        )
        self.image_width = image_width
        self.image_height = image_height
        self.save_debug_images = save_debug_images
        self.debug_save_root = debug_save_root or settings.vision_debug_save_dir
        self.max_attempts = settings.max_attempts
        self.raise_on_error = raise_on_error

        self._process_mask_fn: MaskProcessor = process_mask_with_gmm

        # 运行时状态
        self._yolo_model: YOLO | None = None
        self._sam_model: SAM | None = None
        self._last_error: str | None = None
        self._result: bool = False

    # ---- 公共 API ----

    def execute(self) -> bool:
        """
        执行视觉抓取流程。
        workflow=\"vertical\"：原 Robot.capture_and_move 逻辑；
        workflow=\"bottle\"：执行瓶子检测和固定位置放置流程。

        Returns:
            True  = 抓取成功（夹取成功并回到初始位姿）
            False = 失败
        """
        try:
            if self.workflow == "bottle":
                return self._execute_bottle()
            return self._execute_vertical()
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[VisionCapture] 错误: {exc}")
            if self.raise_on_error:
                raise
            return False

    def _execute_bottle(self) -> bool:
        """Run the managed bottle capture and fixed-position placement pipeline."""
        ok = run_pingzi_capture(
            self.robot_system,
            self.camera,
            self.arm,
            self.settings,
            self.debug_save_root,
            self.save_debug_images,
            self.image_width,
            self.image_height,
        )
        self._result = bool(ok)
        return bool(ok)

    def _execute_vertical(self) -> bool:
        self._ensure_models()

        # 1. 打开夹爪 & 记录初始位姿
        self._gripper_release()
        initial_pose = self._read_pose()
        print("[VisionCapture] 初始位姿:", initial_pose)

        # 2. 首次检测
        color_im, depth_im, intr = self._fetch_frames()
        self._validate_frames(color_im, depth_im, intr)

        detected, mask = detect_and_segment(
            color_im,
            self._yolo_model,
            self._sam_model,
            self.image_width,
            self.image_height,
            self.confidence_threshold,
            apply_gmm=True,
            debug_save_path=self._debug_dir("first"),
            process_mask_fn=self._process_mask_fn,
        )
        if not detected:
            self._save_failed_image(color_im, "failed_detection.jpg")
            raise RuntimeError("首次检测未发现目标")

        # 3. 移动到预备位置
        cur_pose = self._read_pose()

        above, _, final = vertical_catch(
            mask,
            depth_im,
            intr,
            cur_pose,
            self.gripper_length,
            self.gripper_offset,
            self.rotation_matrix,
            self.translation_vector,
        )
        prep_pose = above.copy()
        prep_pose[0] += self.settings.vision_prep_offset_x
        self._move(prep_pose, MotionMode.JOINT, "预备位置")
        time.sleep(1)

        # 4. 二次检测（更精确）
        color_im, depth_im, intr = self._fetch_frames()
        self._validate_frames(color_im, depth_im, intr)

        detected, mask = detect_and_segment(
            color_im,
            self._yolo_model,
            self._sam_model,
            self.image_width,
            self.image_height,
            self.confidence_threshold,
            apply_gmm=True,
            debug_save_path=self._debug_dir("second"),
            process_mask_fn=self._process_mask_fn,
        )
        if not detected:
            raise RuntimeError("二次检测未发现目标")

        cur_pose = self._read_pose()

        _, adj_angle, adj_final = vertical_catch(
            mask,
            depth_im,
            intr,
            cur_pose,
            self.gripper_length,
            self.gripper_offset,
            self.rotation_matrix,
            self.translation_vector,
        )

        # 5. XY 平面移动到目标上方
        adj_final[3] = self.gripper_offset[0]
        adj_final[4] = self.gripper_offset[1]
        adj_final[5] = self.gripper_offset[2]

        above_target = adj_final.copy()
        above_target[2] = cur_pose[2]
        above_target[1] -= 0.015
        self._move(above_target, MotionMode.LINEAR, "目标上方")
        time.sleep(0.5)

        # 6. Z 轴下降
        adj_final[1] -= 0.015
        adj_final[2] = self.settings.vision_grasp_z
        self._move(adj_final, MotionMode.LINEAR, "抓取位姿")

        # 7. 夹取
        self._gripper_pick()

        # 8. 回到初始位姿
        self._move(initial_pose, MotionMode.JOINT, "初始位姿")

        # 9. 释放（放置物体）
        self._gripper_release()

        self._result = True
        print("[VisionCapture] === 抓取流程完成 ===")
        return True

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def success(self) -> bool:
        return self._result

    # ---- 内部方法 ----

    def _ensure_models(self) -> None:
        if self._yolo_model is None:
            self._yolo_model = load_yolo_model(self.yolo_model_path)
        if self._sam_model is None:
            self._sam_model = load_sam_model(self.sam_model_path)

    def _fetch_frames(
        self,
    ) -> tuple[
        NDArray[np.generic],
        NDArray[np.generic],
        dict[str, float],
    ]:
        camera_name = self.settings.vision_camera_name or None
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            frame = self.camera.get_latest_depth_frame(camera_name)
            if frame is not None:
                intrinsics = frame.intrinsics
                return (
                    frame.color_bgr,
                    frame.depth_uint16,
                    {
                        "fx": float(intrinsics[0, 0]),
                        "fy": float(intrinsics[1, 1]),
                        "ppx": float(intrinsics[0, 2]),
                        "ppy": float(intrinsics[1, 2]),
                    },
                )
            time.sleep(0.2)
        raise RuntimeError("等待深度相机帧超时")

    def _read_pose(self) -> list[float]:
        return self.robot_system.read_arm_state(self.arm).pose.to_list()

    def _gripper_release(self) -> None:
        for _attempt in range(self.max_attempts):
            try:
                self.robot_system.open_gripper(self.arm)
                print("[VisionCapture] 夹爪已打开")
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("夹爪打开失败")

    def _gripper_pick(self) -> None:
        for attempt in range(self.max_attempts):
            try:
                self.robot_system.close_gripper(self.arm)
                print("[VisionCapture] 夹取成功")
                return
            except Exception:
                print(f"[VisionCapture] 夹取失败 (attempt {attempt + 1}), 重试...")
                time.sleep(1)
        raise RuntimeError("夹取失败")

    def _move(
        self,
        pose: list[float],
        mode: MotionMode,
        label: str,
    ) -> None:
        self.robot_system.move_to_pose(
            self.arm,
            CartesianPose.from_iterable(pose),
            mode,
            MotionOptions(velocity_percent=self.move_velocity),
        )
        print(f"[VisionCapture] 已移动到 {label}: {pose}")

    def _validate_frames(
        self,
        color: NDArray[np.generic] | None,
        depth: NDArray[np.generic] | None,
        intr: dict[str, float] | None,
    ) -> None:
        if color is None or depth is None or intr is None:
            raise RuntimeError("无法获取深度相机帧")

    def _debug_dir(self, sub: str) -> str | None:
        if not self.save_debug_images:
            return None
        d = os.path.join(self.debug_save_root, sub)
        os.makedirs(d, exist_ok=True)
        return d

    def _save_failed_image(
        self,
        img: NDArray[np.generic] | None,
        name: str,
    ) -> None:
        if not self.save_debug_images or img is None:
            return
        d = self._debug_dir("failed")
        if d is None:
            return
        cv2.imwrite(os.path.join(d, name), img)


def normalize_rotation_matrix(
    values: Sequence[Sequence[float]] | Sequence[float],
) -> list[list[float]]:
    flattened: list[float] = []
    for value in values:
        if isinstance(value, Sequence):
            flattened.extend(float(item) for item in value)
        else:
            flattened.append(float(value))
    if len(flattened) != 9:
        raise ValueError("vision rotation matrix must contain nine values")
    return [flattened[index : index + 3] for index in range(0, 9, 3)]

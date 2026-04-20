# -*- coding: utf-8 -*-
"""
视觉抓取动作模块 (Vision Capture Action)

功能：通过深度相机 + YOLO + SAM 实现目标检测、分割、三维定位与机械臂抓取。

深度相机（如 Intel RealSense D435i）：
  - 建议在 GUI 侧做「深度对齐到彩色」(align depth to color)，使 depth 与 color 同分辨率；
  - 通过 socket 或 RobotController.get_frames_from_gui() 传入 RGB、对齐后的 depth、
    以及彩色相机内参（与 YOLO/SAM 使用的图像一致），与 grab_pingzi_baizhuo.py 用法一致。

支持两种调用模式：
  1. 直接调用 RobotController 实例（优先 get_frames_from_gui + 已加载的 yolo/sam）
  2. 无 controller 时通过 socket 向 GUI 服务取帧（localhost:12345），并本地加载模型

典型调用流程（GUI 场景）：
    from action_vision_capture import VisionCaptureAction

    action = VisionCaptureAction(controller=robot_controller)
    result = action.execute()

典型调用流程（仅 socket、无 RobotController）：
    action = VisionCaptureAction(
        frame_source="socket",
        yolo_model_path=".../best.pt",
        sam_model_path=".../sam2.1_l.pt",
        target_robot="robot1",
    )
    action.execute()

典型调用流程（瓶子/白桌，与 grab_pingzi_baizhuo 一致）：
    action = VisionCaptureAction(controller=controller, workflow="bottle")
    action.execute()
"""

from __future__ import annotations

import os
import time
import socket
import pickle
import struct
import threading
from typing import Optional, Literal, Tuple, Any, Callable
from Robotic_Arm.rm_robot_interface import *

import cv2
import numpy as np
from sklearn.mixture import GaussianMixture
from ultralytics import YOLO, SAM

# ---------------------------------------------------------------
# 从统一配置加载默认值
# ---------------------------------------------------------------
try:
    from ..core.config_loader import Config
    _config = Config.get_instance()
    _DEFAULT_YOLO_MODEL_PATH = _config.YOLO_MODEL_PATH
    _DEFAULT_SAM_MODEL_PATH = _config.SAM_MODEL_PATH
    _DEFAULT_VISION_DEBUG_SAVE_DIR = _config.VISION_DEBUG_SAVE_DIR
    _DEFAULT_FRAME_SOCKET_HOST = _config.VISION_CAMERA_HOST
    _DEFAULT_FRAME_SOCKET_PORT = _config.VISION_CAMERA_PORT
except Exception:
    _DEFAULT_YOLO_MODEL_PATH = "models/best.pt"
    _DEFAULT_SAM_MODEL_PATH = "models/sam2.1_l.pt"
    _DEFAULT_VISION_DEBUG_SAVE_DIR = "pictures"
    _DEFAULT_FRAME_SOCKET_HOST = "localhost"
    _DEFAULT_FRAME_SOCKET_PORT = 12345

# ---------------------------------------------------------------
# 路径与导入
# ---------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PICTURE_DIR_DEFAULT = os.path.join(_THIS_DIR, "pictures")

from .interface import vertical_catch

try:
    from ..arm_sdk.config import (
        ROBOT1_CONFIG, ROBOT2_CONFIG, MOVE_CONFIG,
        GRIPPER_CONFIG, MAX_ATTEMPTS
    )
except ImportError:
    # 独立运行时的最小化默认值
    ROBOT1_CONFIG = {"ip": "192.168.3.18", "port": 8080}
    ROBOT2_CONFIG = {"ip": "192.168.3.19", "port": 8080}
    MOVE_CONFIG   = {"velocity": 10, "radius": 0, "connect": 0, "block": 1}
    GRIPPER_CONFIG = {
        "pick":    {"speed": 100, "force": 300, "timeout": 3},
        "release": {"speed": 100, "timeout": 3}
    }
    MAX_ATTEMPTS = 5


# ---------------------------------------------------------------
# 调试图片保存根目录（可用 VisionCaptureAction(debug_save_root=...) 覆盖）
# ---------------------------------------------------------------
_DEBUG_SAVE_ROOT = _DEFAULT_VISION_DEBUG_SAVE_DIR

# ---------------------------------------------------------------
# 模型缓存（避免重复加载）
# ---------------------------------------------------------------
_model_cache: dict[str, Any] = {}
_cache_lock  = threading.Lock()


def _load_yolo(path: str) -> YOLO:
    with _cache_lock:
        if "yolo" not in _model_cache:
            _model_cache["yolo"] = YOLO(path)
        return _model_cache["yolo"]


def _load_sam(path: str) -> SAM:
    with _cache_lock:
        if "sam" not in _model_cache:
            _model_cache["sam"] = SAM(path)
        return _model_cache["sam"]


# ---------------------------------------------------------------
# 核心算法
# ---------------------------------------------------------------

def process_mask_with_gmm(
    image: np.ndarray,
    mask: np.ndarray,
    n_components: int = 1
) -> np.ndarray:
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
        component_indices = (labels == i)
        component_mask[y_coords[component_indices], x_coords[component_indices]] = 255

        num_labels, labels_im = cv2.connectedComponents(component_mask)
        if num_labels > 1:
            largest_label = 1 + np.argmax(
                [np.sum(labels_im == j) for j in range(1, num_labels)]
            )
            component_mask = (labels_im == largest_label).astype(np.uint8) * 255

        new_mask = cv2.bitwise_or(new_mask, component_mask)

    kernel = np.ones((5, 5), np.uint8)
    new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_CLOSE, kernel)
    new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_OPEN,  kernel)
    return new_mask


def get_frames_from_socket(
    host: str = "localhost",
    port: int = 12345,
    max_retries: int = 3,
    timeout: float = 5.0
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[dict]]:
    """
    通过 socket 向 GUI 服务端请求深度相机帧。

    Returns:
        (color_image, depth_image, color_intrinsics)  任一为 None 表示失败
    """
    for attempt in range(max_retries):
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(timeout)
            client.connect((host, port))
            client.send("get_frames".encode())

            data_size = struct.unpack(">L", client.recv(4))[0]
            received = b""
            while len(received) < data_size:
                chunk = client.recv(4096)
                if not chunk:
                    break
                received += chunk

            frames_data = pickle.loads(received)
            return (
                frames_data["color"],
                frames_data["depth"],
                frames_data["intrinsics"]
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"[VisionCapture] socket 重试 {attempt + 1}/{max_retries}: {e}")
            time.sleep(1)
        except Exception as e:
            print(f"[VisionCapture] socket 异常: {e}")
            break
        finally:
            client.close()

    return None, None, None


def detect_and_segment(
    color_image: np.ndarray,
    yolo_model,
    sam_model,
    width: int = 640,
    height: int = 480,
    confidence_threshold: float = 0.7,
    apply_gmm: bool = True,
    debug_save_path: Optional[str] = None,
    process_mask_fn=None,
) -> Tuple[bool, np.ndarray]:
    """
    对单帧图像执行 YOLO 检测 + SAM 分割，返回合并掩码。

    Args:
        color_image:       RGB 图像
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

    mask = np.zeros((height, width), dtype=np.uint8)
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
                cv2.rectangle(dbg, (int(x1), int(y1)), (int(x2), int(y2)),
                              (0, 255, 0), 2)
                cv2.imwrite(os.path.join(debug_save_path, "detection.jpg"), dbg)
                cv2.imwrite(os.path.join(debug_save_path, "mask.jpg"), mask)

    return detected, mask


# ---------------------------------------------------------------
# 标定 / 运动参数（可外部注入）
# ---------------------------------------------------------------
DEFAULT_GRIPPER_OFFSET     = [3.146, 0.0, 3.128]
DEFAULT_TRANSLATION_VECTOR = [-0.10273135, 0.03312807, -0.07214614]
DEFAULT_ROTATION_MATRIX = [
    [ 0.00215684,  0.97503835,  0.22202606],
    [-0.99995231, -0.0000119,   0.00976617],
    [ 0.00952503, -0.22203654,  0.97499182]
]


def run_pingzi_capture(controller, robot, width: int = 640, height: int = 480) -> bool:
    """
    执行与 grab_pingzi.capture_and_move 相同的「瓶子/白桌」抓取流程。
    """
    from ..actions.grab_pingzi import capture_and_move
    return bool(capture_and_move(controller, robot, width, height))


# ---------------------------------------------------------------
# 主类：VisionCaptureAction
# ---------------------------------------------------------------

class VisionCaptureAction:
    """
    视觉抓取动作。

    使用方式：

        # --- 方式 A：复用 RobotController ---
        controller = RobotController()
        action = VisionCaptureAction(controller=controller)
        action.execute()

        # --- 方式 B：独立使用（大模型调用）---
        action = VisionCaptureAction(
            frame_source      = "socket",
            yolo_model_path = "path/to/best.pt",
            sam_model_path = "path/to/sam2.1_l.pt",
            target_robot    = "robot1",
        )
        action.execute()

        # --- 方式 C：与 grab_pingzi_baizhuo 相同的瓶子流程 ---
        action = VisionCaptureAction(controller=controller, workflow="bottle")
        action.execute()
    """

    def __init__(
        self,
        controller = None,          # RobotController | None
        frame_source: Literal["auto", "controller", "socket"] = "auto",
        frame_socket_host: str = None,
        frame_socket_port: int = None,
        yolo_model_path: str = None,
        sam_model_path: str = None,
        target_robot: Literal["robot1", "robot2"] = "robot1",
        workflow: Literal["vertical", "bottle"] = "vertical",
        gripper_offset: list = None,
        rotation_matrix: list = None,
        translation_vector: list = None,
        gripper_length: float = 100,
        confidence_threshold: float = 0.7,
        move_velocity: int = 15,
        image_width: int = 640,
        image_height: int = 480,
        save_debug_images: bool = True,
        debug_save_root: str = None,
        raise_on_error: bool = True
    ):
        # 使用 config.env 中的配置作为默认值
        self.frame_socket_host = frame_socket_host or _DEFAULT_FRAME_SOCKET_HOST
        self.frame_socket_port = frame_socket_port or _DEFAULT_FRAME_SOCKET_PORT
        self.yolo_model_path   = yolo_model_path or _DEFAULT_YOLO_MODEL_PATH
        self.sam_model_path    = sam_model_path or _DEFAULT_SAM_MODEL_PATH
        self.controller        = controller
        self.frame_source      = frame_source
        self.target_robot      = target_robot
        self.workflow          = workflow

        go = gripper_offset
        rm = rotation_matrix
        tv = translation_vector
        if controller is not None:
            if go is None and hasattr(controller, "gripper_offset"):
                go = controller.gripper_offset
            if rm is None and hasattr(controller, "rotation_matrix"):
                rm = controller.rotation_matrix
            if tv is None and hasattr(controller, "translation_vector"):
                tv = controller.translation_vector

        self.gripper_offset     = list(go or DEFAULT_GRIPPER_OFFSET)
        self.rotation_matrix    = rm or DEFAULT_ROTATION_MATRIX
        self.translation_vector = tv or DEFAULT_TRANSLATION_VECTOR
        self.gripper_length    = gripper_length
        self.confidence_threshold = confidence_threshold
        self.move_velocity     = move_velocity
        self.image_width       = image_width
        self.image_height      = image_height
        self.save_debug_images = save_debug_images
        self.debug_save_root   = (debug_save_root or _DEFAULT_VISION_DEBUG_SAVE_DIR)
        self.raise_on_error   = raise_on_error

        self._process_mask_fn: Callable = process_mask_with_gmm
        if controller is not None and hasattr(controller, "process_mask_with_gmm"):
            self._process_mask_fn = controller.process_mask_with_gmm

        # 运行时状态
        self._robot      = None
        self._yolo_model = None
        self._sam_model  = None
        self._last_error : Optional[str] = None
        self._result     : bool = False

        # 独立模式下的机械臂连接
        self._robot_ctrl : Optional[object] = None

    # ---- 公共 API ----

    def execute(self) -> bool:
        """
        执行视觉抓取流程。
        workflow=\"vertical\"：原 Robot.capture_and_move 逻辑；
        workflow=\"bottle\"：委托 grab_pingzi_baizhuo.capture_and_move（需同目录脚本）。

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
        """与 grab_pingzi_baizhuo.py 一致：GUI 取帧 + 放置到固定点。"""
        if self.controller is None:
            raise RuntimeError("workflow='bottle' 需要传入 RobotController")
        robot = self._ensure_robot()
        ok = run_pingzi_capture(self.controller, robot, self.image_width, self.image_height)
        self._result = bool(ok)
        return bool(ok)

    def _execute_vertical(self) -> bool:
        self._ensure_models()
        robot = self._ensure_robot()

        # 1. 打开夹爪 & 记录初始位姿
        self._gripper_release(robot)
        ret, state = robot.rm_get_current_arm_state()
        self._check_ret(ret, "获取初始位姿")
        initial_pose = state["pose"]
        print("[VisionCapture] 初始位姿:", initial_pose)

        # 2. 首次检测
        color_im, depth_im, intr = self._fetch_frames()
        self._validate_frames(color_im, depth_im, intr)

        detected, mask = detect_and_segment(
            color_im, self._yolo_model, self._sam_model,
            self.image_width, self.image_height,
            self.confidence_threshold,
            apply_gmm=True,
            debug_save_path=self._debug_dir("first"),
            process_mask_fn=self._process_mask_fn,
        )
        if not detected:
            self._save_failed_image(color_im, "failed_detection.jpg")
            raise RuntimeError("首次检测未发现目标")

        # 3. 移动到预备位置
        ret, cur_state = robot.rm_get_current_arm_state()
        self._check_ret(ret, "获取当前位姿")
        cur_pose = cur_state["pose"]

        above, _, final = vertical_catch(
            mask, depth_im, intr, cur_pose,
            self.gripper_length,
            self.gripper_offset,
            self.rotation_matrix,
            self.translation_vector
        )
        prep_pose = above.copy()
        prep_pose[0] -= 0.08
        self._movej(robot, prep_pose, "预备位置")
        time.sleep(1)

        # 4. 二次检测（更精确）
        color_im, depth_im, intr = self._fetch_frames()
        self._validate_frames(color_im, depth_im, intr)

        detected, mask = detect_and_segment(
            color_im, self._yolo_model, self._sam_model,
            self.image_width, self.image_height,
            self.confidence_threshold,
            apply_gmm=True,
            debug_save_path=self._debug_dir("second"),
            process_mask_fn=self._process_mask_fn,
        )
        if not detected:
            raise RuntimeError("二次检测未发现目标")

        ret, cur_state = robot.rm_get_current_arm_state()
        self._check_ret(ret, "获取当前位姿")
        cur_pose = cur_state["pose"]

        _, adj_angle, adj_final = vertical_catch(
            mask, depth_im, intr, cur_pose,
            self.gripper_length,
            self.gripper_offset,
            self.rotation_matrix,
            self.translation_vector
        )

        # 5. XY 平面移动到目标上方
        adj_final[3] = self.gripper_offset[0]
        adj_final[4] = self.gripper_offset[1]
        adj_final[5] = self.gripper_offset[2]

        above_target = adj_final.copy()
        above_target[2] = cur_pose[2]
        above_target[1] -= 0.015
        self._movel(robot, above_target, "目标上方")
        time.sleep(0.5)

        # 6. Z 轴下降
        adj_final[1] -= 0.015
        adj_final[2] = -0.24
        self._movel(robot, adj_final, "抓取位姿")

        # 7. 夹取
        self._gripper_pick(robot)

        # 8. 回到初始位姿
        self._movej(robot, initial_pose, "初始位姿")

        # 9. 释放（放置物体）
        self._gripper_release(robot)

        self._result = True
        print("[VisionCapture] === 抓取流程完成 ===")
        return True

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def success(self) -> bool:
        return self._result

    def shutdown(self) -> None:
        """断开独立模式下创建的机械臂连接。"""
        if self._robot_ctrl is not None:
            self._robot_ctrl.disconnect()
            self._robot_ctrl = None

    # ---- 内部方法 ----

    def _ensure_models(self) -> None:
        ctrl = self.controller
        if ctrl is not None:
            if self._yolo_model is None and getattr(ctrl, "yolo_model", None) is not None:
                self._yolo_model = ctrl.yolo_model
            if self._sam_model is None and getattr(ctrl, "sam_model", None) is not None:
                self._sam_model = ctrl.sam_model
        if self._yolo_model is None:
            self._yolo_model = _load_yolo(self.yolo_model_path)
        if self._sam_model is None:
            self._sam_model = _load_sam(self.sam_model_path)

    def _ensure_robot(self):
        """获取 robot 实例（优先复用 controller，否则自建连接）。"""
        if self.controller is not None:
            ctrl = self.controller
            if self.target_robot == "robot1":
                self._robot = ctrl.robot1_ctrl.robot
            else:
                self._robot = ctrl.robot2_ctrl.robot
        else:
            if self._robot is None:
                config = ROBOT1_CONFIG if self.target_robot == "robot1" else ROBOT2_CONFIG
                self._robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
                handle = self._robot.rm_create_robot_arm(config["ip"], config["port"])

                class _SimpleCtrl:
                    def __init__(s, r):
                        s._r = r
                    def disconnect(s):
                        try:
                            s._r.rm_delete_robot_arm()
                        except Exception:
                            pass

                self._robot_ctrl = _SimpleCtrl(self._robot)
                ret, state = self._robot.rm_get_current_arm_state()
                self._check_ret(ret, "机械臂连接")
                print(f"[VisionCapture] 机械臂已连接: {config['ip']}")

        return self._robot

    def _fetch_frames(self):
        use_controller = False
        if self.frame_source == "controller":
            use_controller = True
        elif self.frame_source == "auto":
            if self.controller is not None and hasattr(
                self.controller, "get_frames_from_gui"
            ):
                use_controller = True
        if use_controller:
            if self.controller is None:
                raise RuntimeError("frame_source=controller/auto 时需要 RobotController")
            return self.controller.get_frames_from_gui()
        return get_frames_from_socket(
            self.frame_socket_host, self.frame_socket_port
        )

    @staticmethod
    def _check_ret(ret, msg=""):
        if ret != 0:
            raise RuntimeError(f"{msg}失败，错误码：{ret}")

    def _gripper_release(self, robot) -> None:
        for attempt in range(MAX_ATTEMPTS):
            ret = robot.rm_set_gripper_release(
                speed=GRIPPER_CONFIG["release"]["speed"],
                block=True,
                timeout=GRIPPER_CONFIG["release"]["timeout"]
            )
            if ret == 0:
                print("[VisionCapture] 夹爪已打开")
                return
            time.sleep(1)
        raise RuntimeError("夹爪打开失败")

    def _gripper_pick(self, robot) -> None:
        for attempt in range(MAX_ATTEMPTS):
            ret = robot.rm_set_gripper_pick_on(
                speed=GRIPPER_CONFIG["pick"]["speed"],
                block=True,
                timeout=GRIPPER_CONFIG["pick"]["timeout"],
                force=GRIPPER_CONFIG["pick"]["force"]
            )
            if ret == 0:
                print("[VisionCapture] 夹取成功")
                return
            print(f"[VisionCapture] 夹取失败 (attempt {attempt + 1}), 重试...")
            time.sleep(1)
        raise RuntimeError("夹取失败")

    def _movej(self, robot, pose, label="") -> None:
        ret = robot.rm_movej_p(pose, v=self.move_velocity, r=0, connect=0, block=1)
        self._check_ret(ret, f"movej -> {label}")
        print(f"[VisionCapture] 已移动到 {label}: {pose}")

    def _movel(self, robot, pose, label="") -> None:
        ret = robot.rm_movel(pose, v=self.move_velocity, r=0, connect=0, block=1)
        self._check_ret(ret, f"movel -> {label}")
        print(f"[VisionCapture] 已移动到 {label}: {pose}")

    def _validate_frames(self, color, depth, intr) -> None:
        if color is None or depth is None or intr is None:
            raise RuntimeError("无法获取深度相机帧")

    def _debug_dir(self, sub: str) -> Optional[str]:
        if not self.save_debug_images:
            return None
        d = os.path.join(self.debug_save_root, sub)
        os.makedirs(d, exist_ok=True)
        return d

    def _save_failed_image(self, img, name) -> None:
        if not self.save_debug_images or img is None:
            return
        d = self._debug_dir("failed")
        cv2.imwrite(os.path.join(d, name), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


# ---------------------------------------------------------------
# 独立入口（可直接 python action_vision_capture.py 运行测试）
# ---------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("VisionCaptureAction 独立测试")
    print("=" * 60)

    action = VisionCaptureAction(
        target_robot    = "robot1",
        save_debug_images = True,
    )

    try:
        result = action.execute()
        print(f"\n执行结果: {'成功 ✓' if result else '失败 ✗'}")
    except Exception as e:
        print(f"\n异常: {e}")
    finally:
        action.shutdown()

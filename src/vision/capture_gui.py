# -*- coding: utf-8 -*-
"""
GUI 调用入口：action_vision_capture_gui.py

功能：封装 VisionCaptureAction，使其可作为独立动作在 GUI 动作编排器中被调用。
GUI 无需感知 YOLO/SAM/机械臂内部逻辑，只需：
    from action_vision_capture_gui import VisionCaptureGUIAction
    action = VisionCaptureGUIAction(controller=robot_controller)
    action.execute()

在 GUI 的动作库 JSON 中注册示例：
    {
        "name": "视觉抓取",
        "type": "execute",
        "script": "action_vision_capture_gui",
        "class": "VisionCaptureGUIAction",
        "description": "使用 YOLO + SAM + 深度相机进行目标检测、分割、抓取",
        "parameters": [
            {"name": "target_robot",   "label": "目标机械臂",  "type": "select",  "options": ["robot1", "robot2"], "default": "robot1"},
            {"name": "confidence",    "label": "检测置信度", "type": "float",   "default": 0.7,   "min": 0.1, "max": 1.0},
            {"name": "debug_images",  "label": "保存调试图片", "type": "bool",   "default": true}
        ]
    }
"""

from __future__ import annotations

import time
from typing import Optional

from .capture import VisionCaptureAction


# ---------------------------------------------------------------
# GUI 封装类
# ---------------------------------------------------------------

class VisionCaptureGUIAction:
    """
    GUI 调用封装层。

    职责：
      - 接收 GUI 传入的 RobotController 实例
      - 将 GUI 参数映射到 VisionCaptureAction
      - 统一异常处理，返回 GUI 友好的结果字典

    使用方式：
        controller = RobotController()
        action = VisionCaptureGUIAction(
            controller     = controller,
            target_robot   = "robot1",
            confidence     = 0.7,
            debug_images   = True,
        )
        result = action.execute()
        # result = {"success": True/False, "error": None/"错误信息", "detail": "..."}
    """

    # GUI 默认参数（可被外部覆盖）
    DEFAULT_ROBOT       = "robot1"
    DEFAULT_CONFIDENCE  = 0.7
    DEFAULT_DEBUG        = True
    DEFAULT_VELOCITY    = 15
    DEFAULT_GRIP_LENGTH = 150.0
    DEFAULT_WORKFLOW = "bottle"  # "vertical" | "bottle"（bottle 同 grab_pingzi_baizhuo）

    def __init__(
        self,
        controller = None,
        target_robot: str = DEFAULT_ROBOT,
        confidence: float = DEFAULT_CONFIDENCE,
        debug_images: bool = DEFAULT_DEBUG,
        move_velocity: int = DEFAULT_VELOCITY,
        gripper_length: float = DEFAULT_GRIP_LENGTH,
        workflow: str = DEFAULT_WORKFLOW,
        frame_source: str = "auto",
        raise_on_error: bool = False,
    ):
        self.controller      = controller
        self.target_robot    = target_robot
        self.confidence      = confidence
        self.debug_images    = debug_images
        self.move_velocity   = move_velocity
        self.gripper_length  = gripper_length
        self.workflow        = workflow
        self.frame_source    = frame_source
        self.raise_on_error  = raise_on_error

        # 运行时
        self._action: Optional[VisionCaptureAction] = None
        self._result: dict = {"success": False, "error": None, "detail": ""}

    # ---- 公共 API ----

    def execute(self) -> dict:
        """
        执行视觉抓取动作。

        Returns:
            dict: {
                "success": bool,     # 是否完全成功
                "error":   str|None, # 错误描述（失败时）
                "detail":  str       # 执行详情日志
            }
        """
        detail_lines = []
        error_str = None

        try:
            # 构建底层 Action 实例
            self._action = VisionCaptureAction(
                controller             = self.controller,
                target_robot           = self.target_robot,
                confidence_threshold   = self.confidence,
                save_debug_images      = self.debug_images,
                move_velocity          = self.move_velocity,
                gripper_length         = self.gripper_length,
                workflow               = self.workflow,
                frame_source           = self.frame_source,
                raise_on_error         = self.raise_on_error,
            )

            detail_lines.append(f"[VisionCaptureGUIAction] 开始执行")
            detail_lines.append(f"  目标机械臂 : {self.target_robot}")
            detail_lines.append(f"  流程模式   : {self.workflow}")
            detail_lines.append(f"  取帧方式   : {self.frame_source}")
            detail_lines.append(f"  置信度阈值 : {self.confidence}")
            detail_lines.append(f"  调试图片   : {'是' if self.debug_images else '否'}")

            # 执行
            ok = self._action.execute()

            if ok:
                self._result = {
                    "success": True,
                    "error":   None,
                    "detail":  "\n".join(detail_lines) + "\n[VisionCaptureGUIAction] 执行成功 ✓"
                }
            else:
                error_str = self._action.last_error or "未知错误"
                detail_lines.append(f"[VisionCaptureGUIAction] 执行失败: {error_str}")
                self._result = {
                    "success": False,
                    "error":   error_str,
                    "detail":  "\n".join(detail_lines)
                }

        except Exception as exc:
            error_str = str(exc)
            detail_lines.append(f"[VisionCaptureGUIAction] 异常: {error_str}")
            self._result = {
                "success": False,
                "error":   error_str,
                "detail":  "\n".join(detail_lines)
            }
            if self.raise_on_error:
                raise

        finally:
            # 独立模式下关闭机械臂连接
            if self._action is not None:
                self._action.shutdown()

        return self._result

    @property
    def success(self) -> bool:
        return self._result.get("success", False)

    @property
    def error(self) -> Optional[str]:
        return self._result.get("error")

    @property
    def detail(self) -> str:
        return self._result.get("detail", "")

    # ---- GUI 辅助 ----

    @staticmethod
    def get_action_library_entry() -> dict:
        """
        返回供 GUI 动作库注册使用的 JSON 片段。
        直接贴入 actions_library.json 即可。
        """
        return {
            "name": "视觉抓取",
            "type": "execute",
            "script": "action_vision_capture_gui",
            "class": "VisionCaptureGUIAction",
            "description": (
                "通过深度相机 + YOLO 检测 + SAM 分割实现目标三维定位与机械臂抓取。"
                "流程：打开夹爪 → 首次检测定位 → 移动到预备位置 → 二次精确检测 → "
                "XY平面移动对准 → Z轴下降 → 夹取 → 返回初始位姿 → 释放。"
            ),
            "parameters": [
                {
                    "name":    "workflow",
                    "label":   "流程",
                    "type":    "select",
                    "options": ["vertical", "bottle"],
                    "default": "vertical",
                    "tooltip": "vertical：通用垂直抓取；bottle：grab_pingzi_baizhuo 瓶子+固定放置"
                },
                {
                    "name":    "frame_source",
                    "label":   "取帧",
                    "type":    "select",
                    "options": ["auto", "controller", "socket"],
                    "default": "auto",
                    "tooltip": "D435i 建议 GUI 提供 get_frames_from_gui，选 auto 或 controller"
                },
                {
                    "name":    "target_robot",
                    "label":   "目标机械臂",
                    "type":    "select",
                    "options": ["robot1", "robot2"],
                    "default": "robot1",
                    "tooltip": "执行抓取动作的机械臂编号"
                },
                {
                    "name":    "confidence",
                    "label":   "检测置信度",
                    "type":    "float",
                    "default": 0.7,
                    "min":     0.1,
                    "max":     1.0,
                    "step":    0.05,
                    "tooltip": "YOLO 检测置信度阈值，低于此值的检测结果将被忽略"
                },
                {
                    "name":    "debug_images",
                    "label":   "保存调试图片",
                    "type":    "bool",
                    "default": True,
                    "tooltip": "是否将检测结果、掩码图片保存到调试目录"
                },
                {
                    "name":    "move_velocity",
                    "label":   "移动速度",
                    "type":    "int",
                    "default": 15,
                    "min":     1,
                    "max":     100,
                    "tooltip": "机械臂运动速度 (mm/s)"
                },
                {
                    "name":    "gripper_length",
                    "label":   "夹爪长度(mm)",
                    "type":    "float",
                    "default": 100.0,
                    "min":     10.0,
                    "max":     500.0,
                    "tooltip": "夹爪长度，用于计算末端到目标物体的距离"
                },
            ],
            "returns": {
                "success": "bool - 抓取全流程是否成功完成",
                "error":   "str|None - 失败原因",
                "detail":  "str - 执行过程日志"
            },
            "dependencies": [
                "action_vision_capture.py",
                "vertical_grab/interface.py",
                "Robotic_Arm/rm_robot_interface.py",
                "best.pt (YOLO 模型)",
                "sam2.1_l.pt (SAM 模型)",
                "config.py"
            ],
            "known_issues": [
                "D435i：GUI 应对齐 depth 到 color 并传彩色内参；socket 模式需服务监听 localhost:12345",
                "YOLO/SAM 模型路径需根据部署环境修改（默认路径为开发机路径）",
                "workflow=bottle 需同目录 grab_pingzi_baizhuo.py"
            ]
        }


# ---------------------------------------------------------------
# 独立测试入口
# ---------------------------------------------------------------
if __name__ == "__main__":
    from src.arm_sdk.controller import RobotController

    print("=" * 60)
    print("VisionCaptureGUIAction 集成测试（使用 RobotController）")
    print("=" * 60)

    controller = None
    try:
        controller = RobotController()
        print("RobotController 已初始化")
    except Exception as e:
        print(f"RobotController 初始化失败: {e}")
        print("将使用独立模式（自建机械臂连接）")

    action = VisionCaptureGUIAction(
        controller     = controller,
        target_robot   = "robot1",
        confidence     = 0.7,
        debug_images   = True,
        raise_on_error = False,
    )

    result = action.execute()
    print("\n" + "=" * 40)
    print("执行结果:")
    print(f"  success : {result['success']}")
    print(f"  error   : {result['error']}")
    print(f"  detail  :\n{result['detail']}")

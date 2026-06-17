# -*- coding: utf-8 -*-
"""
视觉抓取统一执行入口。

GUI (ExecutionThread) 和 WebSocket (ActionExecutor) 共用此模块，消除重复代码。
"""

from __future__ import annotations

from typing import Callable

# log_fn: str -> None


def execute_vision_capture(
    controller,            # RobotController
    params: dict,
    log_fn: Callable[[str], None],
) -> bool:
    """视觉抓取统一执行入口。

    流程：
    1. 从 camera_factory 获取相机管理器 → 取原始帧 (color + depth + intrinsics)
    2. 注入 controller
    3. 创建 VisionCaptureGUIAction 并执行

    Args:
        controller: RobotController 实例
        params:    动作参数字典（与 ActionDefinition.parameters 一致）
                   - 目标机械臂 (str): robot1 / robot2
                   - 工作流 (str): bottle / vertical
                   - 置信度 (float)
                   - 调试图片 (bool)
                   - 移动速度 (int)
                   - 夹爪长度 (float)
        log_fn:    日志回调，签名为 (message: str) -> None

    Returns:
        bool: 执行成功/失败
    """
    from ..core.config_loader import Config

    target_robot = params.get("目标机械臂", "robot1")
    workflow = params.get("工作流", Config.get_instance().VISION_DEFAULT_WORKFLOW)
    confidence = float(params.get("置信度", Config.get_instance().VISION_DEFAULT_CONFIDENCE))
    debug_images = bool(params.get("调试图片", True))
    move_velocity = int(params.get("移动速度", Config.get_instance().VISION_DEFAULT_VELOCITY))
    gripper_length = float(params.get("夹爪长度", Config.get_instance().VISION_DEFAULT_GRIPPER_LENGTH))

    log_fn(f"视觉抓取动作: 机械臂={target_robot}, 工作流={workflow}")
    log_fn(f"  置信度={confidence}, 调试图片={debug_images}")

    if controller is None:
        log_fn("机械臂控制器未初始化")
        return False

    try:
        # ── 从 camera_factory 获取相机管理器取帧 ──
        from ..cameras.camera_factory import get_camera_manager

        mgr = get_camera_manager()
        if mgr is None:
            log_fn("相机管理器未启动，无法取帧")
            return False

        camera_name = Config.get_instance().VISION_CAMERA_NAME or None

        # 等待采集线程产出第一帧（D435 自动曝光/白平衡需 1~3 秒）
        import time
        deadline = time.time() + 10
        raw = None
        while time.time() < deadline:
            raw = mgr.get_latest_raw_frames(camera_name)
            if raw is not None:
                break
            time.sleep(0.2)

        if raw is None:
            info = mgr.get_cameras_info()
            online = [c["name"] for c in info if c.get("online")]
            log_fn(f"相机取帧失败：{camera_name or '(auto)'} 未获取到有效帧，在线相机: {online or '无'}")
            return False

        color, depth, intr = raw
        if color is None or depth is None or intr is None:
            log_fn("相机取帧失败：帧数据不完整")
            return False

        controller.inject_frames(color, depth, intr)

        from .capture_gui import VisionCaptureGUIAction

        action = VisionCaptureGUIAction(
            controller=controller,
            target_robot=target_robot,
            confidence=confidence,
            debug_images=debug_images,
            move_velocity=move_velocity,
            gripper_length=gripper_length,
            workflow=workflow,
            raise_on_error=False,
        )

        result = action.execute()

        if result.get("success"):
            log_fn("视觉抓取执行成功")
            return True
        else:
            error = result.get("error", "未知错误")
            log_fn(f"视觉抓取执行失败: {error}")
            return False

    except Exception as e:
        log_fn(f"执行视觉抓取出错: {str(e)}")
        return False

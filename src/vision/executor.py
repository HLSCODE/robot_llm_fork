# -*- coding: utf-8 -*-
"""
视觉抓取统一执行入口。

统一 ActionEngine 使用此模块执行视觉动作。
"""

from __future__ import annotations

from typing import Callable

# log_fn: str -> None


def execute_vision_capture(
    robot_system,
    camera,
    params: dict,
    log_fn: Callable[[str], None],
) -> bool:
    """视觉抓取统一执行入口。

    流程：
    1. 从 DeviceRuntime 注入的相机获取原始帧 (color + depth + intrinsics)
    2. 创建只依赖项目能力接口的 VisionCaptureAction 并执行

    Args:
        robot_system: DeviceRuntime 提供的 RobotSystem
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

    try:
        if camera is None:
            log_fn("相机管理器未启动，无法取帧")
            return False

        from .capture import VisionCaptureAction

        action = VisionCaptureAction(
            robot_system=robot_system,
            camera=camera,
            target_robot=target_robot,
            confidence_threshold=confidence,
            save_debug_images=debug_images,
            move_velocity=move_velocity,
            gripper_length=gripper_length,
            workflow=workflow,
            raise_on_error=False,
        )

        if action.execute():
            log_fn("视觉抓取执行成功")
            return True
        log_fn(f"视觉抓取执行失败: {action.last_error or '未知错误'}")
        return False

    except Exception as e:
        log_fn(f"执行视觉抓取出错: {str(e)}")
        return False

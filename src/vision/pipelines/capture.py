# -*- coding: utf-8 -*-
"""
视觉抓取统一执行入口。

统一 ActionEngine 使用此模块执行视觉动作。
"""

from __future__ import annotations

from typing import Callable, Literal, cast

from ...configuration.settings import VisionSettings
from ...devices import DepthCameraSource, RobotSystem
from ..models import VisionPipelineResult

# log_fn: str -> None


def execute_vision_capture(
    robot_system: RobotSystem,
    camera: DepthCameraSource,
    parameters: dict[str, object],
    settings: VisionSettings,
    log: Callable[[str], None],
    debug_directory: str,
) -> VisionPipelineResult:
    """视觉抓取统一执行入口。

    流程：
    1. 从 DeviceRuntime 注入的相机获取原始帧 (color + depth + intrinsics)
    2. 创建只依赖项目能力接口的 VisionCaptureAction 并执行

    Args:
        robot_system: DeviceRuntime 提供的 RobotSystem
        parameters: 动作参数字典（与 ActionDefinition.parameters 一致）
                   - 目标机械臂 (str): robot1 / robot2
                   - 工作流 (str): bottle / vertical
                   - 置信度 (float)
                   - 调试图片 (bool)
                   - 移动速度 (int)
                   - 夹爪长度 (float)
        log:       日志回调，签名为 (message: str) -> None

    Returns:
        Typed pipeline result with outcome and processed frame/inference counts.
    """
    target_robot = cast(
        Literal["robot1", "robot2"],
        _choice_parameter(
            parameters.get("目标机械臂", "robot1"),
            field="目标机械臂",
            choices=("robot1", "robot2"),
        ),
    )
    workflow = cast(
        Literal["vertical", "bottle"],
        _choice_parameter(
            parameters.get("工作流", settings.vision_default_workflow),
            field="工作流",
            choices=("vertical", "bottle"),
        ),
    )
    confidence = _float_parameter(
        parameters.get("置信度", settings.vision_default_confidence),
        "置信度",
    )
    debug_images = _bool_parameter(
        parameters.get("调试图片", True),
        "调试图片",
    )
    move_velocity = _int_parameter(
        parameters.get("移动速度", settings.vision_default_velocity),
        "移动速度",
    )
    gripper_length = _float_parameter(
        parameters.get("夹爪长度", settings.vision_default_gripper_length),
        "夹爪长度",
    )

    log(f"视觉抓取动作: 机械臂={target_robot}, 工作流={workflow}")
    log(f"  置信度={confidence}, 调试图片={debug_images}")

    try:
        from .grasp import VisionCaptureAction

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
            settings=settings,
            debug_save_root=debug_directory,
        )

        if action.execute():
            log("视觉抓取执行成功")
            return VisionPipelineResult(True, frames_processed=1, inference_count=1)
        log(f"视觉抓取执行失败: {action.last_error or '未知错误'}")
        return VisionPipelineResult(False, frames_processed=1, inference_count=1)

    except Exception as e:
        log(f"执行视觉抓取出错: {str(e)}")
        return VisionPipelineResult(False, frames_processed=0, inference_count=0)


def _choice_parameter(
    value: object,
    *,
    field: str,
    choices: tuple[str, ...],
) -> str:
    normalized = str(value).strip().lower()
    if normalized not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(choices)}")
    return normalized


def _float_parameter(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{field} must be numeric")
    return float(value)


def _int_parameter(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"{field} must be an integer")
    return int(value)


def _bool_parameter(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise TypeError(f"{field} must be a boolean")

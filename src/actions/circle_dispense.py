from __future__ import annotations

import math
import time
from contextlib import nullcontext
from typing import Callable

from ..core.pose_compensation import parse_pose


LogFn = Callable[[str, str], None]
StateFn = Callable[[], bool]


def _to_float(params: dict, key: str, default: float) -> float:
    value = params.get(key, default)
    if value is None or value == "":
        return float(default)
    return float(value)


def _to_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}
    return bool(value)


def _radius_to_meters(radius: float) -> float:
    # Poses in this project use meters. For operator friendliness, values
    # larger than 1 are treated as millimeters.
    return radius / 1000.0 if abs(radius) > 1.0 else radius


def execute_right_arm_circle_dispense(
    *,
    robot_controller,
    params: dict,
    default_port: str,
    log: LogFn,
    stop_requested: StateFn | None = None,
    paused: StateFn | None = None,
) -> bool:
    """Move Robot2 around a circle centered at pose x/y while dispensing."""
    stop_requested = stop_requested or (lambda: False)
    paused = paused or (lambda: False)

    if robot_controller is None:
        log("机械臂控制器未初始化", "error")
        return False

    ctrl = getattr(robot_controller, "robot2_ctrl", None)
    robot = getattr(ctrl, "robot", None)
    if robot is None:
        log("robot2 未连接", "error")
        return False

    try:
        center_pose = parse_pose(params.get("位姿", params.get("中心位姿", "")))
        radius = _radius_to_meters(_to_float(params, "半径R", 10.0))
        dispense_speed = _to_float(params, "吐液速度", 800.0)
        volume = _to_float(params, "吐液量", params.get("容量", 500.0))
        circle_count = _to_float(params, "圈数", 1.0)
        segments = max(8, int(_to_float(params, "分段数", 72.0)))
        move_velocity = int(_to_float(params, "运动速度", 10.0))
        blend_radius = int(_to_float(params, "过渡半径", params.get("平滑半径", 20.0)))
        continuous_motion = _to_bool(params.get("连续运动"), True)
        clockwise = _to_bool(params.get("顺时针"), False)
        port = params.get("端口", default_port)
    except Exception as exc:
        log(f"右臂转圈注液参数错误: {exc}", "error")
        return False

    if radius <= 0:
        log("半径R必须大于0", "error")
        return False
    if dispense_speed <= 0:
        log("吐液速度必须大于0", "error")
        return False
    if volume <= 0:
        log("吐液量必须大于0", "error")
        return False
    if circle_count <= 0:
        log("圈数必须大于0", "error")
        return False
    if blend_radius < 0:
        log("过渡半径不能小于0", "error")
        return False

    duration = volume / dispense_speed
    total_segments = max(8, int(math.ceil(segments * circle_count)))
    direction = -1.0 if clockwise else 1.0
    cx, cy, z, rx, ry, rz = center_pose

    def circle_pose(step: int) -> list[float]:
        angle = direction * 2.0 * math.pi * circle_count * step / total_segments
        return [
            cx + radius * math.cos(angle),
            cy + radius * math.sin(angle),
            z,
            rx,
            ry,
            rz,
        ]

    lock_factory = getattr(robot_controller, "_sdk_lock_for_robot", None)
    sdk_lock = lock_factory(robot) if lock_factory else nullcontext()

    try:
        log(
            "右臂转圈注液: "
            f"center=({cx:.4f}, {cy:.4f}, {z:.4f}), "
            f"R={radius * 1000:.1f}mm, volume={volume:.1f}ul, "
            f"speed={dispense_speed:.1f}ul/s, duration={duration:.2f}s, "
            f"segments={total_segments}, blend={blend_radius}, continuous={continuous_motion}"
        )

        start_pose = circle_pose(0)
        log("移动到圆周起点...")
        with sdk_lock:
            ret = robot.rm_movel(start_pose, v=move_velocity, r=0, connect=0, block=1)
        if ret != 0:
            log(f"移动到圆周起点失败，错误码: {ret}", "error")
            return False

        from ..devices import ADP

        adp = ADP(port=port)
        try:
            log(f"设置吐液速度: {dispense_speed:.1f}ul/s")
            if not adp.set_dispense_speed(int(round(dispense_speed))):
                log("设置吐液速度失败", "error")
                return False

            log(f"开始吐液: {volume:.1f}ul")
            if not adp.dispense(int(round(volume))):
                log("吐液命令发送失败", "error")
                return False
        finally:
            adp.close()

        start_time = time.monotonic()
        for step in range(1, total_segments + 1):
            if stop_requested():
                log("右臂转圈注液已停止")
                return False
            while paused():
                if stop_requested():
                    log("右臂转圈注液已停止")
                    return False
                time.sleep(0.1)

            target_pose = circle_pose(step)
            is_last = step == total_segments
            connect = 1 if continuous_motion and not is_last else 0
            block = 0 if continuous_motion and not is_last else 1
            r = blend_radius if continuous_motion and not is_last else 0
            with sdk_lock:
                ret = robot.rm_movel(target_pose, v=move_velocity, r=r, connect=connect, block=block)
            if ret != 0:
                log(f"圆周第 {step}/{total_segments} 段移动失败，错误码: {ret}", "error")
                return False

            if not continuous_motion:
                target_elapsed = duration * step / total_segments
                remaining = target_elapsed - (time.monotonic() - start_time)
                if remaining > 0:
                    time.sleep(remaining)

        extra_wait = duration - (time.monotonic() - start_time)
        if extra_wait > 0:
            time.sleep(extra_wait)

        log("右臂转圈注液完成")
        return True
    except Exception as exc:
        log(f"右臂转圈注液执行异常: {exc}", "error")
        return False

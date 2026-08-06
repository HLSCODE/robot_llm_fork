from __future__ import annotations

import math
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable, TypedDict

from ...geometry.pose_compensation import parse_pose
from ...devices import (
    ArmId,
    ArmMotion,
    CartesianPose,
    MotionMode,
    MotionOptions,
    Pipette,
)


StateFn = Callable[[], bool]
LogFn = Callable[[str, str], None]


class _ConcurrentResults(TypedDict):
    motion: bool
    dispense: bool
    motion_error: str
    dispense_error: str


def _to_float(params: dict[str, Any], key: str, default: Any) -> float:
    value = params.get(key, default)
    if value is None or value == "":
        return float(default)
    return float(value)


def _to_bool(value: Any, default: bool = False) -> bool:
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
    robot_motion: ArmMotion,
    pipette: Pipette,
    params: dict[str, Any],
    log: LogFn,
    stop_requested: StateFn | None = None,
    paused: StateFn | None = None,
) -> bool:
    """Move Robot2 around a circle centered at pose x/y while dispensing."""
    stop_requested = stop_requested or (lambda: False)
    paused = paused or (lambda: False)

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

    try:
        log(
            "右臂转圈注液: "
            f"center=({cx:.4f}, {cy:.4f}, {z:.4f}), "
            f"R={radius * 1000:.1f}mm, volume={volume:.1f}ul, "
            f"speed={dispense_speed:.1f}ul/s, duration={duration:.2f}s, "
            f"segments={total_segments}, blend={blend_radius}, continuous={continuous_motion}",
            "info",
        )

        start_pose = circle_pose(0)
        log("移动到圆周起点...", "info")
        robot_motion.move_to_pose(
            ArmId.RIGHT,
            CartesianPose.from_iterable(start_pose),
            MotionMode.LINEAR,
            MotionOptions(velocity_percent=move_velocity),
        )

        with nullcontext(pipette) as adp:
            log(f"设置吐液速度: {dispense_speed:.1f}ul/s", "info")
            if not adp.set_dispense_speed(int(round(dispense_speed))):
                log("设置吐液速度失败", "error")
                return False

            start_signal = threading.Event()
            started_at = [0.0]
            results: _ConcurrentResults = {
                "motion": False,
                "dispense": False,
                "motion_error": "",
                "dispense_error": "",
            }

            def run_dispense() -> None:
                try:
                    start_signal.wait()
                    log(f"开始吐液: {volume:.1f}ul", "info")
                    if adp.dispense(int(round(volume))):
                        results["dispense"] = True
                    else:
                        results["dispense_error"] = "吐液命令发送失败"
                except Exception as exc:
                    results["dispense_error"] = f"吐液线程异常: {exc}"

            def run_motion() -> None:
                try:
                    start_signal.wait()
                    for step in range(1, total_segments + 1):
                        if stop_requested():
                            results["motion_error"] = "右臂转圈注液已停止"
                            return
                        while paused():
                            if stop_requested():
                                results["motion_error"] = "右臂转圈注液已停止"
                                return
                            time.sleep(0.1)

                        target_pose = circle_pose(step)
                        is_last = step == total_segments
                        connect = 1 if continuous_motion and not is_last else 0
                        block = 0 if continuous_motion and not is_last else 1
                        r = blend_radius if continuous_motion and not is_last else 0
                        robot_motion.move_to_pose(
                            ArmId.RIGHT,
                            CartesianPose.from_iterable(target_pose),
                            MotionMode.LINEAR,
                            MotionOptions(
                                velocity_percent=move_velocity,
                                blend_radius=r,
                                connected=bool(connect),
                                blocking=bool(block),
                            ),
                        )

                        if not continuous_motion:
                            target_elapsed = duration * step / total_segments
                            remaining = target_elapsed - (time.monotonic() - started_at[0])
                            if remaining > 0:
                                time.sleep(remaining)

                    results["motion"] = True
                except Exception as exc:
                    results["motion_error"] = f"运动线程异常: {exc}"

            motion_thread = threading.Thread(target=run_motion, name="CircleDispenseMotion")
            dispense_thread = threading.Thread(target=run_dispense, name="CircleDispenseADP")
            motion_thread.start()
            dispense_thread.start()

            log("同步启动右臂转圈与吐液...", "info")
            started_at[0] = time.monotonic()
            start_signal.set()

            motion_thread.join()
            dispense_thread.join()

            if not results["dispense"]:
                log(results["dispense_error"] or "吐液命令发送失败", "error")
                return False
            if not results["motion"]:
                log(results["motion_error"] or "圆周运动失败", "error")
                return False

            extra_wait = duration - (time.monotonic() - started_at[0])
            if extra_wait > 0:
                time.sleep(extra_wait)
        log("右臂转圈注液完成", "info")
        return True
    except Exception as exc:
        log(f"右臂转圈注液执行异常: {exc}", "error")
        return False

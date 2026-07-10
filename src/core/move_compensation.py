from __future__ import annotations

from typing import Callable

from .execution_context import ExecutionContext
from .pose_compensation import compensate_pose, parse_pose


LogFn = Callable[[str], None]


def _normalize_mode(mode: str | None) -> str:
    text = str(mode or "").strip().lower()
    if text in {"", "none", "不补偿", "无"}:
        return "none"
    if text in {"udp", "udp_tag", "定位补偿", "udp定位补偿"}:
        return "udp"
    if text in {"vision", "visual", "视觉补偿", "视觉重定位补偿"}:
        return "vision"
    return text


def _legacy_compensation_config(params: dict) -> dict:
    legacy = params.get("定位补偿", {})
    if legacy.get("enabled"):
        return {
            "mode": "udp",
            "udp": legacy,
        }
    return {"mode": "none"}


def resolve_robot_target_pose(
    params: dict,
    arm: str,
    context: ExecutionContext,
    log_fn: LogFn | None = None,
) -> list[float]:
    """Resolve a robot move target after optional UDP or vision compensation."""
    log = log_fn or (lambda message: None)
    target_pose = parse_pose(params.get("点位", ""))
    compensation = params.get("补偿") or _legacy_compensation_config(params)
    mode = _normalize_mode(compensation.get("mode") or compensation.get("方式"))

    if mode == "none":
        return target_pose

    if mode == "udp":
        udp_config = compensation.get("udp") or compensation
        teach_offset = udp_config.get("teach_offset")
        if not teach_offset:
            raise RuntimeError("UDP定位补偿已启用，但动作中缺少创建时定位基准")

        from ..gui.udp_receive import get_latest_position

        current_offset = get_latest_position(max_age=2.0, wait_timeout=1.5)
        if current_offset is None:
            raise RuntimeError("UDP定位补偿已启用，但未收到当前有效定位数据")

        resolved = compensate_pose(target_pose, teach_offset, current_offset, arm=arm)
        log(
            "UDP定位补偿: "
            f"teach=({teach_offset.get('x')}, {teach_offset.get('y')}, {teach_offset.get('angle')}) "
            f"current=({current_offset.get('x')}, {current_offset.get('y')}, {current_offset.get('angle')})"
        )
        log(f"补偿后点位: {resolved}")
        return resolved

    if mode == "vision":
        vision_config = compensation.get("vision") or compensation
        station_id = str(vision_config.get("station_id") or vision_config.get("工位ID") or "").strip()
        if not station_id:
            raise RuntimeError("视觉补偿已启用，但动作中缺少工位ID")
        from ..vision.relocalization import compensate_pose_with_context

        resolved = compensate_pose_with_context(
            target_pose,
            station_id,
            arm,
            context,
            mode=vision_config.get("mode") or vision_config.get("compensation_mode"),
            planar_constraint=vision_config.get("planar_constraint"),
        )
        log(
            "视觉重定位补偿: "
            f"station={station_id}, arm={arm}, mode={vision_config.get('mode') or 'default'}"
        )
        log(f"补偿后点位: {resolved}")
        return resolved

    raise RuntimeError(f"未知补偿方式: {mode}")

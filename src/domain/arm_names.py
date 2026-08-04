"""Canonical arm-name normalization shared across domain boundaries."""


def normalize_arm_name(arm: str | None) -> str:
    text = str(arm or "").strip().lower()
    if text in {"left", "l", "left_arm", "robot1", "r1", "1", "左", "左臂"}:
        return "left"
    if text in {"right", "r", "right_arm", "robot2", "r2", "2", "右", "右臂"}:
        return "right"
    return text or "left"

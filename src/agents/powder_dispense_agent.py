"""Closed-loop powder dispensing agent."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import uuid4

from .powder_dispense_logger import append_powder_dispense_log


class PowderController(Protocol):
    def enable_all(self) -> None: ...
    def lift_to_dispense(self, position: int) -> None: ...
    def lift_to_safe(self, position: int) -> None: ...
    def rotation_move_relative(self, delta_steps: int) -> None: ...
    def rotation_to_home(self, position: int) -> None: ...
    def rotation_stop(self) -> None: ...
    def close(self) -> None: ...


@dataclass
class PowderDispenseResult:
    success: bool
    initial_g: float
    final_g: float
    target_mg: float
    added_mg: float
    rounds: int
    message: str


@dataclass
class PowderDispenseConfig:
    target_mg: float = 100.0
    tolerance_mg: float = 5.0
    max_rounds: int = 20
    settle_seconds: float = 2.0
    lift_safe_position: int = 0
    lift_dispense_position: int = 50000
    rotation_home_position: int = 0
    large_step: int = 20000
    medium_step: int = 8000
    small_step: int = 2000
    micro_step: int = 500
    max_read_failures: int = 3
    max_drop_mg: float = 10.0


class PowderDispenseAgent:
    """Use balance feedback to add powder until the target delta is reached."""

    def __init__(
        self,
        controller_factory: Callable[[], PowderController],
        read_balance: Callable[[], float],
        *,
        log: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._controller_factory = controller_factory
        self._read_balance = read_balance
        self._log = log or (lambda _msg: None)
        self._should_stop = should_stop or (lambda: False)

    def run(self, config: PowderDispenseConfig, context: dict[str, Any] | None = None) -> PowderDispenseResult:
        if config.target_mg <= 0:
            raise ValueError("目标重量mg必须大于0")
        if config.tolerance_mg <= 0:
            raise ValueError("容差mg必须大于0")
        if config.max_rounds <= 0:
            raise ValueError("最大轮次必须大于0")

        ctrl: PowderController | None = None
        initial_g = 0.0
        current_g = 0.0
        rounds = 0
        round_records: list[dict[str, Any]] = []
        result: PowderDispenseResult | None = None
        run_id = str(uuid4())
        context = context or {}

        try:
            ctrl = self._controller_factory()
            ctrl.enable_all()
            initial_g = self._read_balance_retry(config)
            current_g = initial_g
            target_final_g = initial_g + config.target_mg / 1000.0

            self._log(
                f"智能加粉开始: 初始={initial_g:.4f}g, "
                f"目标增加={config.target_mg:.1f}mg, 目标终点={target_final_g:.4f}g"
            )
            ctrl.lift_to_dispense(config.lift_dispense_position)

            for rounds in range(1, config.max_rounds + 1):
                if self._should_stop():
                    result = self._result(False, initial_g, current_g, config, rounds - 1, "用户停止")
                    return result

                added_mg = (current_g - initial_g) * 1000.0
                remaining_mg = config.target_mg - added_mg

                if remaining_mg < -config.tolerance_mg:
                    result = self._result(True, initial_g, current_g, config, rounds - 1, "加粉超量")
                    return result
                if remaining_mg <= config.tolerance_mg:
                    result = self._result(True, initial_g, current_g, config, rounds - 1, "达到目标")
                    return result

                step = self._choose_step(remaining_mg, config)
                self._log(
                    f"第{rounds}轮: 当前={current_g:.4f}g, "
                    f"已加={added_mg:.1f}mg, 剩余={remaining_mg:.1f}mg, 旋转={step}步"
                )
                ctrl.rotation_move_relative(step)
                time.sleep(config.settle_seconds)

                previous_g = current_g
                current_g = self._read_balance_retry(config)
                delta_mg = (current_g - previous_g) * 1000.0
                round_records.append(
                    {
                        "round": rounds,
                        "before_g": previous_g,
                        "after_g": current_g,
                        "before_added_mg": added_mg,
                        "remaining_mg": remaining_mg,
                        "rotation_steps": step,
                        "delta_mg": delta_mg,
                        "settle_seconds": config.settle_seconds,
                    }
                )
                if delta_mg < -config.max_drop_mg:
                    result = self._result(False, initial_g, current_g, config, rounds, "读数异常下降")
                    return result

            result = self._result(True, initial_g, current_g, config, config.max_rounds, "达到最大轮次，继续后续流程")
            return result
        except Exception as exc:
            result = self._result(False, initial_g, current_g, config, rounds, f"异常: {exc}")
            raise
        finally:
            if ctrl is not None:
                self._return_safe(ctrl, config)
            self._write_run_log(run_id, context, config, result, round_records)

    def _read_balance_retry(self, config: PowderDispenseConfig) -> float:
        last_error: Exception | None = None
        for attempt in range(1, config.max_read_failures + 1):
            try:
                return float(self._read_balance())
            except Exception as exc:
                last_error = exc
                self._log(f"天平读数失败 ({attempt}/{config.max_read_failures}): {exc}")
                time.sleep(0.5)
        raise RuntimeError(f"连续读取天平失败: {last_error}")

    @staticmethod
    def _choose_step(remaining_mg: float, config: PowderDispenseConfig) -> int:
        if remaining_mg > 25:
            return config.large_step
        if remaining_mg > 10:
            return config.medium_step
        if remaining_mg > 3:
            return config.small_step
        return config.micro_step

    def _return_safe(self, ctrl: PowderController, config: PowderDispenseConfig) -> None:
        for label, action in (
            ("停止旋转", ctrl.rotation_stop),
            ("升降回安全位置", lambda: ctrl.lift_to_safe(config.lift_safe_position)),
            ("旋转回原点", lambda: ctrl.rotation_to_home(config.rotation_home_position)),
            ("关闭串口", ctrl.close),
        ):
            try:
                action()
            except Exception as exc:
                self._log(f"{label}失败: {exc}")

    def _write_run_log(
        self,
        run_id: str,
        context: dict[str, Any],
        config: PowderDispenseConfig,
        result: PowderDispenseResult | None,
        round_records: list[dict[str, Any]],
    ) -> None:
        if result is None:
            result = self._result(False, 0.0, 0.0, config, 0, "未生成加粉结果")

        record = {
            "run_id": run_id,
            "task_name": context.get("task_name") or "manual_sequence",
            "action_name": context.get("action_name") or "智能加粉",
            "target_mg": config.target_mg,
            "tolerance_mg": config.tolerance_mg,
            "max_rounds": config.max_rounds,
            "settle_seconds": config.settle_seconds,
            "step_config": {
                "large_step": config.large_step,
                "medium_step": config.medium_step,
                "small_step": config.small_step,
                "micro_step": config.micro_step,
            },
            "initial_g": result.initial_g,
            "final_g": result.final_g,
            "added_mg": result.added_mg,
            "success": result.success,
            "message": result.message,
            "rounds": result.rounds,
            "round_records": round_records,
        }
        try:
            path = append_powder_dispense_log(record)
            self._log(f"加粉结构化日志已写入: {path}")
        except Exception as exc:
            self._log(f"加粉结构化日志写入失败: {exc}")

    @staticmethod
    def _result(
        success: bool,
        initial_g: float,
        final_g: float,
        config: PowderDispenseConfig,
        rounds: int,
        message: str,
    ) -> PowderDispenseResult:
        return PowderDispenseResult(
            success=success,
            initial_g=initial_g,
            final_g=final_g,
            target_mg=config.target_mg,
            added_mg=(final_g - initial_g) * 1000.0,
            rounds=rounds,
            message=message,
        )


def config_from_params(params: dict, tapping_config: dict | None = None) -> PowderDispenseConfig:
    cfg = tapping_config or {}
    return PowderDispenseConfig(
        target_mg=float(params.get("目标重量mg", params.get("target_mg", 100))),
        tolerance_mg=float(params.get("容差mg", params.get("tolerance_mg", 5))),
        max_rounds=int(params.get("最大轮次", params.get("max_rounds", 20))),
        settle_seconds=float(params.get("稳定等待秒数", params.get("settle_seconds", 2.0))),
        lift_safe_position=int(params.get("安全位置步数", cfg.get("lift_safe_position", 0))),
        lift_dispense_position=int(params.get("加粉位置步数", cfg.get("lift_dispense_position", 50000))),
        rotation_home_position=int(params.get("旋转原点步数", cfg.get("rotation_home_position", 0))),
        large_step=int(params.get("大步步数", cfg.get("powder_large_step", 20000))),
        medium_step=int(params.get("中步步数", cfg.get("powder_medium_step", 8000))),
        small_step=int(params.get("小步步数", cfg.get("powder_small_step", 2000))),
        micro_step=int(params.get("微步步数", cfg.get("powder_micro_step", 500))),
    )

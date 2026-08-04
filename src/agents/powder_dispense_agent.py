"""Closed-loop powder dispensing agent."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class PowderController(Protocol):
    def enable_all(self) -> None: ...
    def lift_to_dispense(self, position: int) -> None: ...
    def lift_to_safe(self, position: int) -> None: ...
    def rotation_move_relative(self, delta_steps: int) -> None: ...
    def rotation_to_home(self, position: int) -> None: ...
    def rotation_stop(self) -> None: ...


class PowderDispenseOutcome(str, Enum):
    """Terminal outcomes for one closed-loop dispensing attempt."""

    TARGET_REACHED = "target_reached"
    MAX_ROUNDS_REACHED = "max_rounds_reached"
    STOPPED = "stopped"
    OVER_TARGET = "over_target"
    READING_ANOMALY = "reading_anomaly"


class PowderRoundOutcome(str, Enum):
    CONTINUE = "continue"
    TARGET_REACHED = "target_reached"
    OVER_TARGET = "over_target"
    READING_ANOMALY = "reading_anomaly"


_TERMINAL_ROUND_OUTCOMES = {
    PowderRoundOutcome.TARGET_REACHED: (
        PowderDispenseOutcome.TARGET_REACHED,
        "达到目标",
    ),
    PowderRoundOutcome.OVER_TARGET: (
        PowderDispenseOutcome.OVER_TARGET,
        "加粉超量",
    ),
    PowderRoundOutcome.READING_ANOMALY: (
        PowderDispenseOutcome.READING_ANOMALY,
        "读数异常下降",
    ),
}


@dataclass(frozen=True, slots=True)
class PowderDispenseRound:
    round_number: int
    reading_before_g: float
    reading_after_g: float
    added_before_mg: float
    remaining_before_mg: float
    tolerance_mg: float
    rotation_steps: int
    round_delta_mg: float
    outcome: PowderRoundOutcome


@dataclass(frozen=True, slots=True)
class PowderDispenseResult:
    outcome: PowderDispenseOutcome
    initial_g: float
    final_g: float
    target_mg: float
    added_mg: float
    rounds: int
    message: str
    round_records: tuple[PowderDispenseRound, ...] = ()

    @property
    def successful(self) -> bool:
        return self.outcome is PowderDispenseOutcome.TARGET_REACHED


@dataclass(frozen=True, slots=True)
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
    large_step_threshold_mg: float = 25.0
    medium_step_threshold_mg: float = 10.0
    small_step_threshold_mg: float = 3.0
    max_read_failures: int = 3
    read_retry_delay_seconds: float = 0.5
    max_drop_mg: float = 10.0

    def __post_init__(self) -> None:
        positive_values = {
            "目标重量mg": self.target_mg,
            "容差mg": self.tolerance_mg,
            "最大轮次": self.max_rounds,
            "大步步数": self.large_step,
            "中步步数": self.medium_step,
            "小步步数": self.small_step,
            "微步步数": self.micro_step,
            "最大读数失败次数": self.max_read_failures,
            "最大允许下降mg": self.max_drop_mg,
        }
        for label, value in positive_values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label}必须大于0")
        if self.settle_seconds < 0:
            raise ValueError("稳定等待秒数不能小于0")
        if self.read_retry_delay_seconds < 0:
            raise ValueError("读数重试等待秒数不能小于0")
        if not (
            math.isfinite(self.large_step_threshold_mg)
            and math.isfinite(self.medium_step_threshold_mg)
            and math.isfinite(self.small_step_threshold_mg)
            and self.large_step_threshold_mg
            > self.medium_step_threshold_mg
            > self.small_step_threshold_mg
            > 0
        ):
            raise ValueError("步数阈值必须满足 大步阈值 > 中步阈值 > 小步阈值 > 0")


def choose_rotation_step(
    remaining_mg: float,
    config: PowderDispenseConfig,
) -> int:
    """Choose one configured rotation increment for the remaining powder mass."""
    if remaining_mg > config.large_step_threshold_mg:
        return config.large_step
    if remaining_mg > config.medium_step_threshold_mg:
        return config.medium_step
    if remaining_mg > config.small_step_threshold_mg:
        return config.small_step
    return config.micro_step


class PowderDispenseAgent:
    """Use balance feedback to add powder until the target delta is reached."""

    def __init__(
        self,
        controller: PowderController,
        read_balance: Callable[[], float],
        *,
        log: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] | None = None,
        audit_round: Callable[[PowderDispenseRound], None] | None = None,
    ) -> None:
        self._controller = controller
        self._read_balance = read_balance
        self._log = log or (lambda _msg: None)
        self._should_stop = should_stop or (lambda: False)
        self._sleep = sleep or time.sleep
        self._audit_round = audit_round or (lambda _record: None)

    def run(self, config: PowderDispenseConfig) -> PowderDispenseResult:
        ctrl = self._controller
        initial_g = 0.0
        current_g = 0.0
        rounds = 0
        round_records: list[PowderDispenseRound] = []

        try:
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
                    return self._result(
                        PowderDispenseOutcome.STOPPED,
                        initial_g,
                        current_g,
                        config,
                        rounds - 1,
                        "用户停止",
                        round_records,
                    )

                added_mg = (current_g - initial_g) * 1000.0
                remaining_mg = config.target_mg - added_mg

                if remaining_mg < -config.tolerance_mg:
                    return self._result(
                        PowderDispenseOutcome.OVER_TARGET,
                        initial_g,
                        current_g,
                        config,
                        rounds - 1,
                        "加粉超量",
                        round_records,
                    )
                if remaining_mg <= config.tolerance_mg:
                    return self._result(
                        PowderDispenseOutcome.TARGET_REACHED,
                        initial_g,
                        current_g,
                        config,
                        rounds - 1,
                        "达到目标",
                        round_records,
                    )

                step = choose_rotation_step(remaining_mg, config)
                self._log(
                    f"第{rounds}轮: 当前={current_g:.4f}g, "
                    f"已加={added_mg:.1f}mg, 剩余={remaining_mg:.1f}mg, 旋转={step}步"
                )
                ctrl.rotation_move_relative(step)
                self._sleep(config.settle_seconds)

                previous_g = current_g
                current_g = self._read_balance_retry(config)
                delta_mg = (current_g - previous_g) * 1000.0
                outcome = self._round_outcome(
                    current_g=current_g,
                    initial_g=initial_g,
                    config=config,
                    delta_mg=delta_mg,
                )
                record = PowderDispenseRound(
                    round_number=rounds,
                    reading_before_g=previous_g,
                    reading_after_g=current_g,
                    added_before_mg=added_mg,
                    remaining_before_mg=remaining_mg,
                    tolerance_mg=config.tolerance_mg,
                    rotation_steps=step,
                    round_delta_mg=delta_mg,
                    outcome=outcome,
                )
                round_records.append(record)
                self._audit_round(record)
                terminal = _TERMINAL_ROUND_OUTCOMES.get(outcome)
                if terminal is not None:
                    terminal_outcome, message = terminal
                    return self._result(
                        terminal_outcome,
                        initial_g,
                        current_g,
                        config,
                        rounds,
                        message,
                        round_records,
                    )

            return self._result(
                PowderDispenseOutcome.MAX_ROUNDS_REACHED,
                initial_g,
                current_g,
                config,
                config.max_rounds,
                "达到最大轮次但未达到目标",
                round_records,
            )
        finally:
            self._return_safe(ctrl, config)

    def _read_balance_retry(self, config: PowderDispenseConfig) -> float:
        last_error: Exception | None = None
        for attempt in range(1, config.max_read_failures + 1):
            try:
                reading_g = float(self._read_balance())
                if not math.isfinite(reading_g):
                    raise ValueError("天平读数必须是有限数值")
                return reading_g
            except Exception as exc:
                last_error = exc
                self._log(f"天平读数失败 ({attempt}/{config.max_read_failures}): {exc}")
                if attempt < config.max_read_failures:
                    self._sleep(config.read_retry_delay_seconds)
        assert last_error is not None
        raise RuntimeError(f"连续读取天平失败: {last_error}") from last_error

    def _return_safe(self, ctrl: PowderController, config: PowderDispenseConfig) -> None:
        for label, action in (
            ("停止旋转", ctrl.rotation_stop),
            ("升降回安全位置", lambda: ctrl.lift_to_safe(config.lift_safe_position)),
            ("旋转回原点", lambda: ctrl.rotation_to_home(config.rotation_home_position)),
        ):
            try:
                action()
            except Exception as exc:
                self._log(f"{label}失败: {exc}")

    @staticmethod
    def _round_outcome(
        *,
        current_g: float,
        initial_g: float,
        config: PowderDispenseConfig,
        delta_mg: float,
    ) -> PowderRoundOutcome:
        if delta_mg < -config.max_drop_mg:
            return PowderRoundOutcome.READING_ANOMALY
        remaining_mg = config.target_mg - (current_g - initial_g) * 1000.0
        if remaining_mg < -config.tolerance_mg:
            return PowderRoundOutcome.OVER_TARGET
        if remaining_mg <= config.tolerance_mg:
            return PowderRoundOutcome.TARGET_REACHED
        return PowderRoundOutcome.CONTINUE

    @staticmethod
    def _result(
        outcome: PowderDispenseOutcome,
        initial_g: float,
        final_g: float,
        config: PowderDispenseConfig,
        rounds: int,
        message: str,
        round_records: list[PowderDispenseRound],
    ) -> PowderDispenseResult:
        return PowderDispenseResult(
            outcome=outcome,
            initial_g=initial_g,
            final_g=final_g,
            target_mg=config.target_mg,
            added_mg=(final_g - initial_g) * 1000.0,
            rounds=rounds,
            message=message,
            round_records=tuple(round_records),
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
        large_step_threshold_mg=float(
            params.get(
                "大步阈值mg",
                cfg.get("powder_large_step_threshold_mg", 25.0),
            )
        ),
        medium_step_threshold_mg=float(
            params.get(
                "中步阈值mg",
                cfg.get("powder_medium_step_threshold_mg", 10.0),
            )
        ),
        small_step_threshold_mg=float(
            params.get(
                "小步阈值mg",
                cfg.get("powder_small_step_threshold_mg", 3.0),
            )
        ),
        read_retry_delay_seconds=float(
            params.get("读数重试等待秒数", 0.5)
        ),
    )

"""GUI-thread orchestration; robot calls and persistence stay on workers."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from ...application.services import ApplicationServices
from ...devices.runtime.arm_models import ArmId, ArmState
from ...domain.models import ActionDefinition, ActionType
from ..app_dialogs import ask_text
from ..views.teaching import TeachingView

logger = logging.getLogger(__name__)
STATE_MAX_AGE_SECONDS = 2.0
MOTION_TIMEOUT_SECONDS = 15.0


class TeachingController(QObject):
    session_changed = Signal(bool)
    problem = Signal(str)

    def __init__(self, view: TeachingView, services: ApplicationServices, parent: QObject) -> None:
        super().__init__(parent)
        self.view = view
        self.services = services
        self.teaching = services.manual_control.teaching
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teaching-state")
        self._commands = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teaching-command")
        self._stopper = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teaching-stop")
        self._read: Future[ArmState] | None = None
        self._command: Future[None] | None = None
        self._stop: Future[None] | None = None
        self._last_state_at = 0.0
        self._command_started = 0.0
        self._closing = False
        self._announced_active = False
        self._saving = False
        view.enter_requested.connect(self.begin)
        view.stop_requested.connect(self.stop)
        view.hidden.connect(self.stop)
        view.step_requested.connect(self.step)
        view.save_requested.connect(self.save)
        provider = services.settings.robot.provider
        view.frame_label.setText(
            "参考坐标系：世界坐标系" if provider == "tianji" else "参考坐标系：机械臂基座"
        )
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def begin(self) -> None:
        if self._closing or self._command is not None or self._read is not None or self._stop is not None:
            return
        try:
            self.teaching.begin(ArmId.parse(self.view.arm.currentData()))
        except Exception as error:
            self._report("无法进入示教", error)
            return
        self._last_state_at = 0.0
        self._set_active(True)
        self.view.status.setText("已取得控制权，正在读取实际状态…")
        self._tick()

    def step(self, joint: bool, axis: int, amount: float, speed: int) -> None:
        if not self._ready() or self._command is not None or self._closing:
            return
        self._saving = False
        self._command_started = time.monotonic()
        self._command = self._commands.submit(
            self.teaching.step, joint=joint, axis=axis, amount=amount, speed=speed,
        )
        self.view.status.setText("单步移动中；可点击停止，禁止连续提交")
        self._render_controls()

    def stop(self) -> None:
        if not self.teaching.active:
            return
        self.teaching.request_stop()
        if self._stop is None:
            self._stop = self._stopper.submit(self.teaching.close)
        self.view.status.setText("正在停止并释放示教控制权…")
        self._render_controls()

    def _ready(self) -> bool:
        return (
            self.teaching.enabled
            and self._last_state_at > 0
            and 0 <= time.monotonic() - self._last_state_at < STATE_MAX_AGE_SECONDS
        )

    def _tick(self) -> None:
        if self._closing:
            return
        if self._stop is not None and self._stop.done():
            stop, self._stop = self._stop, None
            try:
                stop.result()
                self.view.status.setText("已退出示教；显示的是最后采样值")
            except Exception as error:
                self._report("停止失败，请检查设备并重试停止", error)
        if self._command is not None and self._command.done():
            command, self._command = self._command, None
            try:
                command.result()
                if self.teaching.enabled:
                    self.view.status.setText("示教点已保存到基础动作" if self._saving else "单步移动完成")
            except Exception as error:
                self._report("保存失败" if self._saving else "示教移动失败", error)
                if not self._saving:
                    self.stop()
            # Keep the real sample timestamp: command completion does not make
            # a recent sample stale. Every subsequent move reads its own target.
        if self._read is not None and self._read.done():
            read, self._read = self._read, None
            try:
                state = read.result()
                if self.teaching.enabled:
                    self._last_state_at = time.monotonic()
                    self.view.render_state(state, joints_supported=self.teaching.supports_joints)
                    if self._command is None:
                        self.view.status.setText("示教就绪 · 状态已更新")
            except Exception as error:
                if self.teaching.enabled:
                    self._report("读取机械臂状态失败", error)
                    self.stop()
        if self.teaching.enabled:
            if self._read is None:
                self._read = self._reader.submit(self.teaching.read_state)
            if self._command is not None and not self._saving:
                if time.monotonic() - self._command_started > MOTION_TIMEOUT_SECONDS:
                    self._report("示教移动超时", TimeoutError("已请求停止，请检查机械臂"))
                    self.stop()
            if self._last_state_at and not self._ready():
                self.view.status.setText("状态超过 2 秒未更新，禁止新的移动；可点击停止")
        self._set_active(self.teaching.active)
        self._render_controls()

    def _render_controls(self) -> None:
        self.view.set_session(
            active=self.teaching.active,
            ready=self._ready(),
            busy=self._command is not None or self._stop is not None,
        )

    def _set_active(self, active: bool) -> None:
        if self._announced_active != active:
            self._announced_active = active
            self.session_changed.emit(active)

    def _report(self, context: str, error: Exception) -> None:
        logger.error("%s: %s", context, error, exc_info=(type(error), error, error.__traceback__))
        message = f"{context}：{error}"
        self.view.status.setText(message)
        self.problem.emit(message)

    def save(self) -> None:
        if not self._ready() or self._command is not None:
            return
        joint = bool(self.view.mode.currentData())
        name, accepted = ask_text(self.view, "保存示教点", "动作名称：")
        if not accepted or not name.strip() or not self._ready():
            return
        self._saving = True
        self._command = self._commands.submit(self._save_point, name.strip(), joint)
        self._render_controls()

    def _save_point(self, name: str, joint: bool) -> None:
        state = self.teaching.read_state()
        parameters: dict[str, object] = {
            "目标": "机械臂", "臂": "左" if state.arm is ArmId.LEFT else "右",
            "模式": "move_joints" if joint else "move_l",
        }
        if joint:
            if not self.teaching.supports_joints:
                raise ValueError("当前机械臂不支持关节目标动作，请保存笛卡尔位姿")
            if state.joints is None or len(state.joints.positions_deg) != 7:
                raise ValueError("当前动作格式仅支持七关节目标；请保存笛卡尔位姿")
            parameters["关节角"] = state.joints.to_list()
        else:
            parameters["点位"] = state.pose.to_list()
            parameters["补偿"] = {"mode": "none"}
        self.services.composition.create_action(
            ActionDefinition(
                id=uuid4().hex, name=name, type=ActionType.MOVE, parameters=parameters,
                robot_profile_id=self.services.composition.robot_profile_id,
            ),
            origin="gui.teaching",
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.stop()
        return super().eventFilter(watched, event)

    def prepare_shutdown(self) -> None:
        self._closing = True
        self._timer.stop()
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self.stop()

    def shutdown(self) -> None:
        """Called after the event loop, before application/device destruction."""
        self._stopper.shutdown(wait=True, cancel_futures=False)
        self._commands.shutdown(wait=True, cancel_futures=True)
        self._reader.shutdown(wait=True, cancel_futures=True)
        if self._stop is not None:
            try:
                self._stop.result()
            except Exception:
                logger.exception("示教关闭失败，后续设备安全关闭仍需处理")

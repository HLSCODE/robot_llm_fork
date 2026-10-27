"""Exclusive, bounded manual teaching; no device is connected implicitly."""

from __future__ import annotations

import math
import logging
import time
from threading import Event, RLock
from uuid import uuid4

from ..devices import DeviceRuntime, ResourceArbiter, ResourceLease
from ..devices.runtime.arm_models import (
    ArmId, ArmState, CartesianPose, JointVector, MotionMode, MotionOptions,
)
from ..devices.runtime.contracts import (
    ArmJointMotion, ArmMotion, ArmStateReader, StoppableDevice,
)
from ..devices.runtime.ids import ROBOT_SYSTEM
from ..devices.runtime.models import StopMode
from ..geometry.pose_compensation import (
    euler_xyz_to_matrix, matrix_to_euler_xyz,
)

MAX_TRANSLATION_MM = 10.0
MAX_ANGLE_DEG = 5.0
MAX_SPEED_PERCENT = 10
logger = logging.getLogger(__name__)


def offset_pose(pose: CartesianPose, axis: int, amount: float) -> CartesianPose:
    """Translate in the adapter reference frame, or rotate about its fixed axes.

    Translation input is mm; rotation input is degrees. The adapter reference
    is world for Tianji and arm base for RealMan, not necessarily a shared base.
    """
    if not 0 <= axis < 6:
        raise ValueError("无效的笛卡尔轴")
    limit = MAX_TRANSLATION_MM if axis < 3 else MAX_ANGLE_DEG
    if not math.isfinite(amount) or not 0 < abs(amount) <= limit:
        raise ValueError(f"单次步长必须大于 0 且不超过 {limit}")
    values = pose.to_list()
    if axis < 3:
        values[axis] += amount / 1000.0
    else:
        angles = [0.0, 0.0, 0.0]
        angles[axis - 3] = math.radians(amount)
        delta = euler_xyz_to_matrix(*angles)
        current = euler_xyz_to_matrix(*values[3:])
        rotation = [
            [sum(delta[i][k] * current[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]
        values[3:] = matrix_to_euler_xyz(rotation)
    return CartesianPose.from_iterable(values)


class TeachingService:
    """Hold ownership through motion completion, including cancellation.

    Commands and reads run on workers. request_stop only invalidates commands
    and is safe on the UI thread; close performs the device stop on a worker.
    A failed stop retains the lease and requires another explicit stop attempt.
    """

    def __init__(self, runtime: DeviceRuntime, resources: ResourceArbiter) -> None:
        self._runtime = runtime
        self._resources = resources
        self._lock = RLock()
        self._close_lock = RLock()
        self._cancelled = Event()
        self._idle = Event()
        self._idle.set()
        self._lease: ResourceLease | None = None
        self._arm = ArmId.LEFT
        self._device: object | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._lease is not None

    @property
    def enabled(self) -> bool:
        return self.active and not self._cancelled.is_set()

    @property
    def supports_joints(self) -> bool:
        return isinstance(self._device, ArmJointMotion)

    def begin(self, arm: ArmId) -> None:
        """Reserve a ready robot without connecting it or commanding motion."""
        if not self._close_lock.acquire(blocking=False):
            raise RuntimeError("正在停止示教，请等待停止完成")
        try:
            self._begin(arm)
        finally:
            self._close_lock.release()

    def _begin(self, arm: ArmId) -> None:
        with self._lock:
            if self._lease is not None:
                raise RuntimeError("请先退出当前示教会话")
            lease = self._resources.acquire(f"teaching:{uuid4().hex}", (ROBOT_SYSTEM,))
            try:
                device = self._runtime.get_if_ready(ROBOT_SYSTEM)
                if not isinstance(device, ArmStateReader) or not isinstance(device, ArmMotion):
                    raise RuntimeError("机械臂未就绪，请先在设备菜单中初始化")
                if not isinstance(device, StoppableDevice) or StopMode.QUICK not in device.supported_stop_modes:
                    raise RuntimeError("机械臂不支持快速停止，不能进入示教")
            except Exception:
                lease.release()
                raise
            self._lease = lease
            self._device = device
            self._arm = arm
            self._cancelled.clear()
            logger.info("Teaching session started: arm=%s", arm.value)

    def read_state(self) -> ArmState:
        with self._lock:
            self._require_enabled()
            reader = self._device
            arm = self._arm
        assert isinstance(reader, ArmStateReader)
        state = reader.read_arm_state(arm)
        if state.arm != arm or state.device_error_code:
            raise RuntimeError("机械臂状态异常，禁止示教移动")
        return state

    def step(self, *, joint: bool, axis: int, amount: float, speed: int) -> None:
        if not 1 <= speed <= MAX_SPEED_PERCENT:
            raise ValueError(f"示教速度必须在 1～{MAX_SPEED_PERCENT}% 之间")
        if not math.isfinite(amount) or amount == 0:
            raise ValueError("步长必须是非零有限数值")
        with self._lock:
            self._require_enabled()
            if not self._idle.is_set():
                raise RuntimeError("上一步移动尚未结束")
            self._idle.clear()
            device = self._device
        try:
            state = self.read_state()
            options = MotionOptions(velocity_percent=speed, blocking=True)
            logger.info(
                "Teaching step: arm=%s joint=%s axis=%s amount=%s speed_percent=%s",
                state.arm.value, joint, axis, amount, speed,
            )
            if joint:
                if not isinstance(device, ArmJointMotion) or state.joints is None:
                    raise RuntimeError("当前机械臂不支持关节目标示教")
                values = state.joints.to_list()
                if not 0 <= axis < len(values) or abs(amount) > MAX_ANGLE_DEG:
                    raise ValueError("无效的关节轴或步长")
                values[axis] += amount
                target = JointVector.from_iterable(values)
                self._require_enabled()
                device.move_to_joints(state.arm, target, options)
            else:
                pose = offset_pose(state.pose, axis, amount)
                # Tianji skips targets already inside its tolerance. Keep this
                # command's threshold below even the smallest teaching step,
                # without changing the configured tolerance of normal actions.
                step_size = abs(amount) / 1000 if axis < 3 else math.radians(abs(amount))
                options = MotionOptions(
                    velocity_percent=speed, blocking=True,
                    cartesian_tolerance=step_size / 10,
                )
                assert isinstance(device, ArmMotion)
                self._require_enabled()
                device.move_to_pose(state.arm, pose, MotionMode.LINEAR, options)
        except Exception:
            self.request_stop()
            raise
        finally:
            self._idle.set()

    def request_stop(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        """Stop and release; never release an outstanding blocking SDK call.

        Rechecking after a stop covers a command crossing the dispatch boundary
        concurrently with cancellation. This is not a hardware safety watchdog.
        """
        self.request_stop()
        with self._close_lock:
            self.request_stop()
            with self._lock:
                if self._lease is None:
                    return
                device = self._device
            assert isinstance(device, StoppableDevice)
            deadline = time.monotonic() + 3.0
            while True:
                device.stop(StopMode.QUICK)
                if self._idle.is_set():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("停止后运动调用仍未返回，保留控制权；请检查设备并重试停止")
                self._idle.wait(0.1)
            with self._lock:
                assert self._lease is not None
                self._lease.release()
                self._lease = None
                self._device = None
                logger.info("Teaching session stopped: arm=%s", self._arm.value)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("示教已停止或尚未进入示教")

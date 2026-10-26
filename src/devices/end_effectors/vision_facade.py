"""Compose robot motion and an explicitly configured gripper for vision."""

from ..runtime.arm_models import ArmId, CartesianPose, MotionMode, MotionOptions, ArmState
from ..runtime.contracts import RobotSystem
from ..runtime.ids import ROBOT_SYSTEM
from .runtime import EndEffectorRuntime, ResolvedTool


class VisionRobotFacade:
    def __init__(self, routing: EndEffectorRuntime, target: ResolvedTool) -> None:
        self._routing = routing
        self._target = target
        self._robot = routing.runtime.require(ROBOT_SYSTEM, RobotSystem)

    def move_to_pose(
        self,
        arm: ArmId,
        pose: CartesianPose,
        mode: MotionMode,
        options: MotionOptions | None = None,
    ) -> None:
        self._robot.move_to_pose(arm, pose, mode, options)

    def read_arm_state(self, arm: ArmId) -> ArmState:
        return self._robot.read_arm_state(arm)

    def try_read_arm_state(self, arm: ArmId) -> ArmState | None:
        return self._robot.try_read_arm_state(arm)

    def _check_arm(self, arm: ArmId) -> None:
        if arm != self._target.arm:
            raise ValueError("视觉夹爪绑定与目标机械臂不一致")

    def open_gripper(self, arm: ArmId) -> None:
        self._check_arm(arm)
        self._routing.gripper(self._target).open()

    def close_gripper(self, arm: ArmId) -> None:
        self._check_arm(arm)
        self._routing.gripper(self._target).grip()

    def move_gripper(self, arm: ArmId, position: int) -> None:
        self._check_arm(arm)
        self._routing.move_position(self._target, position)

    def close(self) -> None:
        """Borrowed robot and gripper remain owned by the application runtime."""

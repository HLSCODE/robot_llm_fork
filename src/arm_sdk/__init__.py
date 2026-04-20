"""RealMan RM75 机械臂 SDK + 总控"""

try:
    from .controller import RobotController
except ImportError:
    RobotController = None

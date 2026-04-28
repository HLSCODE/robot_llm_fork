"""
执行桥接器
连接 AI 层和现有 ExecutionThread 执行层
"""
import logging
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from ..core.models import SequenceItem, SequenceItemStatus

logger = logging.getLogger(__name__)


class ExecutionBridge(QObject):
    """
    执行桥接器
    连接 AI 层和现有 ExecutionThread 执行层
    """

    # 信号定义
    execution_status_changed = pyqtSignal(str)           # 执行状态变更
    step_started = pyqtSignal(int, SequenceItem)       # 步骤开始
    step_completed = pyqtSignal(int, SequenceItem)      # 步骤完成
    step_failed = pyqtSignal(int, SequenceItem, str)    # 步骤失败
    execution_completed = pyqtSignal(bool)              # 执行完成
    log_message = pyqtSignal(str)                        # 日志消息

    def __init__(self):
        """初始化执行桥接器"""
        super().__init__()
        self._execution_thread = None
        self._simulation_mode = False
        self._main_window = None
        logger.info("ExecutionBridge 初始化完成")

    def set_main_window(self, main_window) -> None:
        """
        设置主窗口引用（用于获取 robot_controller 等）

        Args:
            main_window: MainWindow 实例
        """
        self._main_window = main_window

    def set_simulation_mode(self, enabled: bool) -> None:
        """
        设置模拟模式

        Args:
            enabled: 是否启用模拟模式
        """
        self._simulation_mode = enabled
        logger.info(f"ExecutionBridge 模拟模式: {'启用' if enabled else '禁用'}")

    def is_simulation_mode(self) -> bool:
        """是否启用模拟模式"""
        return self._simulation_mode

    def execute_sequence_items(
        self,
        items: List[SequenceItem],
        simulation: bool = False
    ) -> bool:
        """
        执行动作序列

        Args:
            items: SequenceItem 列表
            simulation: 是否模拟执行

        Returns:
            是否全部执行成功
        """
        if not items:
            logger.warning("动作序列为空")
            return False

        logger.info(f"开始执行动作序列，共 {len(items)} 个动作，模拟={simulation}")

        # 如果是模拟模式，执行模拟执行
        if simulation or self._simulation_mode:
            return self._execute_simulation(items)

        # 真实执行模式
        return self._execute_real(items)

    def _execute_simulation(self, items: List[SequenceItem]) -> bool:
        """
        模拟执行动作序列（不连接真实硬件）

        Args:
            items: SequenceItem 列表

        Returns:
            模拟执行结果（始终返回 True）
        """
        import time

        self.execution_status_changed.emit("模拟执行中...")

        for index, item in enumerate(items):
            self.step_started.emit(index, item)
            self.log_message.emit(f"[模拟] 执行: {item.definition.name}")

            # 模拟执行时间
            estimated_time = 1.0  # 每个动作模拟执行1秒
            time.sleep(estimated_time)

            item.status = SequenceItemStatus.SUCCESS
            self.step_completed.emit(index, item)
            self.log_message.emit(f"[模拟] 完成: {item.definition.name}")

        self.execution_completed.emit(True)
        self.log_message.emit("[模拟] 所有动作执行完成")
        return True

    def _execute_real(self, items: List[SequenceItem]) -> bool:
        """
        真实执行动作序列（通过 ExecutionThread）

        Args:
            items: SequenceItem 列表

        Returns:
            是否全部执行成功
        """
        if self._main_window is None:
            logger.error("主窗口未设置，无法执行真实动作")
            return False

        try:
            # 获取 robot_controller、body_controller 和 move_controller
            robot_controller = getattr(self._main_window, 'robot_controller', None)
            body_controller = getattr(self._main_window, 'body_controller', None)
            move_controller = getattr(self._main_window, 'move_controller', None)

            # 导入 ExecutionThread
            from ..gui.execution import ExecutionThread

            # 创建执行线程
            self._execution_thread = ExecutionThread(
                sequence=items,
                robot_controller=robot_controller,
                body_controller=body_controller,
                move_controller=move_controller,
            )

            # 连接信号
            self._execution_thread.step_started.connect(self._on_step_started)
            self._execution_thread.step_completed.connect(self._on_step_completed)
            self._execution_thread.step_failed.connect(self._on_step_failed)
            self._execution_thread.log_message.connect(self._on_log_message)
            self._execution_thread.finished.connect(self._on_execution_finished)

            # 开始执行
            self._execution_thread.start()

            return True

        except Exception as e:
            logger.error(f"执行动作序列失败: {e}", exc_info=True)
            self.log_message.emit(f"执行失败: {e}")
            return False

    def stop_execution(self) -> None:
        """停止当前执行"""
        if self._execution_thread and self._execution_thread.isRunning():
            logger.info("请求停止执行...")
            self._execution_thread.stop()
            self._execution_thread.wait()  # 等待线程结束
            self.execution_status_changed.emit("已停止")
            self.log_message.emit("执行已停止")

    def get_execution_status(self) -> str:
        """获取当前执行状态"""
        if self._execution_thread is None:
            return "空闲"
        if self._execution_thread.isRunning():
            return "执行中"
        return "空闲"

    def is_executing(self) -> bool:
        """是否正在执行"""
        return self._execution_thread is not None and self._execution_thread.isRunning()

    # ==================== 内部回调方法 ====================

    def _on_step_started(self, index: int, item: SequenceItem) -> None:
        """步骤开始回调"""
        self.step_started.emit(index, item)
        self.log_message.emit(f"开始执行步骤 {index + 1}: {item.definition.name}")

    def _on_step_completed(self, index: int, item: SequenceItem) -> None:
        """步骤完成回调"""
        self.step_completed.emit(index, item)
        self.log_message.emit(f"步骤 {index + 1} 执行完成: {item.definition.name}")

    def _on_step_failed(self, index: int, item: SequenceItem, error: str) -> None:
        """步骤失败回调"""
        self.step_failed.emit(index, item, error)
        self.log_message.emit(f"步骤 {index + 1} 执行失败: {error}")

    def _on_execution_finished(self) -> None:
        """执行完成回调"""
        # 检查是否有失败的步骤
        if self._execution_thread:
            failed_count = sum(
                1 for item in self._execution_thread.sequence
                if item.status == SequenceItemStatus.FAILED
            )
            success = failed_count == 0
            self.execution_completed.emit(success)
            self.execution_status_changed.emit("执行完成")
        else:
            self.execution_completed.emit(True)

    def _on_log_message(self, message: str) -> None:
        """日志消息回调"""
        self.log_message.emit(message)

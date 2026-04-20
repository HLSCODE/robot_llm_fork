"""
AI 核心控制器
核心编排器：连接用户输入、LLM、Skill系统和执行层
"""
import logging
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

from ..core.config_loader import Config
from ..llm import OpenAIClient, DeepSeekClient
from ..llm.base import LLMPlanResult
from ..skill_system import SkillEngine
from ..skill_system.models import SkillMatchResult, Skill
from ..core.models import SequenceItem
from .execution_bridge import ExecutionBridge

logger = logging.getLogger(__name__)


class AIController(QObject):
    """
    AI 核心控制器
    继承 QObject，通过 Signal 与 GUI 通信

    核心流程：
    1. 接收用户文字输入
    2. 调用 LLM 进行意图理解
    3. 调用 SkillEngine 解析并展开
    4. 触发 GUI 预览
    5. 用户确认后执行
    """

    # ==================== 信号定义 ====================
    # 状态信号
    status_changed = pyqtSignal(str)                    # 状态变更
    understanding_started = pyqtSignal()                 # LLM 开始分析
    understanding_finished = pyqtSignal(dict)           # LLM 分析完成

    # 技能匹配信号
    skill_matched = pyqtSignal(str, dict)               # 技能匹配成功 (skill_id, params)
    skill_not_matched = pyqtSignal(str)                  # 技能匹配失败 (error)

    # 预览信号
    preview_ready = pyqtSignal(list, dict)               # 预览序列已准备好 (items, skill_info)
    preview_validation = pyqtSignal(bool, str, list)     # 验证结果 (is_valid, message, warnings)

    # 执行信号
    execution_started = pyqtSignal()                      # 开始执行
    sequence_execution_started = pyqtSignal(list)         # 开始执行（携带序列数据）
    execution_finished = pyqtSignal(bool, str)           # 执行完成 (success, message)
    step_progress = pyqtSignal(int, int, str)            # 步骤进度 (current, total, step_name)

    # 错误信号
    error_occurred = pyqtSignal(str)                     # 发生错误

    def __init__(self, execution_bridge: Optional[ExecutionBridge] = None):
        """
        初始化 AI 控制器

        Args:
            execution_bridge: 可选，执行桥接器实例
        """
        super().__init__()

        self._config = Config.get_instance()
        self._llm_client: Optional[OpenAIClient] = None
        self._skill_engine: Optional[SkillEngine] = None
        self._execution_bridge = execution_bridge

        # 当前任务状态
        self._current_user_text: str = ""
        self._current_llm_result: Optional[LLMPlanResult] = None
        self._current_skill_match: Optional[SkillMatchResult] = None
        self._current_sequence: List[SequenceItem] = []
        self._current_skill_info: Dict[str, Any] = {}
        self._simulation_mode: bool = False
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._processing = False

        # 初始化组件
        self._initialize()

        logger.info("AIController 初始化完成")

    def _initialize(self) -> None:
        """初始化 LLM 和 Skill 引擎"""
        # 初始化 LLM（根据配置的提供商选择）
        if self._config.OPENAI_API_KEY:
            provider = self._config.MODEL_PROVIDER.lower()
            base_url = getattr(self._config, 'OPENAI_BASE_URL', '')

            if provider == "deepseek":
                self._llm_client = DeepSeekClient(
                    api_key=self._config.OPENAI_API_KEY,
                    model=self._config.OPENAI_MODEL or "deepseek-reasoner",
                    base_url=base_url,
                )
                if self._llm_client.is_available():
                    logger.info(f"DeepSeek 客户端就绪，使用模型: {self._llm_client.get_model_name()}")
                else:
                    logger.warning("DeepSeek 客户端不可用，请检查 API Key")
            elif provider == "dashscope":
                # 阿里云百炼，兼容 OpenAI 协议
                self._llm_client = OpenAIClient(
                    api_key=self._config.OPENAI_API_KEY,
                    model=self._config.OPENAI_MODEL or "qwen-plus",
                    base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
                if self._llm_client.is_available():
                    logger.info(f"百炼客户端就绪，使用模型: {self._llm_client.get_model_name()}")
                else:
                    logger.warning("百炼客户端不可用，请检查 API Key")
            else:
                # OpenAI 或其他兼容服务
                self._llm_client = OpenAIClient(
                    api_key=self._config.OPENAI_API_KEY,
                    model=self._config.OPENAI_MODEL or "gpt-4o",
                    base_url=base_url,
                )
                if self._llm_client.is_available():
                    logger.info(f"LLM 客户端就绪，使用模型: {self._llm_client.get_model_name()}")
                else:
                    logger.warning("LLM 客户端不可用，请检查 API Key")
        else:
            logger.warning("未配置 API Key")

        # 初始化 Skill 引擎
        self._skill_engine = SkillEngine()
        skill_count = self._skill_engine.load_skills()
        logger.info(f"技能引擎加载了 {skill_count} 个技能")

    def process_input(self, text: str) -> None:
        """
        主入口：处理用户输入文字
        触发流程：text → LLM → SkillEngine → preview_ready Signal
        LLM 调用在后台线程执行，避免阻塞 GUI

        Args:
            text: 用户输入的文字
        """
        if not text or not text.strip():
            self.error_occurred.emit("输入不能为空")
            return

        if self._processing:
            logger.warning("正在处理上一次请求，请稍候")
            self.error_occurred.emit("正在处理上一次请求，请稍候")
            return

        self._current_user_text = text.strip()
        self._processing = True
        self.status_changed.emit("分析中...")
        self.understanding_started.emit()

        logger.info(f"处理用户输入: {self._current_user_text}")

        def _do_background_work():
            try:
                if self._simulation_mode:
                    self._emit_error("模拟模式已启用，请先在设置中关闭模拟模式以使用 LLM")
                    self._emit_status("模拟模式")
                    return

                if self._llm_client is None or not self._llm_client.is_available():
                    self._emit_error("LLM 不可用，请检查 API Key 配置")
                    self._emit_status("错误")
                    return

                skill_summaries = self._skill_engine.list_all_skills()
                llm_result = self._llm_client.plan(self._current_user_text, skill_summaries)

                self._current_llm_result = llm_result
                self.understanding_finished.emit(llm_result.__dict__)

                logger.info(f"LLM 解析结果: skill_id={llm_result.skill_id}, confidence={llm_result.confidence}")

                if not llm_result.is_valid():
                    error_msg = llm_result.error or f"无法理解您的意图（置信度: {llm_result.confidence:.0%}）"
                    self.skill_not_matched.emit(error_msg)
                    self._emit_error(error_msg)
                    self._emit_status("匹配失败")
                    return

                skill_match_result = SkillMatchResult(
                    skill_id=llm_result.skill_id,
                    skill_name=llm_result.skill_name,
                    confidence=llm_result.confidence,
                    extracted_params=llm_result.parameters,
                    reasoning=llm_result.reasoning
                )

                self._current_skill_match = skill_match_result

                skill_info = self._skill_engine.get_skill_info(llm_result.skill_id)
                if skill_info is None:
                    self._emit_error(f"技能 {llm_result.skill_id} 不存在")
                    self._emit_status("错误")
                    return

                self._current_skill_info = skill_info
                self.skill_matched.emit(llm_result.skill_id, llm_result.parameters)

                sequence, validation = self._skill_engine.parse_and_expand(skill_match_result)

                if not validation.is_valid:
                    self._emit_error(validation.message)
                    self._emit_status("错误")
                    return

                self._current_sequence = sequence
                self.preview_validation.emit(validation.is_valid, validation.message, validation.warnings)

                self.preview_ready.emit(
                    [item.to_dict() for item in sequence],
                    skill_info
                )
                self._emit_status("预览就绪")

                logger.info(f"技能 {llm_result.skill_name} 展开为 {len(sequence)} 个动作")

            except Exception as e:
                logger.error(f"处理输入时发生错误: {e}", exc_info=True)
                self._emit_error(f"处理失败: {str(e)}")
                self._emit_status("错误")
            finally:
                self._processing = False

        self._executor.submit(_do_background_work)

    def _run_sequence_execution(self) -> None:
        """在右侧序列动画就绪后启动真实/模拟执行。"""
        if not self._current_sequence:
            return
        if self._execution_bridge is None:
            self.error_occurred.emit("执行器未初始化")
            return

        try:
            success = self._execution_bridge.execute_sequence_items(
                self._current_sequence,
                simulation=self._simulation_mode,
            )

            if success:
                self.execution_finished.emit(True, "执行成功")
                self.status_changed.emit("执行完成")
            else:
                self.execution_finished.emit(False, "执行失败")
                self.status_changed.emit("执行失败")

        except Exception as e:
            logger.error(f"执行动作序列时发生错误: {e}", exc_info=True)
            self.error_occurred.emit(f"执行失败: {str(e)}")
            self.execution_finished.emit(False, str(e))
            self.status_changed.emit("错误")

    def _emit_error(self, msg: str) -> None:
        """线程安全地发射错误信号"""
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        try:
            self.error_occurred.emit(msg)
        except RuntimeError:
            pass

    def _emit_status(self, status: str) -> None:
        """线程安全地发射状态信号"""
        try:
            self.status_changed.emit(status)
        except RuntimeError:
            pass

    def confirm_and_execute(self) -> None:
        """
        用户预览确认后，执行动作序列
        """
        if not self._current_sequence:
            self.error_occurred.emit("没有可执行的动作序列")
            return

        if self._execution_bridge is None:
            self.error_occurred.emit("执行器未初始化")
            return

        self.status_changed.emit("执行中...")
        self.execution_started.emit()
        self.sequence_execution_started.emit(self._current_sequence)

        n = len(self._current_sequence)
        # 右侧序列区逐项飞入（50ms/步），执行线程须等卡片就绪后再启动，避免步骤索引与列表不同步
        stagger_ms = 50
        delay_ms = min(stagger_ms * n + 180, 2000)

        logger.info(
            f"开始执行动作序列，共 {n} 个动作（{delay_ms}ms 后启动执行线程，供右侧动画）"
        )

        QTimer.singleShot(delay_ms, self._run_sequence_execution)

    def cancel_current_task(self) -> None:
        """取消当前任务"""
        if self._execution_bridge:
            self._execution_bridge.stop_execution()

        self._current_user_text = ""
        self._current_llm_result = None
        self._current_skill_match = None
        self._current_sequence = []
        self._current_skill_info = {}

        self.status_changed.emit("已取消")
        logger.info("当前任务已取消")

    def set_simulation_mode(self, enabled: bool) -> None:
        """
        设置模拟模式

        Args:
            enabled: 是否启用模拟模式
        """
        self._simulation_mode = enabled
        if self._execution_bridge:
            self._execution_bridge.set_simulation_mode(enabled)
        logger.info(f"模拟模式: {'启用' if enabled else '禁用'}")

    def is_simulation_mode(self) -> bool:
        """是否启用模拟模式"""
        return self._simulation_mode

    def get_skill_list(self) -> List[Dict[str, Any]]:
        """获取当前所有可用技能列表"""
        if self._skill_engine is None:
            return []
        return self._skill_engine.list_all_skills()

    def get_current_preview(self) -> tuple:
        """获取当前预览信息"""
        return self._current_sequence, self._current_skill_info

    def is_llm_available(self) -> bool:
        """LLM 是否可用"""
        return self._llm_client is not None and self._llm_client.is_available()

    def get_llm_model_name(self) -> str:
        """获取 LLM 模型名称"""
        if self._llm_client:
            return self._llm_client.get_model_name()
        return "未配置"

    def get_model_provider(self) -> str:
        """获取模型提供商名称"""
        return self._config.MODEL_PROVIDER.upper()

    def is_api_key_set(self) -> bool:
        """API Key 是否已配置"""
        return bool(self._config.OPENAI_API_KEY)

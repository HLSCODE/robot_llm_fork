"""
WebSocket 服务端
作为独立服务运行，接受前端 WebSocket 连接，调用机器人控制函数。
功能与 GUI 模式完全对等。

协议说明:
    前端 → 服务端（指令）:

    === 执行控制 ===
        {"action": "execute",       "sequence": [...]}     执行动作序列
        {"action": "execute_task",  "name": "xxx.task"}    加载并执行已保存的任务
        {"action": "stop"}                                 停止当前执行
        {"action": "pause"}                                暂停执行
        {"action": "resume"}                               恢复执行

    === 动作库管理 ===
        {"action": "list_actions"}                         获取动作库（按类型分组）
        {"action": "get_action_schema"}                    获取动作类型参数结构定义（供前端动态生成表单）
        {"action": "create_action", "name": "...", "type": "MOVE_TO_POINT", "parameters": {...}}
        {"action": "delete_action", "id": "..."}           删除动作
        {"action": "update_action", "id": "...", "name": "...", "type": "...", "parameters": {...}}

    === 序列编排 ===
        {"action": "get_sequence"}                         获取当前编排的序列
        {"action": "add_to_sequence",    "items": [...]}   添加动作到序列
        {"action": "remove_from_sequence", "index": 0}     删除序列中的某项
        {"action": "move_in_sequence",   "from": 0, "to": 1}  移动序列项位置
        {"action": "clear_sequence"}                       清空序列

    === 任务持久化 ===
        {"action": "list_tasks"}                           获取已保存的任务列表
        {"action": "save_task",     "name": "xxx.task"}    保存当前序列为任务文件
        {"action": "load_task",     "name": "xxx.task"}    加载任务到当前序列（不执行）
        {"action": "delete_task",   "name": "xxx.task"}    删除任务文件

    === AI 助手 ===
        {"action": "ai_chat",      "text": "帮我抓一个瓶子"}  AI 自然语言规划
        {"action": "ai_confirm"}                            确认执行 AI 规划的序列
        {"action": "ai_cancel"}                             取消 AI 规划
        {"action": "ai_status"}                             查询 AI/LLM 状态
        {"action": "list_skills"}                           获取可用技能列表

    === 设备管理 ===
        {"action": "status"}                               查询设备/执行状态（含相机状态）
        {"action": "init_robots"}                          初始化机械臂
        {"action": "init_body"}                            初始化身体（升降平台）
        {"action": "disconnect"}                           断开所有硬件连接
        {"action": "test_camera"}                          测试 RealSense 相机（单次）

    === 相机流媒体 ===
        {"action": "camera_status"}                        查询相机管理器状态

    === MiniCPM 聊天代理 ===
        {"action": "minicpm_status"}                       查询 MiniCPM 网关配置与代理状态
        {"action": "chat_connect"}                         建立聊天会话（标记当前连接进入聊天模式）
        {"action": "chat",         "messages": [...]}      发送聊天消息（每次临时连接网关，收完响应后关闭）
        {"action": "chat_disconnect"}                      断开聊天会话

WebSocket 路径:
    ws://{host}:{port}/               — 前端主控连接（本协议，所有功能均通过 action 字段分发）

    服务端 → 前端（事件推送）:
        {"event": "step_started",       "index": 0, "name": "...", "status": "RUNNING"}
        {"event": "step_completed",     "index": 0, "name": "..."}
        {"event": "step_failed",        "index": 0, "name": "...", "error": "..."}
        {"event": "log",                "level": "info|warn|error", "message": "..."}
        {"event": "execution_finished"}
        {"event": "error",              "message": "..."}              # 请求参数校验错误
        {"event": "ai_status_changed",  "status": "分析中..."}
        {"event": "ai_skill_matched",   "skill_id": "...", "skill_name": "...", "params": {...}}
        {"event": "ai_preview_ready",   "sequence": [...], "skill_info": {...}}
        {"event": "ai_execution_finished", "success": true, "message": "..."}
        {"event": "chat_connected"}                                    # 聊天会话已建立
        {"event": "chat_disconnected"}                                 # 聊天会话已断开
        {"event": "chat_data",          "type": "chunk", ...}          # MiniCPM 聊天响应（规范化字段 + 完整 packet）
        {"event": "minicpm_instruction","instruction": "..."}          # 检测到可执行机器人指令

    log 事件 level 取值:
        info  — 常规执行日志（默认）
        warn  — 可恢复的异常，如重试中
        error — 执行失败或硬件异常

启动方式:
    python run_server.py
"""

import asyncio
import base64
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Set, List, Dict, Any
from uuid import uuid4

try:
    import websockets
except ImportError:
    websockets = None

from src.cameras import get_camera_manager
from src.cameras.camera_factory import CameraManager
from .action_executor import ActionExecutor
from .minicpm_proxy import MiniCPMProxyConfig, _extract_user_text
from .interceptor import OutgoingInjector
from .ask_service import classify_instruction
from ..core.models import ActionDefinition, ActionType, SequenceItem, SequenceItemStatus
from ..core.storage import StorageManager
from ..core.config_loader import Config
from ..arm_sdk import RobotController



logger = logging.getLogger(__name__)


class RobotWebSocketServer:
    """
    机器人 WebSocket 服务端

    接受前端连接，提供与 GUI 完全对等的功能：
    - 动作库 CRUD
    - 序列编排
    - 任务持久化
    - 执行控制
    - AI 自然语言规划
    - 设备管理
    """

    def __init__(
        self,
        robot_controller=None,
        body_controller=None,
        neck_controller=None,
        host: str = "0.0.0.0",
        port: int = 8765,
    ):
        if websockets is None:
            raise ImportError(
                "websockets 库未安装，无法启动 WebSocket 服务端"
            )

        self._robot_controller = robot_controller
        self._body_controller = body_controller
        self._neck_controller = neck_controller
        self._host = host
        self._port = port

        # 已连接的客户端集合
        self._clients: Set = set()

        # 执行器（延迟创建，回调绑定广播函数）
        self._executor: Optional[ActionExecutor] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 当前编排的序列（对应 GUI 右侧的序列列表）
        self._current_sequence: List[SequenceItem] = []

        # AI 相关状态
        self._ai_preview_sequence: List[SequenceItem] = []
        self._ai_preview_skill_info: Dict[str, Any] = {}
        self._ai_processing = False
        self._ai_thread_pool = ThreadPoolExecutor(max_workers=1)

        # LLM 客户端和技能引擎（延迟初始化，避免未安装 AI 依赖时报错）
        self._llm_client = None
        self._skill_engine = None

        # 设备连接状态
        self._device_status = {
            "robot1": False,
            "robot2": False,
            "body": body_controller is not None,
        }
        # 检测机械臂连接状态
        if robot_controller is not None:
            self._device_status["robot1"] = True
            self._device_status["robot2"] = True

        # RealSense 相机管理器（可选，延迟初始化）
        self._camera_manager: Optional[CameraManager] = None

        # 相机帧订阅：通过 subscribe_camera_frames action 注册
        self._camera_frame_subs: Set = set()
        self._camera_push_task: Optional[asyncio.Task] = None

        # MiniCPM 代理配置（延迟初始化）
        self._minicpm_cfg: Optional[MiniCPMProxyConfig] = None

        # MiniCPM 聊天会话：id(websocket) -> {"gw_ws": ws, "injector": OutgoingInjector}
        self._minicpm_sessions: Dict[int, Dict] = {}

        # AI 执行跟踪（用于发送 ai_execution_finished 事件）
        self._ai_execution_pending = False
        self._execution_had_failure = False

    # ------------------------------------------------------------------
    # 启动服务
    # ------------------------------------------------------------------

    def run(self) -> None:
        """阻塞运行 WebSocket 服务（主线程调用）"""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """异步启动 WebSocket 服务"""
        self._loop = asyncio.get_running_loop()

        # 创建执行器
        self._executor = ActionExecutor(
            robot_controller=self._robot_controller,
            body_controller=self._body_controller,
            on_step_started=self._on_step_started,
            on_step_completed=self._on_step_completed,
            on_step_failed=self._on_step_failed,
            on_log=self._on_log,
            on_finished=self._on_finished,
        )

        # 初始化 AI 组件
        self._init_ai()

        # 启动相机管理器
        self._init_camera()

        # 加载 MiniCPM 代理配置
        self._init_minicpm_config()

        async with websockets.serve(self._handler, self._host, self._port):
            logger.info("WebSocket 服务已启动: ws://%s:%d", self._host, self._port)
            print(f"WebSocket 服务已启动: ws://{self._host}:{self._port}")
            print("等待前端连接...")
            await asyncio.Future()

    # ------------------------------------------------------------------
    # AI 初始化（不依赖 Qt）
    # ------------------------------------------------------------------

    def _init_ai(self) -> None:
        """初始化 LLM 客户端和技能引擎"""
        try:
            config = Config.get_instance()

            # 初始化技能引擎
            from ..skill_system import SkillEngine
            self._skill_engine = SkillEngine()
            skill_count = self._skill_engine.load_skills()
            logger.info("技能引擎加载了 %d 个技能", skill_count)

            # 初始化 LLM 客户端
            if config.OPENAI_API_KEY:
                provider = config.MODEL_PROVIDER.lower()
                base_url = config.OPENAI_BASE_URL

                if provider == "deepseek":
                    from ..llm import DeepSeekClient
                    self._llm_client = DeepSeekClient(
                        api_key=config.OPENAI_API_KEY,
                        model=config.OPENAI_MODEL or "deepseek-reasoner",
                        base_url=base_url,
                    )
                elif provider == "dashscope":
                    # 阿里云百炼，兼容 OpenAI 协议
                    from ..llm import OpenAIClient
                    self._llm_client = OpenAIClient(
                        api_key=config.OPENAI_API_KEY,
                        model=config.OPENAI_MODEL or "qwen-plus",
                        base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    )
                else:
                    # OpenAI 或其他兼容服务
                    from ..llm import OpenAIClient
                    self._llm_client = OpenAIClient(
                        api_key=config.OPENAI_API_KEY,
                        model=config.OPENAI_MODEL or "gpt-4o",
                        base_url=base_url,
                    )

                if self._llm_client.is_available():
                    logger.info("LLM 客户端就绪: %s", self._llm_client.get_model_name())
                else:
                    logger.warning("LLM 客户端不可用")
            else:
                logger.warning("未配置 API Key，AI 功能不可用")

        except Exception as e:
            logger.warning("AI 组件初始化失败: %s", e)

    # ------------------------------------------------------------------
    # 连接处理
    # ------------------------------------------------------------------

    async def _handler(self, websocket) -> None:
        """所有连接统一进入主控处理器，通过 action 字段分发指令。"""
        await self._handle_frontend_ws(websocket)

    async def _handle_frontend_ws(self, websocket) -> None:
        """处理前端主控 WebSocket 连接"""
        self._clients.add(websocket)
        remote = websocket.remote_address
        logger.info("前端客户端已连接: %s", remote)
        print(f"前端客户端已连接: {remote}")

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(self._json_msg(
                        {"event": "error", "message": "无效的 JSON 格式"}
                    ))
                    continue

                await self._dispatch(websocket, data)

        except websockets.exceptions.ConnectionClosed:
            logger.info("前端客户端断开: %s", remote)
        finally:
            self._clients.discard(websocket)
            self._camera_frame_subs.discard(websocket)
            await self._close_minicpm_session(websocket)
            print(f"前端客户端断开: {remote}")


    # ------------------------------------------------------------------
    # 指令分发
    # ------------------------------------------------------------------

    async def _dispatch(self, websocket, data: dict) -> None:
        """根据 action 字段分发到对应处理函数"""
        action = data.get("action", "")

        # 指令路由表
        handlers = {
            # 执行控制
            "execute":       self._handle_execute,
            "execute_task":  self._handle_execute_task,
            "stop":          self._handle_stop,
            "pause":         self._handle_pause,
            "resume":        self._handle_resume,
            # 动作库管理
            "list_actions":  self._handle_list_actions,
            "get_action_schema": self._handle_get_action_schema,
            "create_action": self._handle_create_action,
            "delete_action": self._handle_delete_action,
            "update_action": self._handle_update_action,
            # 序列编排
            "get_sequence":          self._handle_get_sequence,
            "add_to_sequence":       self._handle_add_to_sequence,
            "remove_from_sequence":  self._handle_remove_from_sequence,
            "move_in_sequence":      self._handle_move_in_sequence,
            "clear_sequence":        self._handle_clear_sequence,
            # 任务持久化
            "list_tasks":    self._handle_list_tasks,
            "save_task":     self._handle_save_task,
            "load_task":     self._handle_load_task,
            "delete_task":   self._handle_delete_task,
            "get_task_detail": self._handle_get_task_detail,
            "rename_task": self._handle_rename_task,
            "add_to_task": self._handle_add_to_task,
            "remove_from_task": self._handle_remove_from_task,
            "move_in_task": self._handle_move_in_task,
            # AI 助手
            "ai_chat":       self._handle_ai_chat,
            "ai_confirm":    self._handle_ai_confirm,
            "ai_cancel":     self._handle_ai_cancel,
            "ai_status":     self._handle_ai_status,
            "list_skills":   self._handle_list_skills,
            # 设备管理
            "status":        self._handle_status,
            "init_robots":   self._handle_init_robots,
            "init_body":     self._handle_init_body,
            "disconnect":    self._handle_disconnect,
            "test_camera":   self._handle_test_camera,
            # 相机管理器
            "camera_status":             self._handle_camera_status,
            # 相机帧订阅（替代独立 /camera/frames 连接）
            "subscribe_camera_frames":   self._handle_subscribe_camera_frames,
            "unsubscribe_camera_frames": self._handle_unsubscribe_camera_frames,
            # MiniCPM 聊天代理（替代独立 /ws/chat 连接）
            "chat_connect":              self._handle_chat_connect,
            "chat_disconnect":           self._handle_chat_disconnect,
            "chat":                      self._handle_chat_send,
            # MiniCPM 代理配置查询
            "minicpm_status": self._handle_minicpm_status,
        }

        handler = handlers.get(action)
        if handler:
            await handler(websocket, data)
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"未知的 action: {action}"}
            ))

    # ==================================================================
    # 执行控制
    # ==================================================================

    async def _handle_execute(self, websocket, data: dict) -> None:
        """
        执行动作序列
        请求: {"action": "execute", "sequence": [...]}
        如果 sequence 省略，则执行当前编排的序列
        """
        if self._executor.is_running:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "已有序列正在执行，请先停止"}
            ))
            return

        raw_sequence = data.get("sequence")
        if raw_sequence:
            # 前端传入了序列数据
            try:
                sequence = self._parse_sequence(raw_sequence)
            except Exception as e:
                await websocket.send(self._json_msg(
                    {"event": "error", "message": f"序列解析失败: {str(e)}"}
                ))
                return
        else:
            # 执行当前编排的序列
            sequence = self._current_sequence

        if not sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "序列为空，请先添加动作"}
            ))
            return

        # 重置状态
        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        await self._broadcast({
            "event": "accepted",
            "message": "开始执行",
            "steps": len(sequence),
        })
        self._executor.execute(sequence)

    async def _handle_execute_task(self, websocket, data: dict) -> None:
        """
        加载并执行已保存的任务
        请求: {"action": "execute_task", "name": "xxx.task"}
        """
        if self._executor.is_running:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "已有序列正在执行，请先停止"}
            ))
            return

        task_name = data.get("name", "")
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "name 不能为空"}
            ))
            return

        sequence = StorageManager.load_sequence(task_name)
        if not sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        await self._broadcast({
            "event": "accepted",
            "message": f"加载任务 '{task_name}'，开始执行",
            "steps": len(sequence),
        })
        self._executor.execute(sequence)

    async def _handle_stop(self, websocket, data: dict) -> None:
        """停止执行"""
        if self._executor.is_running:
            if self._ai_execution_pending:
                self._execution_had_failure = True  # 人工停止视为未成功完成
            self._executor.stop()
            await websocket.send(self._json_msg(
                {"event": "stopped", "message": "已发送停止指令"}
            ))
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "当前没有正在执行的序列"}
            ))

    async def _handle_pause(self, websocket, data: dict) -> None:
        """暂停执行"""
        if self._executor.is_running and not self._executor.is_paused:
            self._executor.pause()
            await websocket.send(self._json_msg(
                {"event": "paused", "message": "执行已暂停"}
            ))
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "无法暂停：未在执行或已暂停"}
            ))

    async def _handle_resume(self, websocket, data: dict) -> None:
        """恢复执行"""
        if self._executor.is_running and self._executor.is_paused:
            self._executor.resume()
            await websocket.send(self._json_msg(
                {"event": "resumed", "message": "执行已恢复"}
            ))
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "无法恢复：未处于暂停状态"}
            ))

    # ==================================================================
    # 动作库管理
    # ==================================================================

    async def _handle_list_actions(self, websocket, data: dict) -> None:
        """
        返回动作库（按类型分组）
        响应: {"event": "actions_list", "actions": {...按类型分组...}}
        """
        all_actions = StorageManager.load_actions()

        # 按类型分组（与 GUI 左侧 Tab 对应）
        grouped = {
            "MOVE_TO_POINT": [],
            "ARM_ACTION": [],
            "INSPECT_AND_OUTPUT": [],
            "CHANGE_GUN": [],
            "VISION_CAPTURE": [],
        }
        for a in all_actions:
            type_key = a.type.value
            if type_key in grouped:
                grouped[type_key].append(a.to_dict())

        await websocket.send(self._json_msg({
            "event": "actions_list",
            "actions": grouped,
            "total": len(all_actions),
        }))

    async def _handle_get_action_schema(self, websocket, data: dict) -> None:
        """
        返回所有动作类型的参数结构定义，前端可据此动态生成表单
        请求: {"action": "get_action_schema"}
        响应: {"event": "action_schema", "types": {...}}
        """
        schema = {
            "MOVE_TO_POINT": {
                "label": "移动类",
                "description": "机械臂移动 / 升降平台移动",
                "variants": {
                    "机械臂": {
                        "description": "控制机械臂移动到指定点位",
                        "fields": {
                            "目标": {"type": "select", "options": ["机械臂"], "default": "机械臂", "label": "目标"},
                            "臂":   {"type": "select", "options": ["左", "右"], "default": "左", "label": "臂"},
                            "模式": {"type": "select", "options": [
                                {"value": "move_j", "label": "关节运动 (move_j)"},
                                {"value": "move_l", "label": "直线运动 (move_l)"}
                            ], "default": "move_j", "label": "运动模式"},
                            "点位": {"type": "text", "placeholder": "例如: [-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]", "label": "点位", "required": True}
                        }
                    },
                    "身体": {
                        "description": "控制升降平台移动到指定位置",
                        "fields": {
                            "目标": {"type": "select", "options": ["身体"], "default": "身体", "label": "目标"},
                            "位置": {"type": "number", "min": 0, "max": 500000, "default": 0, "unit": "脉冲", "label": "目标位置"}
                        }
                    }
                },
                "variant_key": "目标"
            },
            "ARM_ACTION": {
                "label": "执行类",
                "description": "快换手、继电器、夹爪、吸液枪等执行器操作",
                "variants": {
                    "快换手": {
                        "description": "控制快换手开关",
                        "fields": {
                            "执行器": {"type": "select", "options": ["快换手"], "default": "快换手", "label": "执行器"},
                            "编号":   {"type": "select", "options": [1, 2], "default": 1, "label": "编号"},
                            "操作":   {"type": "select", "options": ["开", "关"], "default": "开", "label": "操作"}
                        }
                    },
                    "继电器": {
                        "description": "控制继电器开关",
                        "fields": {
                            "执行器": {"type": "select", "options": ["继电器"], "default": "继电器", "label": "执行器"},
                            "编号":   {"type": "select", "options": [1, 2], "default": 1, "label": "编号"},
                            "操作":   {"type": "select", "options": ["开", "关"], "default": "开", "label": "操作"}
                        }
                    },
                    "夹爪": {
                        "description": "控制夹爪开关",
                        "fields": {
                            "执行器": {"type": "select", "options": ["夹爪"], "default": "夹爪", "label": "执行器"},
                            "编号":   {"type": "select", "options": [1, 2], "default": 1, "label": "编号"},
                            "操作":   {"type": "select", "options": ["开", "关"], "default": "开", "label": "操作"}
                        }
                    },
                    "吸液枪": {
                        "description": "控制吸液枪吸液/吐液",
                        "fields": {
                            "执行器": {"type": "select", "options": ["吸液枪"], "default": "吸液枪", "label": "执行器"},
                            "操作":   {"type": "select", "options": ["吸", "吐", "退枪头"], "default": "吸", "label": "操作"},
                            "容量":   {"type": "number", "min": 0, "max": 10000, "default": 500, "unit": "ul", "label": "容量"}
                        }
                    }
                },
                "variant_key": "执行器"
            },
            "INSPECT_AND_OUTPUT": {
                "label": "检测类",
                "description": "传感器读取与阈值判定",
                "fields": {
                    "Sensor_ID": {"type": "text", "label": "传感器 ID", "required": True},
                    "Threshold": {"type": "number", "min": -9999, "max": 9999, "default": 0, "label": "判定阈值"},
                    "Timeout":   {"type": "number", "min": 0.1, "max": 60, "default": 5, "unit": "s", "label": "超时时间"}
                }
            },
            "CHANGE_GUN": {
                "label": "换枪类",
                "description": "取/放工具头",
                "fields": {
                    "Gun_Position": {"type": "select", "options": [1, 2], "default": 1, "label": "枪位"},
                    "Operation":    {"type": "select", "options": ["取", "放"], "default": "取", "label": "取/放"}
                }
            },
            "VISION_CAPTURE": {
                "label": "视觉类",
                "description": "视觉识别 + 自动抓取（参数已固定）",
                "fields": {
                    "目标机械臂": {"type": "text", "default": "robot1", "label": "目标机械臂", "readonly": True},
                    "工作流":     {"type": "text", "default": "bottle", "label": "工作流", "readonly": True},
                    "置信度":     {"type": "number", "default": 0.7, "label": "置信度", "readonly": True},
                    "调试图片":   {"type": "boolean", "default": True, "label": "调试图片", "readonly": True},
                    "移动速度":   {"type": "number", "default": 15, "unit": "mm/s", "label": "移动速度", "readonly": True},
                    "夹爪长度":   {"type": "number", "default": 150.0, "unit": "mm", "label": "夹爪长度", "readonly": True}
                },
                "note": "视觉抓取参数已固定，前端仅需填写动作名称即可"
            }
        }

        await websocket.send(self._json_msg({
            "event": "action_schema",
            "types": schema,
        }))

    async def _handle_create_action(self, websocket, data: dict) -> None:
        """
        新建动作
        请求: {"action": "create_action", "name": "移动到A点", "type": "MOVE_TO_POINT", "parameters": {...}}
        """
        name = data.get("name", "").strip()
        if not name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "动作名称不能为空"}
            ))
            return

        action_type_str = data.get("type", "")
        try:
            action_type = ActionType(action_type_str)
        except ValueError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的动作类型: {action_type_str}，"
                 f"可选: {[t.value for t in ActionType]}"}
            ))
            return

        parameters = data.get("parameters", {})

        # 创建动作定义
        action_def = ActionDefinition(
            id=str(uuid4()),
            name=name,
            type=action_type,
            parameters=parameters,
        )

        # 保存到持久化
        all_actions = StorageManager.load_actions()
        all_actions.append(action_def)
        StorageManager.save_actions(all_actions)

        await websocket.send(self._json_msg({
            "event": "action_created",
            "action": action_def.to_dict(),
        }))
        logger.info("新建动作: %s (%s)", name, action_type_str)

    async def _handle_delete_action(self, websocket, data: dict) -> None:
        """
        删除动作
        请求: {"action": "delete_action", "id": "..."}
        """
        action_id = data.get("id", "")
        if not action_id:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "动作 id 不能为空"}
            ))
            return

        all_actions = StorageManager.load_actions()
        original_count = len(all_actions)
        all_actions = [a for a in all_actions if a.id != action_id]

        if len(all_actions) == original_count:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"未找到 id 为 '{action_id}' 的动作"}
            ))
            return

        StorageManager.save_actions(all_actions)
        await websocket.send(self._json_msg({
            "event": "action_deleted",
            "id": action_id,
        }))
        logger.info("删除动作: %s", action_id)

    async def _handle_update_action(self, websocket, data: dict) -> None:
        """
        更新动作
        请求: {"action": "update_action", "id": "...", "name": "...", "type": "...", "parameters": {...}}
        """
        action_id = data.get("id", "")
        if not action_id:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "动作 id 不能为空"}
            ))
            return

        all_actions = StorageManager.load_actions()
        target = None
        for a in all_actions:
            if a.id == action_id:
                target = a
                break

        if target is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"未找到 id 为 '{action_id}' 的动作"}
            ))
            return

        # 更新字段（只更新提供的字段）
        if "name" in data:
            target.name = data["name"]
        if "type" in data:
            try:
                target.type = ActionType(data["type"])
            except ValueError:
                await websocket.send(self._json_msg(
                    {"event": "error", "message": f"无效的动作类型: {data['type']}"}
                ))
                return
        if "parameters" in data:
            target.parameters = data["parameters"]

        StorageManager.save_actions(all_actions)
        await websocket.send(self._json_msg({
            "event": "action_updated",
            "action": target.to_dict(),
        }))
        logger.info("更新动作: %s", action_id)

    # ==================================================================
    # 序列编排（对应 GUI 右侧序列列表）
    # ==================================================================

    async def _handle_get_sequence(self, websocket, data: dict) -> None:
        """获取当前编排的序列"""
        await websocket.send(self._json_msg({
            "event": "sequence",
            "sequence": [item.to_dict() for item in self._current_sequence],
        }))

    async def _handle_add_to_sequence(self, websocket, data: dict) -> None:
        """
        添加动作到序列
        请求: {"action": "add_to_sequence", "items": [
            {"name": "...", "type": "MOVE_TO_POINT", "parameters": {...}},
            ...
        ]}
        也支持传入动作库中的 id: {"action": "add_to_sequence", "action_ids": ["id1", "id2"]}
        """
        # 方式1: 通过 action_ids 从动作库引用
        action_ids = data.get("action_ids", [])
        if action_ids:
            all_actions = StorageManager.load_actions()
            action_map = {a.id: a for a in all_actions}
            for aid in action_ids:
                if aid in action_map:
                    seq_item = SequenceItem.from_definition(action_map[aid])
                    self._current_sequence.append(seq_item)
                else:
                    await websocket.send(self._json_msg(
                        {"event": "error", "message": f"动作库中不存在 id: {aid}"}
                    ))
                    return

        # 方式2: 直接传入动作数据
        items = data.get("items", [])
        if items:
            parsed = self._parse_sequence(items)
            self._current_sequence.extend(parsed)

        if not action_ids and not items:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "请提供 items 或 action_ids"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [item.to_dict() for item in self._current_sequence],
        }))

    async def _handle_remove_from_sequence(self, websocket, data: dict) -> None:
        """
        删除序列中的某项
        请求: {"action": "remove_from_sequence", "index": 0}
        """
        index = data.get("index")
        if index is None or not (0 <= index < len(self._current_sequence)):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的索引: {index}，序列长度: {len(self._current_sequence)}"}
            ))
            return

        removed = self._current_sequence.pop(index)
        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "removed": removed.to_dict(),
            "sequence": [item.to_dict() for item in self._current_sequence],
        }))

    async def _handle_move_in_sequence(self, websocket, data: dict) -> None:
        """
        移动序列项位置
        请求: {"action": "move_in_sequence", "from": 0, "to": 1}
        """
        from_idx = data.get("from")
        to_idx = data.get("to")
        seq_len = len(self._current_sequence)

        if from_idx is None or to_idx is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "需要提供 from 和 to 索引"}
            ))
            return

        if not (0 <= from_idx < seq_len) or not (0 <= to_idx < seq_len):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"索引越界，序列长度: {seq_len}"}
            ))
            return

        item = self._current_sequence.pop(from_idx)
        self._current_sequence.insert(to_idx, item)

        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [item.to_dict() for item in self._current_sequence],
        }))

    async def _handle_clear_sequence(self, websocket, data: dict) -> None:
        """清空序列"""
        self._current_sequence.clear()
        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [],
        }))

    # ==================================================================
    # 任务持久化
    # ==================================================================

    async def _handle_list_tasks(self, websocket, data: dict) -> None:
        """返回所有已保存的任务文件名"""
        tasks = StorageManager.list_tasks()
        await websocket.send(self._json_msg({
            "event": "tasks_list",
            "tasks": tasks,
        }))

    async def _handle_save_task(self, websocket, data: dict) -> None:
        """
        保存当前序列为任务文件
        请求: {"action": "save_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        if not self._current_sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "序列为空，无需保存"}
            ))
            return

        StorageManager.save_sequence(self._current_sequence, task_name)
        await websocket.send(self._json_msg({
            "event": "task_saved",
            "name": task_name,
            "steps": len(self._current_sequence),
        }))
        logger.info("任务已保存: %s", task_name)

    async def _handle_load_task(self, websocket, data: dict) -> None:
        """
        加载任务到当前序列（不执行）
        请求: {"action": "load_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        sequence = StorageManager.load_sequence(task_name)
        if not sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        # 加载到当前序列（替换）
        self._current_sequence = sequence
        for item in self._current_sequence:
            item.status = SequenceItemStatus.PENDING

        await websocket.send(self._json_msg({
            "event": "task_loaded",
            "name": task_name,
            "sequence": [item.to_dict() for item in self._current_sequence],
        }))
        logger.info("任务已加载: %s", task_name)


    async def _handle_delete_task(self, websocket, data: dict) -> None:
        """
        删除任务文件
        请求: {"action": "delete_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        # 构建文件路径
        name = Path(task_name).name
        filepath = StorageManager.TASKS_DIR / name
        if filepath.suffix != ".task":
            filepath = filepath.with_suffix(".task")

        if not filepath.is_file():
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        filepath.unlink()
        await websocket.send(self._json_msg({
            "event": "task_deleted",
            "name": task_name,
        }))
        logger.info("任务已删除: %s", task_name)

    async def _handle_get_task_detail(self, websocket, data: dict) -> None:
        """
        读取任务文件内容，但不影响当前序列
        请求: {"action": "get_task_detail", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        sequence = StorageManager.load_sequence(task_name)
        if not sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        await websocket.send(self._json_msg({
            "event": "task_detail",
            "name": Path(task_name).with_suffix(".task").name,
            "sequence": [item.to_dict() for item in sequence],
        }))

    async def _handle_rename_task(self, websocket, data: dict) -> None:
        """
        重命名任务文件
        请求: {"action": "rename_task", "name": "old.task", "new_name": "new.task"}
        """
        task_name = data.get("name", "").strip()
        new_name = data.get("new_name", "").strip()

        if not task_name or not new_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "name 和 new_name 不能为空"}
            ))
            return

        old_path = self._resolve_task_path(task_name)
        new_path = self._resolve_task_path(new_name)

        if not old_path.is_file():
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        if new_path.is_file() and old_path.resolve() != new_path.resolve():
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{new_path.name}' 已存在"}
            ))
            return

        old_path.replace(new_path)
        await websocket.send(self._json_msg({
            "event": "task_renamed",
            "name": old_path.name,
            "new_name": new_path.name,
        }))

    async def _handle_add_to_task(self, websocket, data: dict) -> None:
        """
        直接向任务文件追加/插入动作
        请求:
          {"action": "add_to_task", "name": "x.task", "items": [...], "index": 0}
          {"action": "add_to_task", "name": "x.task", "action_ids": ["..."], "index": 0}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        sequence = self._load_task_sequence_for_edit(task_name)
        if sequence is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        insert_items = []
        action_ids = data.get("action_ids", [])
        if action_ids:
            all_actions = StorageManager.load_actions()
            action_map = {a.id: a for a in all_actions}
            for aid in action_ids:
                if aid not in action_map:
                    await websocket.send(self._json_msg(
                        {"event": "error", "message": f"动作库中不存在 id: {aid}"}
                    ))
                    return
                insert_items.append(SequenceItem.from_definition(action_map[aid]))

        items = data.get("items", [])
        if items:
            try:
                insert_items.extend(self._parse_sequence(items))
            except Exception as e:
                await websocket.send(self._json_msg(
                    {"event": "error", "message": f"动作解析失败: {str(e)}"}
                ))
                return

        if not insert_items:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "请提供 items 或 action_ids"}
            ))
            return

        index = data.get("index")
        if index is None:
            sequence.extend(insert_items)
        else:
            if not isinstance(index, int) or not (0 <= index <= len(sequence)):
                await websocket.send(self._json_msg(
                    {"event": "error", "message": f"无效的插入位置: {index}"}
                ))
                return
            for offset, item in enumerate(insert_items):
                sequence.insert(index + offset, item)

        StorageManager.save_sequence(sequence, task_name)
        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": self._resolve_task_path(task_name).name,
            "sequence": [item.to_dict() for item in sequence],
        }))

    async def _handle_remove_from_task(self, websocket, data: dict) -> None:
        """
        直接删除任务文件中的某一步
        请求: {"action": "remove_from_task", "name": "x.task", "index": 0}
        """
        task_name = data.get("name", "").strip()
        index = data.get("index")

        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        sequence = self._load_task_sequence_for_edit(task_name)
        if sequence is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        if index is None or not isinstance(index, int) or not (0 <= index < len(sequence)):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的索引: {index}"}
            ))
            return

        removed = sequence.pop(index)
        StorageManager.save_sequence(sequence, task_name)
        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": self._resolve_task_path(task_name).name,
            "removed": removed.to_dict(),
            "sequence": [item.to_dict() for item in sequence],
        }))

    async def _handle_move_in_task(self, websocket, data: dict) -> None:
        """
        直接调整任务文件内部顺序
        请求: {"action": "move_in_task", "name": "x.task", "from": 0, "to": 1}
        """
        task_name = data.get("name", "").strip()
        from_idx = data.get("from")
        to_idx = data.get("to")

        if not task_name:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "任务名称不能为空"}
            ))
            return

        sequence = self._load_task_sequence_for_edit(task_name)
        if sequence is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        if from_idx is None or to_idx is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "需要提供 from 和 to 索引"}
            ))
            return

        if not isinstance(from_idx, int) or not isinstance(to_idx, int):
            await websocket.send(self._json_msg(
                {"event": "error", "message": "from 和 to 必须为整数"}
            ))
            return

        if not (0 <= from_idx < len(sequence)) or not (0 <= to_idx < len(sequence)):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"索引越界，序列长度: {len(sequence)}"}
            ))
            return

        item = sequence.pop(from_idx)
        sequence.insert(to_idx, item)
        StorageManager.save_sequence(sequence, task_name)
        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": self._resolve_task_path(task_name).name,
            "sequence": [item.to_dict() for item in sequence],
        }))

    def _resolve_task_path(self, task_name: str) -> Path:
        name = Path(task_name).name
        filepath = StorageManager.TASKS_DIR / name
        if filepath.suffix != ".task":
            filepath = filepath.with_suffix(".task")
        return filepath

    def _load_task_sequence_for_edit(self, task_name: str):
        filepath = self._resolve_task_path(task_name)
        if not filepath.is_file():
            return None
        sequence = StorageManager.load_sequence(filepath.name)
        for item in sequence:
            item.status = SequenceItemStatus.PENDING
        return sequence

    # ==================================================================
    # AI 助手
    # ==================================================================

    async def _handle_ai_chat(self, websocket, data: dict) -> None:
        """
        AI 自然语言规划
        请求: {"action": "ai_chat", "text": "帮我抓一个瓶子"}
        流程: text → LLM → SkillEngine → 预览序列推送到前端
        """
        text = data.get("text", "").strip()
        if not text:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "text 不能为空"}
            ))
            return

        if self._ai_processing:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "正在处理上一次请求，请稍候"}
            ))
            return

        if self._llm_client is None or not self._llm_client.is_available():
            await websocket.send(self._json_msg(
                {"event": "error", "message": "LLM 不可用，请检查 config.env 中的 API Key 配置"}
            ))
            return

        if self._skill_engine is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "技能引擎未初始化"}
            ))
            return

        self._start_ai_planning(text)

    async def _handle_ai_confirm(self, websocket, data: dict) -> None:
        """
        确认执行 AI 规划的序列
        请求: {"action": "ai_confirm"}
        """
        if not self._ai_preview_sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "没有待确认的 AI 规划序列"}
            ))
            return

        if self._executor.is_running:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "已有序列正在执行，请先停止"}
            ))
            return

        sequence = self._ai_preview_sequence
        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        # 同步到当前序列
        self._current_sequence = list(sequence)

        # 标记本次为 AI 触发执行，以便 _on_finished 发送 ai_execution_finished 事件
        self._ai_execution_pending = True
        self._execution_had_failure = False

        await self._broadcast({
            "event": "accepted",
            "message": "AI 序列开始执行",
            "steps": len(sequence),
        })
        self._executor.execute(sequence)

        # 清空预览状态
        self._ai_preview_sequence = []
        self._ai_preview_skill_info = {}

    async def _handle_ai_cancel(self, websocket, data: dict) -> None:
        """取消 AI 规划"""
        self._ai_preview_sequence = []
        self._ai_preview_skill_info = {}
        await websocket.send(self._json_msg({
            "event": "ai_cancelled",
            "message": "AI 规划已取消",
        }))

    async def _handle_ai_status(self, websocket, data: dict) -> None:
        """查询 AI/LLM 状态"""
        llm_available = self._llm_client is not None and self._llm_client.is_available()
        model_name = self._llm_client.get_model_name() if self._llm_client else "未配置"

        try:
            config = Config.get_instance()
            provider = config.MODEL_PROVIDER.upper()
            api_key_set = bool(config.OPENAI_API_KEY)
        except Exception:
            provider = "未知"
            api_key_set = False

        await websocket.send(self._json_msg({
            "event": "ai_status",
            "llm_available": llm_available,
            "api_key_set": api_key_set,
            "model": model_name,
            "provider": provider,
            "processing": self._ai_processing,
            "has_preview": bool(self._ai_preview_sequence),
        }))

    async def _handle_list_skills(self, websocket, data: dict) -> None:
        """获取可用技能列表"""
        if self._skill_engine is None:
            await websocket.send(self._json_msg({
                "event": "skills_list",
                "skills": [],
            }))
            return

        skills = self._skill_engine.list_all_skills()
        await websocket.send(self._json_msg({
            "event": "skills_list",
            "skills": skills,
        }))

    def _start_ai_planning(self, text: str) -> bool:
        """启动 AI 技能规划（后台线程）。

        返回 True 表示已提交规划任务；False 表示条件不满足（处理中 / 组件不可用）。
        外部调用方负责事先验证 text 不为空。
        """
        if self._ai_processing:
            return False
        if self._llm_client is None or not self._llm_client.is_available():
            return False
        if self._skill_engine is None:
            return False

        self._ai_processing = True
        self._broadcast_threadsafe({"event": "ai_status_changed", "status": "分析中..."})

        def _do_work():
            try:
                from ..skill_system.models import SkillMatchResult

                skill_summaries = self._skill_engine.list_all_skills()
                llm_result = self._llm_client.plan(text, skill_summaries)
                if not llm_result.is_valid():
                    error_msg = llm_result.error or f"无法理解您的意图（置信度: {llm_result.confidence:.0%}）"
                    self._broadcast_threadsafe({"event": "ai_skill_not_matched", "error": error_msg})
                    self._broadcast_threadsafe({"event": "ai_status_changed", "status": "匹配失败"})
                    return

                skill_match = SkillMatchResult(
                    skill_id=llm_result.skill_id,
                    skill_name=llm_result.skill_name,
                    confidence=llm_result.confidence,
                    extracted_params=llm_result.parameters,
                    reasoning=llm_result.reasoning,
                )
                self._broadcast_threadsafe({
                    "event": "ai_skill_matched",
                    "skill_id": llm_result.skill_id,
                    "skill_name": llm_result.skill_name,
                    "confidence": llm_result.confidence,
                    "params": llm_result.parameters,
                    "reasoning": llm_result.reasoning,
                })

                skill_info = self._skill_engine.get_skill_info(llm_result.skill_id)
                if skill_info is None:
                    self._broadcast_threadsafe({"event": "error", "message": f"技能 {llm_result.skill_id} 不存在"})
                    return

                sequence, validation = self._skill_engine.parse_and_expand(skill_match)
                if not validation.is_valid:
                    self._broadcast_threadsafe({"event": "error", "message": validation.message})
                    return

                self._ai_preview_sequence = sequence
                self._ai_preview_skill_info = skill_info
                self._broadcast_threadsafe({
                    "event": "ai_preview_ready",
                    "sequence": [item.to_dict() for item in sequence],
                    "skill_info": skill_info,
                })
                self._broadcast_threadsafe({"event": "ai_status_changed", "status": "预览就绪"})
                logger.info("AI 规划完成: %s → %d 个动作", llm_result.skill_name, len(sequence))

            except Exception as e:
                logger.error("AI 处理失败: %s", e, exc_info=True)
                self._broadcast_threadsafe({"event": "error", "message": f"AI 处理失败: {str(e)}"})
            finally:
                self._ai_processing = False

        self._ai_thread_pool.submit(_do_work)
        return True


    # ==================================================================
    # 设备管理
    # ==================================================================

    async def _handle_status(self, websocket, data: dict) -> None:
        """查询设备和执行状态"""
        await websocket.send(self._json_msg({
            "event": "status",
            "devices": self._device_status,
            "executor": {
                "running": self._executor.is_running,
                "paused": self._executor.is_paused,
            },
            "sequence_length": len(self._current_sequence),
            "ai_processing": self._ai_processing,
            "camera": {
                "available": self._camera_manager is not None and self._camera_manager.camera_count > 0,
                "camera_count": self._camera_manager.camera_count if self._camera_manager else 0,
                "cameras": self._camera_manager.get_cameras_info() if self._camera_manager else [],
            },
            "minicpm": {
                "configured": self._minicpm_cfg is not None,
                "gateway": (
                    f"{self._minicpm_cfg.gateway_scheme}://"
                    f"{self._minicpm_cfg.gateway_host}"
                    f"{self._minicpm_cfg._port_suffix}"
                    f"{self._minicpm_cfg.gateway_path_prefix}"
                ) if self._minicpm_cfg else None,
            },
        }))

    async def _handle_init_robots(self, websocket, data: dict) -> None:
        """
        初始化机械臂
        请求: {"action": "init_robots"}
        """
        def _do_init():
            try:
                self._broadcast_threadsafe({"event": "log", "level": "info", "message": "正在初始化机械臂..."})
                if RobotController is None:
                    raise ImportError("RobotController SDK unavailable")
                self._robot_controller = RobotController()

                # 初始化 Robot1
                robot1 = self._robot_controller.init_robot1()
                if robot1 is not None:
                    self._device_status["robot1"] = True
                    self._broadcast_threadsafe({"event": "log", "level": "info", "message": "Robot1 初始化成功"})
                else:
                    self._broadcast_threadsafe({"event": "log", "level": "warn", "message": "Robot1 初始化失败"})

                # 初始化 Robot2
                robot2 = self._robot_controller.init_robot2()
                if robot2 is not None:
                    self._device_status["robot2"] = True
                    self._broadcast_threadsafe({"event": "log", "level": "info", "message": "Robot2 初始化成功"})
                else:
                    self._broadcast_threadsafe({"event": "log", "level": "warn", "message": "Robot2 初始化失败"})

                # 更新执行器的控制器引用
                self._executor._robot_controller = self._robot_controller

                self._broadcast_threadsafe({
                    "event": "device_status_changed",
                    "devices": self._device_status,
                })

            except ImportError as e:
                self._broadcast_threadsafe({"event": "error", "message": f"机械臂模块导入失败: {e}"})
            except Exception as e:
                self._broadcast_threadsafe({"event": "error", "message": f"机械臂初始化异常: {e}"})

        threading.Thread(target=_do_init, daemon=True, name="InitRobots").start()
        await websocket.send(self._json_msg({"event": "log", "level": "info", "message": "开始初始化机械臂..."}))

    async def _handle_init_body(self, websocket, data: dict) -> None:
        """
        初始化身体（升降平台）
        请求: {"action": "init_body"}
        """
        try:
            from ..devices import ModbusMotor
            self._body_controller = ModbusMotor(
                port="/dev/body", baudrate=115200, slave_id=1, timeout=1
            )
            self._device_status["body"] = True
            self._executor._body_controller = self._body_controller

            await websocket.send(self._json_msg({
                "event": "log",
                "level": "info",
                "message": "身体控制器初始化成功",
            }))
            await websocket.send(self._json_msg({
                "event": "device_status_changed",
                "devices": self._device_status,
            }))
        except ImportError as e:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"身体模块导入失败: {e}"}
            ))
        except Exception as e:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"身体初始化异常: {e}"}
            ))

    async def _handle_disconnect(self, websocket, data: dict) -> None:
        """断开所有硬件连接"""
        messages = []

        if self._executor.is_running:
            self._executor.stop()
            messages.append("已停止当前执行")

        if self._robot_controller is not None:
            try:
                self._robot_controller.shutdown()
                messages.append("机械臂已断开")
            except Exception as e:
                messages.append(f"断开机械臂出错: {e}")
            self._robot_controller = None
            self._executor._robot_controller = None
            self._device_status["robot1"] = False
            self._device_status["robot2"] = False

        if self._body_controller is not None:
            try:
                self._body_controller.close()
                messages.append("身体控制器已断开")
            except Exception as e:
                messages.append(f"断开身体控制器出错: {e}")
            self._body_controller = None
            self._executor._body_controller = None
            self._device_status["body"] = False

        await websocket.send(self._json_msg({
            "event": "disconnected",
            "messages": messages,
            "devices": self._device_status,
        }))

    async def _handle_test_camera(self, websocket, data: dict) -> None:
        """
        测试 RealSense 相机
        请求: {"action": "test_camera"}
        """
        def _do_test():
            try:
                import time

                config = Config.get_instance()
                provider = getattr(config, "CAMERA_PROVIDER", "auto").lower()
                if provider in ("webcam", "opencv"):
                    import cv2

                    raw_indexes = getattr(config, "WEBCAM_DEVICE_INDEXES", "0")
                    indexes = [int(x.strip()) for x in raw_indexes.split(",") if x.strip()]
                    index = indexes[0] if indexes else 0
                    cap = cv2.VideoCapture(index, getattr(cv2, "CAP_DSHOW", 0))
                    try:
                        if not cap.isOpened():
                            raise RuntimeError(f"本地摄像头无法打开: index={index}")
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            raise RuntimeError(f"本地摄像头无法读取画面: index={index}")
                        height, width = frame.shape[:2]
                        self._broadcast_threadsafe({
                            "event": "camera_test_result",
                            "success": True,
                            "message": f"本地摄像头测试成功: index={index} size={width}x{height}",
                        })
                    finally:
                        cap.release()
                    return

                import pyrealsense2 as rs
                sn = config.REALSENSE_DEVICE_SN

                ctx = rs.context()
                devices = list(ctx.devices)
                if not devices:
                    self._broadcast_threadsafe({
                        "event": "camera_test_result",
                        "success": False,
                        "message": "未检测到 RealSense 设备",
                    })
                    return

                pipeline = rs.pipeline()
                cfg = rs.config()
                cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
                cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
                if sn:
                    cfg.enable_device(sn)
                pipeline.start(cfg)

                time.sleep(1)

                import time as _time
                deadline = _time.time() + 10
                while _time.time() < deadline:
                    try:
                        frames = pipeline.wait_for_frames(200)
                        color = frames.get_color_frame()
                        depth = frames.get_depth_frame()
                        if color and depth:
                            msg = (f"相机测试成功: color={color.width}x{color.height}  "
                                   f"depth={depth.get_distance(320, 240):.3f}m  "
                                   f"(SN={sn or 'auto-select'})")
                            pipeline.stop()
                            self._broadcast_threadsafe({
                                "event": "camera_test_result",
                                "success": True,
                                "message": msg,
                            })
                            return
                    except Exception:
                        pass

                pipeline.stop()
                self._broadcast_threadsafe({
                    "event": "camera_test_result",
                    "success": False,
                    "message": "取帧超时（10 秒内未获得有效帧）",
                })

            except Exception as e:
                self._broadcast_threadsafe({
                    "event": "camera_test_result",
                    "success": False,
                    "message": str(e),
                })

        threading.Thread(target=_do_test, daemon=True, name="TestCamera").start()
        await websocket.send(self._json_msg({"event": "log", "level": "info", "message": "正在测试相机..."}))

    # ==================================================================
    # MiniCPM 代理
    # ==================================================================

    def _init_minicpm_config(self) -> None:
        """从 Config 加载 MiniCPM 代理配置。"""
        try:
            cfg_dict = Config.get_minicpm_proxy_config()
            self._minicpm_cfg = MiniCPMProxyConfig(**cfg_dict)
            logger.info(
                "MiniCPM 代理已配置: %s://%s%s%s",
                self._minicpm_cfg.gateway_scheme,
                self._minicpm_cfg.gateway_host,
                self._minicpm_cfg._port_suffix,
                self._minicpm_cfg.gateway_path_prefix,
            )
        except Exception as exc:
            logger.warning("MiniCPM 代理配置加载失败: %s", exc)
            self._minicpm_cfg = None

    async def _handle_minicpm_status(self, websocket, data: dict) -> None:
        """
        查询 MiniCPM 网关配置与代理状态
        请求: {"action": "minicpm_status"}
        响应: {"event": "minicpm_status", "configured": bool,
               "gateway": "https://host:port",
               "ask_enabled": bool}
        """
        if self._minicpm_cfg is None:
            await websocket.send(self._json_msg({
                "event": "minicpm_status",
                "configured": False,
            }))
            return

        cfg = self._minicpm_cfg
        await websocket.send(self._json_msg({
            "event": "minicpm_status",
            "configured": True,
            "gateway": f"{cfg.gateway_scheme}://{cfg.gateway_host}{cfg._port_suffix}{cfg.gateway_path_prefix}",
            "ask_enabled": cfg.ask_enabled,
            "chat_action": "chat_connect / chat / chat_disconnect",
        }))

    # ==================================================================
    # 相机管理器
    # ==================================================================

    def _init_camera(self) -> None:
        """根据 CAMERA_PROVIDER 初始化相机管理器单例，详见 src/cameras/camera_factory.py。"""
        self._camera_manager = get_camera_manager()

    async def _handle_camera_status(self, websocket, data: dict) -> None:
        """
        查询相机管理器状态
        请求: {"action": "camera_status"}
        响应: {
            "event": "camera_status",
            "available": bool,          // 是否有在线相机
            "camera_count": int,        // 在线相机数量
            "cameras": [                // 所有已配置相机的状态
                {"serial": "...", "name": "...", "online": true},
                {"serial": "...", "name": "...", "online": false, "error": "..."}
            ],
            "stream_url": "ws://.../camera/stream",
            "frames_url": "ws://.../camera/frames"
        }
        """
        display_host = "localhost" if self._host == "0.0.0.0" else self._host
        if self._camera_manager is None:
            await websocket.send(self._json_msg({
                "event": "camera_status",
                "available": False,
                "camera_count": 0,
                "cameras": [],
                "stream_url": f"ws://{display_host}:{self._port}/camera/stream",
                "frames_url": f"ws://{display_host}:{self._port}/camera/frames",
            }))
            return

        cameras_info = self._camera_manager.get_cameras_info()
        available = self._camera_manager.camera_count > 0
        await websocket.send(self._json_msg({
            "event": "camera_status",
            "available": available,
            "camera_count": self._camera_manager.camera_count,
            "cameras": cameras_info,
            "stream_url": f"ws://{display_host}:{self._port}/camera/stream",
            "frames_url": f"ws://{display_host}:{self._port}/camera/frames",
        }))

    # ==================================================================
    # 相机帧订阅（dispatch 模式，替代独立 /camera/frames WebSocket）
    # ==================================================================

    async def _handle_subscribe_camera_frames(self, websocket, data: dict) -> None:
        """订阅相机帧推送。
        请求: {"action": "subscribe_camera_frames"}
        成功后服务端持续推送: {"event": "camera_frames", "frames": [...]}
        """
        if self._camera_manager is None:
            await websocket.send(self._json_msg({
                "event": "camera_error",
                "message": "未配置任何相机",
                "cameras": [],
            }))
            return
        if not self._camera_manager.camera_count:
            await websocket.send(self._json_msg({
                "event": "camera_error",
                "message": "所有配置相机均不可用",
                "cameras": self._camera_manager.get_cameras_info(),
            }))
            return

        self._camera_frame_subs.add(websocket)
        await websocket.send(self._json_msg({"event": "camera_subscribed"}))

        if self._camera_push_task is None or self._camera_push_task.done():
            self._camera_push_task = asyncio.ensure_future(self._camera_push_loop())
        logger.info("客户端订阅相机帧: %s", websocket.remote_address)

    async def _handle_unsubscribe_camera_frames(self, websocket, data: dict) -> None:
        """取消相机帧订阅。
        请求: {"action": "unsubscribe_camera_frames"}
        """
        self._camera_frame_subs.discard(websocket)
        await websocket.send(self._json_msg({"event": "camera_unsubscribed"}))

    async def _camera_push_loop(self) -> None:
        """后台任务：以 30fps 向所有订阅客户端推送相机帧。"""
        interval = 1.0 / 30
        while self._camera_frame_subs:
            if self._camera_manager:
                jpegs = self._camera_manager.get_latest_jpegs()
                if jpegs:
                    payload = json.dumps({
                        "event": "camera_frames",
                        "frames": [
                            {
                                "serial": serial,
                                "name": name,
                                "index": idx,
                                "data": base64.b64encode(jpeg).decode("ascii"),
                            }
                            for idx, (serial, name, jpeg) in enumerate(jpegs)
                        ],
                    }, ensure_ascii=False)
                    dead: List = []
                    for ws in list(self._camera_frame_subs):
                        try:
                            await ws.send(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self._camera_frame_subs.discard(ws)
            await asyncio.sleep(interval)
        self._camera_push_task = None

    # ==================================================================
    # MiniCPM 聊天代理（dispatch 模式）
    # ==================================================================

    async def _handle_chat_connect(self, websocket, data: dict) -> None:
        """标记聊天会话激活（不预先连接网关）。
        请求: {"action": "chat_connect"}
        成功: {"event": "chat_connected"}

        MiniCPM /ws/chat 是一次性连接（一问一答后网关自动关闭），
        因此每次发消息时才临时连接网关，会话标记独立于网关连接状态。
        """
        if self._minicpm_cfg is None:
            await websocket.send(self._json_msg({
                "event": "error", "message": "MiniCPM 代理未配置"
            }))
            return
        if id(websocket) in self._minicpm_sessions:
            await websocket.send(self._json_msg({
                "event": "error", "message": "聊天会话已连接，请先断开"
            }))
            return

        self._minicpm_sessions[id(websocket)] = {"active": True}
        await websocket.send(self._json_msg({"event": "chat_connected"}))
        logger.info("MiniCPM 聊天会话已就绪: %s", websocket.remote_address)

    async def _handle_chat_disconnect(self, websocket, data: dict) -> None:
        """断开 MiniCPM 聊天会话。
        请求: {"action": "chat_disconnect"}
        """
        self._minicpm_sessions.pop(id(websocket), None)
        await websocket.send(self._json_msg({"event": "chat_disconnected"}))

    async def _handle_chat_send(self, websocket, data: dict) -> None:
        """发送聊天消息：每次临时连接网关、收完响应后关闭，不影响会话状态。
        请求: {"action": "chat", "messages": [...], "streaming": true, ...}
        服务端持续推送规范化的 chat_data 事件。
        """
        if id(websocket) not in self._minicpm_sessions:
            await websocket.send(self._json_msg({
                "event": "error", "message": "请先发送 chat_connect 建立聊天会话"
            }))
            return

        cfg = self._minicpm_cfg
        gw_url = f"{cfg.gateway_ws_base}/ws/chat"
        try:
            gw_ws = await websockets.connect(
                gw_url,
                ssl=cfg.ssl_ctx(),
                max_size=100 * 1024 * 1024,
                open_timeout=30,
            )
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "error", "message": f"连接 MiniCPM 网关失败: {exc}"
            }))
            return

        # 每条消息用独立注入器（网关连接是全新的，需重新注入系统提示）
        injector = OutgoingInjector("chat")
        payload = {k: v for k, v in data.items() if k != "action"}
        processed = injector.process(json.dumps(payload, ensure_ascii=False))
        try:
            await gw_ws.send(processed)
        except Exception as exc:
            await gw_ws.close()
            await websocket.send(self._json_msg({
                "event": "error", "message": f"发送消息失败: {exc}"
            }))
            return

        # 后台接收响应，网关关闭后不影响客户端会话状态
        asyncio.ensure_future(self._minicpm_recv_once(websocket, gw_ws))

        # 指令分类
        try:
            user_text = _extract_user_text(payload)
            if user_text:
                await self._on_chat_user_text(user_text)
        except Exception:
            pass

    @staticmethod
    def _normalize_chat_data(raw) -> dict:
        """将 MiniCPM 上游原始消息转换为稳定字段 + 完整 packet 的 chat_data 结构。"""
        text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        base_event = {"event": "chat_data"}
        try:
            packet = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {
                **base_event,
                "type": "unknown",
                "text": text,
                # 仅在无法解析或无法识别时保留 raw，避免正常音频包重复放大体积。
                "raw": text,
            }

        # 将上游原始 JSON 整包挂到 packet，前端如需兼容新增字段可直接读取。
        base_event = {
            **base_event,
            "packet": packet,
        }

        if not isinstance(packet, dict):
            return {
                **base_event,
                "type": "unknown",
                "text": text,
                "raw": text,
            }

        packet_type = packet.get("type")
        if packet_type == "prefill_done":
            return {
                **base_event,
                "type": "prefill_done",
                "input_tokens": packet.get("input_tokens"),
            }
        if packet_type == "chunk":
            return {
                **base_event,
                "type": "chunk",
                "text_delta": packet.get("text_delta", ""),
                "audio_data": packet.get("audio_data"),
            }
        if packet_type == "done":
            return {
                **base_event,
                "type": "done",
                "text": packet.get("text", ""),
                "generated_tokens": packet.get("generated_tokens"),
                "input_tokens": packet.get("input_tokens"),
                "audio_data": packet.get("audio_data"),
                "recording_session_id": packet.get("recording_session_id"),
            }
        if packet_type == "error":
            return {
                **base_event,
                "type": "error",
                "error": packet.get("error", ""),
                "raw": text,
            }

        return {
            **base_event,
            "type": "unknown",
            "text": text,
            "raw": text,
        }

    async def _minicpm_recv_once(self, client_ws, gw_ws) -> None:
        """接收单次 MiniCPM 响应并转发，网关关闭后不改变客户端会话状态。"""
        try:
            async for raw in gw_ws:
                try:
                    await client_ws.send(
                        self._json_msg(self._normalize_chat_data(raw))
                    )
                except Exception:
                    break
        except Exception:
            pass
        finally:
            try:
                await gw_ws.close()
            except Exception:
                pass
            logger.debug("MiniCPM 单次响应结束")

    async def _close_minicpm_session(self, websocket) -> None:
        """清理指定客户端的 MiniCPM 会话标记。"""
        self._minicpm_sessions.pop(id(websocket), None)

    async def _on_chat_user_text(self, text: str) -> None:
        """对聊天用户输入进行指令分类，必要时触发 AI 技能规划。"""
        if not text.strip():
            return
        cfg = self._minicpm_cfg
        ask_result = await classify_instruction(
            text,
            api_key=cfg.ask_api_key if cfg else "",
            base_url=cfg.ask_base_url if cfg else "",
            model=cfg.ask_model if cfg else "gpt-4o-mini",
            enabled=cfg.ask_enabled if cfg else False,
        )
        if not ask_result.get("is_Instruction", False):
            logger.debug("用户输入非指令，跳过规划: %s", text[:60])
            return
        instruction = ask_result.get("Instruction", text)
        logger.info("检测到机器人指令，触发 AI 规划: %s", instruction)
        self._broadcast_threadsafe({"event": "minicpm_instruction", "instruction": instruction})
        if not self._start_ai_planning(instruction):
            logger.debug("AI 规划未启动（处理中或组件不可用），指令: %s", instruction)

    # ==================================================================
    # 序列解析
    # ==================================================================

    def _parse_sequence(self, raw: list) -> list:
        """
        将前端传来的 JSON 数组转换为 SequenceItem 列表

        支持两种格式:
        1. 完整格式（含 uuid）: {"uuid": "...", "definition": {...}, "status": "PENDING"}
        2. 简化格式:           {"name": "移动到A点", "type": "MOVE_TO_POINT", "parameters": {...}}
        """
        sequence = []
        for item_data in raw:
            if "definition" in item_data:
                seq_item = SequenceItem.from_dict(item_data)
            else:
                action_def = ActionDefinition.from_dict({
                    "id": item_data.get("id", ""),
                    "name": item_data.get("name", "未命名动作"),
                    "type": item_data.get("type", ""),
                    "parameters": item_data.get("parameters", {}),
                })
                seq_item = SequenceItem.from_definition(action_def)

            seq_item.status = SequenceItemStatus.PENDING
            sequence.append(seq_item)

        return sequence

    # ==================================================================
    # 执行器回调 → 广播到所有客户端
    # ==================================================================

    def _on_step_started(self, index: int, item: SequenceItem) -> None:
        self._broadcast_threadsafe({
            "event": "step_started",
            "index": index,
            "name": item.definition.name,
            "status": item.status.value,
        })

    def _on_step_completed(self, index: int, item: SequenceItem) -> None:
        self._broadcast_threadsafe({
            "event": "step_completed",
            "index": index,
            "name": item.definition.name,
        })

    def _on_step_failed(self, index: int, item: SequenceItem, error: str) -> None:
        self._execution_had_failure = True
        self._broadcast_threadsafe({
            "event": "step_failed",
            "index": index,
            "name": item.definition.name,
            "error": error,
        })

    def _on_log(self, message: str, level: str = "info") -> None:
        log_fn = {"warn": logger.warning, "error": logger.error}.get(level, logger.info)
        log_fn(message)
        self._broadcast_threadsafe({
            "event": "log",
            "level": level,
            "message": message,
        })

    def _on_finished(self) -> None:
        if self._ai_execution_pending:
            self._ai_execution_pending = False
            success = not self._execution_had_failure
            self._broadcast_threadsafe({
                "event": "ai_execution_finished",
                "success": success,
                "message": "AI 序列执行完成" if success else "AI 序列执行失败",
            })
        self._broadcast_threadsafe({
            "event": "execution_finished",
        })

    # ==================================================================
    # 广播工具
    # ==================================================================

    def _broadcast_threadsafe(self, data: dict) -> None:
        """从任意线程安全地广播消息到所有客户端"""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            asyncio.ensure_future,
            self._broadcast(data),
        )

    async def _broadcast(self, data: dict) -> None:
        """广播消息到所有已连接的客户端"""
        if not self._clients:
            return
        msg = self._json_msg(data)
        disconnected = []
        for client in list(self._clients):
            try:
                await client.send(msg)
            except Exception:
                disconnected.append(client)
        for client in disconnected:
            self._clients.discard(client)

    @staticmethod
    def _json_msg(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False)

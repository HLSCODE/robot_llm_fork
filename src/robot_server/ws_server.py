"""
WebSocket 服务端
作为 GUI 应用的可选附加服务运行，与 GUI 共用 ApplicationServices。

协议说明:
    前端 → 服务端（指令）:

    === 执行控制 ===
        {"action": "execute",       "sequence": [...]}     执行动作序列
        {"action": "execute_task",  "name": "xxx.task"}    加载并执行已保存的任务
        {"action": "stop"}                                 停止当前执行
        {"action": "quick_stop"}                           向支持的运动设备发送软件快停
        {"action": "emergency_stop"}                       向支持的运动设备发送软件急停
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
        {"action": "ai_chat",      "text": "帮我抓一个瓶子"}  远程文本意图入口（chat/command/vision/session）
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

    === LLM 聊天 / MiniCPM 状态 ===
        {"action": "minicpm_status"}                       查询 MiniCPM 网关配置与状态
        {"action": "chat_connect"}                         建立聊天会话（标记当前连接进入聊天模式）
        {"action": "chat",         "messages": [...]}      发送聊天消息（底层由 LLM provider 处理）
        {"action": "chat",         "messages": [...], "route_to_interaction": true}
                                                              聊天同时显式路由到当前机器人意图入口
        {"action": "chat_disconnect"}                      断开聊天会话

WebSocket 路径:
    ws://{host}:{port}/               — 前端主控连接（本协议，所有功能均通过 action 字段分发）

    服务端 → 前端（事件推送）:
        {"event": "step_started",       "index": 0, "name": "...", "status": "RUNNING", "control_policy": {...}}
        {"event": "step_completed",     "index": 0, "name": "..."}
        {"event": "step_failed",        "index": 0, "name": "...", "error": "...", "failure": {...}}
        {"event": "log",                "level": "info|warn|error", "message": "..."}
        {"event": "execution_finished"}
        {"event": "error",              "message": "..."}              # 请求参数校验错误
        {"event": "ai_status_changed",  "status": "分析中..."}
        {"event": "ai_skill_matched",   "skill_id": "...", "skill_name": "...", "params": {...}}
        {"event": "ai_preview_ready",   "sequence": [...], "skill_info": {...},
         "validation": {"is_valid": true, "code": "valid"},
         "requires_confirmation": true}
        {"event": "ai_execution_finished", "success": true, "message": "..."}
        {"event": "chat_connected"}                                    # 聊天会话已建立
        {"event": "chat_disconnected"}                                 # 聊天会话已断开
        {"event": "chat_data",          "type": "chunk", ...}          # LLM 聊天响应（规范化字段 + 完整 packet）
        {"event": "minicpm_instruction","instruction": "..."}          # 检测到可执行机器人指令

    log 事件 level 取值:
        info  — 常规执行日志（默认）
        warn  — 可恢复的异常，如重试中
        error — 执行失败或硬件异常

启动方式:
    python run.py
"""

import asyncio
import base64
import json
import logging
import os
import re
import threading
from collections.abc import Coroutine
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)
from uuid import uuid4

try:
    import websockets
except ImportError:
    websockets = None

from ..application import (
    ApplicationServices,
    CameraSession,
    CompositionChangeType,
    CompositionEvent,
)
from ..core.models import ActionDefinition, ActionType, SequenceItem, SequenceItemStatus, LoopBlock, SequenceEntry
from ..core.config_loader import Config
from ..device_runtime.ids import BODY_AXIS, CAMERA, ROBOT_SYSTEM
from ..device_runtime import StopMode
from ..execution import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionState,
)
from ..llm import LLMCapability, LLMContentPart, LLMMessage, LLMRegistry, LLMStreamEvent
from ..voice_interaction import CamerasModuleProvider, WakeFeedback, VoiceInteractionController
from .access_control import (
    AuditSink,
    WebSocketAccessController,
    WebSocketAccessError,
    WebSocketAccessLevel,
    WebSocketAuditEvent,
    log_websocket_audit_event,
)
from .protocol import (
    RequestCorrelation,
    WEBSOCKET_API_VERSION,
    WebSocketErrorCode,
    WebSocketRequestContext,
)
from .request_limits import WebSocketRequestLimiter


if TYPE_CHECKING:
    from ..data_collection import RLBenchRecorder


logger = logging.getLogger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CURRENT_REQUEST: ContextVar[WebSocketRequestContext | None] = ContextVar(
    "websocket_request",
    default=None,
)


@dataclass(frozen=True, slots=True)
class _WebSocketRoute:
    handler: Callable[[Any, dict[str, Any]], Awaitable[None]]
    access_level: WebSocketAccessLevel
    audited: bool = True


class _BoundedWebSocket:
    """Apply a send deadline while preserving the websocket interface."""

    def __init__(self, websocket: Any, timeout_seconds: float) -> None:
        self._websocket = websocket
        self._timeout_seconds = timeout_seconds

    def __aiter__(self):
        return self._websocket.__aiter__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._websocket, name)

    async def send(self, message: str) -> None:
        await asyncio.wait_for(
            self._websocket.send(message),
            timeout=self._timeout_seconds,
        )


class MiniCPMChatConfig:
    """MiniCPM 相关配置，仅用于状态展示。"""

    def __init__(
        self,
        gateway_host: str = "localhost",
        gateway_port: int = 8006,
        ws_scheme: str = "wss",
        gateway_path_prefix: str = "",
        realtime_path: str = "/v1/realtime",
        ask_enabled: bool = True,
        ask_api_key: str = "",
        ask_base_url: str = "",
        ask_model: str = "gpt-4o-mini",
    ) -> None:
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.ws_scheme = self._normalize_ws_scheme(ws_scheme)
        self.gateway_path_prefix = gateway_path_prefix.rstrip("/")
        self.realtime_path = realtime_path
        self.ask_enabled = ask_enabled
        self.ask_api_key = ask_api_key
        self.ask_base_url = ask_base_url
        self.ask_model = ask_model

    @property
    def _port_suffix(self) -> str:
        default = 443 if self.ws_scheme == "wss" else 80
        return "" if self.gateway_port == default else f":{self.gateway_port}"

    @staticmethod
    def _normalize_ws_scheme(scheme: str) -> str:
        scheme = (scheme or "wss").strip().lower()
        if scheme in ("https", "wss"):
            return "wss"
        if scheme in ("http", "ws"):
            return "ws"
        return "wss"


def _extract_user_text(data: dict) -> Optional[str]:
    """从聊天消息体中提取最后一条用户文本。"""
    def _text_from_content(content) -> Optional[str]:
        if isinstance(content, str):
            return content.strip() or None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "").strip()
                    if text:
                        return text
        return None

    messages = data.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            result = _text_from_content(msg.get("content", ""))
            if result:
                return result

    if data.get("role") == "user":
        return _text_from_content(data.get("content", ""))

    return None


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
        services: ApplicationServices,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        auth_token: str = "",
        control_lease_seconds: float = 30.0,
        max_message_size_bytes: int = 1_048_576,
        max_requests_per_second: int = 120,
        max_concurrent_requests: int = 16,
        max_queued_messages: int = 16,
        send_timeout_seconds: float = 2.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        normalized_host = host.strip()
        if not normalized_host:
            raise ValueError("WebSocket host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("WebSocket port must be in range 1..65535")
        if max_message_size_bytes <= 0:
            raise ValueError("max_message_size_bytes must be positive")
        if max_queued_messages <= 0:
            raise ValueError("max_queued_messages must be positive")
        if send_timeout_seconds <= 0:
            raise ValueError("send_timeout_seconds must be positive")

        self._services = services
        self._host = normalized_host
        self._port = port
        self._max_message_size_bytes = max_message_size_bytes
        self._max_queued_messages = max_queued_messages
        self._send_timeout_seconds = send_timeout_seconds
        self._server: Any = None
        self._composition_unsubscribe = None

        # 已连接的客户端集合
        self._clients: Set = set()
        self._client_ids: dict[Any, str] = {}
        self._access = WebSocketAccessController(
            auth_token,
            control_lease_seconds=control_lease_seconds,
        )
        self._audit_sink = audit_sink or log_websocket_audit_event
        self._execution_requests: dict[str, RequestCorrelation] = {}
        self._execution_requests_lock = threading.RLock()
        self._request_limiter = WebSocketRequestLimiter(
            max_requests_per_second=max_requests_per_second,
            max_concurrent_requests=max_concurrent_requests,
        )

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # AI 相关状态
        self._ai_preview_sequence: List[SequenceItem] = []
        self._ai_preview_skill_info: Dict[str, Any] = {}
        self._ai_preview_validated = False
        self._ai_processing = False

        # LLM 客户端和技能引擎（延迟初始化，避免未安装 AI 依赖时报错）
        self._llm_registry: Optional[LLMRegistry] = None
        self._llm_client = None
        self._planner_client = None
        self._skill_engine = None
        self._interaction_controller: Optional[VoiceInteractionController] = None

        # 相机帧订阅：通过 subscribe_camera_frames action 注册
        self._camera_frame_subs: Set = set()
        self._camera_push_task: Optional[asyncio.Task] = None
        self._camera_preview_session: Optional[CameraSession] = None

        # MiniCPM 代理配置（延迟初始化）
        self._minicpm_cfg: Optional[MiniCPMChatConfig] = None

        # LLM 聊天会话：id(websocket) -> {"active": True}
        self._minicpm_sessions: Dict[int, Dict] = {}

        # AI 执行跟踪（用于发送 ai_execution_finished 事件）
        self._ai_execution_pending = False
        self._execution_had_failure = False

        # 遥操作状态（双臂独立）
        self._teleop_modes = {"左": False, "右": False}  # 双臂遥操作状态字典
        self._teleop_msg_counts = {"左": 0, "右": 0}  # 双臂消息计数器字典
        self._last_grip = {"左": None, "右": None}  # 夹爪状态跟踪（避免重复执行）

        # 数据采集状态
        self._demo_recorder: Optional["RLBenchRecorder"] = None
        self._demo_camera_session: Optional[CameraSession] = None
        self._demo_session = {
            "active": False,
            "task": None,
            "description": None,
            "next_episode_id": 0,
        }
        self._routes = self._build_routes()

    @property
    def _robot_system(self):
        return self._services.device_runtime.get_if_ready(ROBOT_SYSTEM)

    @property
    def _body_controller(self):
        return self._services.device_runtime.get_if_ready(BODY_AXIS)

    @property
    def _camera_manager(self):
        return self._services.device_runtime.get_if_ready(CAMERA)

    @property
    def name(self) -> str:
        return "websocket"

    @property
    def endpoint(self) -> str:
        return f"ws://{self._host}:{self._port}/"

    async def start(self) -> None:
        """Bind the socket and return after the service is ready."""
        if websockets is None:
            raise RuntimeError("websockets 库未安装，无法启动 WebSocket 服务")
        if self._server is not None:
            raise RuntimeError("WebSocket service is already running")
        self._loop = asyncio.get_running_loop()
        self._composition_unsubscribe = (
            self._services.composition.subscribe(
                self._on_composition_event
            )
        )

        # 初始化 AI 组件
        self._init_ai()

        # 相机在视觉动作、测试或订阅实时预览时按需启动。

        # 加载 MiniCPM 代理配置
        self._init_minicpm_config()

        self._server = await websockets.serve(
            self._handler,
            self._host,
            self._port,
            max_size=self._max_message_size_bytes,
            max_queue=self._max_queued_messages,
        )
        self._schedule_background_task(
            self._control_lease_monitor(),
            name="WebSocketControlLeaseMonitor",
        )
        logger.info(
            "WebSocket 服务已启动: %s, write_auth_configured=%s",
            self.endpoint,
            self._access.authentication_configured,
        )

    async def stop(self) -> None:
        """Stop accepting clients and release service-owned async resources."""
        unsubscribe = self._composition_unsubscribe
        self._composition_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

        self._camera_frame_subs.clear()
        await self._cancel_background_tasks()
        self._stop_camera_if_idle()
        await self._close_llm_clients()
        await self._close_demo_recorder()
        self._clients.clear()
        self._client_ids.clear()
        self._access.clear()
        self._minicpm_sessions.clear()
        self._loop = None
        logger.info("WebSocket 服务已停止")

    def _on_composition_event(
        self,
        event: CompositionEvent,
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def schedule() -> None:
            self._schedule_background_task(
                self._broadcast_composition_event(event),
                name="WebSocketCompositionChanged",
            )

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            logger.debug(
                "WebSocket loop closed before composition event scheduling"
            )

    async def _broadcast_composition_event(
        self,
        event: CompositionEvent,
    ) -> None:
        payload = await asyncio.to_thread(
            self._composition_event_payload,
            event,
        )
        await self._broadcast(payload)

    def _composition_event_payload(
        self,
        event: CompositionEvent,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "composition_changed",
            "change": event.change_type.value,
            "revision": event.revision,
            "change_revision": event.change_revision,
            "origin": event.origin,
        }
        composition = self._services.composition
        if event.change_type is CompositionChangeType.SEQUENCE:
            payload["sequence"] = [
                entry.to_dict()
                for entry in composition.sequence_entries()
            ]
        elif event.change_type is CompositionChangeType.ACTIONS:
            payload["actions"] = [
                action.to_dict()
                for action in composition.list_actions()
            ]
        elif event.change_type is CompositionChangeType.TASKS:
            payload["tasks"] = [
                {
                    "name": summary.name,
                    "steps": summary.step_count,
                }
                for summary in composition.list_tasks()
            ]
        return payload

    def _schedule_background_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_finished)
        return task

    def _background_task_finished(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "WebSocket background task %s failed: %s",
                task.get_name(),
                error,
            )

    async def _cancel_background_tasks(self) -> None:
        tasks = tuple(
            task
            for task in self._background_tasks
            if not task.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._camera_push_task = None

    async def _close_llm_clients(self) -> None:
        registry = self._llm_registry
        self._llm_registry = None
        self._interaction_controller = None
        self._llm_client = None
        self._planner_client = None
        if registry is None:
            return

        clients = tuple(
            registry.get_provider(provider_name)
            for provider_name in registry.loaded_provider_names
        )
        results = await asyncio.gather(
            *(client.close() for client in clients),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("关闭 WebSocket LLM client 失败: %s", result)

    async def _close_demo_recorder(self) -> None:
        recorder = self._demo_recorder
        self._demo_recorder = None
        camera_session = self._demo_camera_session
        self._demo_camera_session = None
        if recorder is None and camera_session is None:
            return
        errors: list[Exception] = []
        if recorder is not None:
            try:
                await asyncio.to_thread(recorder.stop_recording)
            except Exception as exc:
                errors.append(exc)
            try:
                recorder.end_session()
            except Exception as exc:
                errors.append(exc)
        try:
            self._services.teleoperation.stop()
        except Exception as exc:
            errors.append(exc)
        finally:
            if camera_session is not None:
                camera_session.close()
            self._demo_session = {
                "active": False,
                "task": None,
                "description": None,
                "next_episode_id": 0,
            }
        if errors:
            detail = "; ".join(str(error) for error in errors)
            raise RuntimeError(
                f"failed to close data collection recorder: {detail}"
            )

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

            # 初始化 LLM 能力层
            self._llm_registry = LLMRegistry.from_config(config)
            logger.info(
                "LLMRegistry 就绪: default=%s, providers=%s",
                self._llm_registry.default_provider,
                self._llm_registry.describe_providers(),
            )

            voice_config = Config.get_voice_interaction_config()
            self._interaction_controller = VoiceInteractionController(
                llm_registry=self._llm_registry,
                skill_engine=self._skill_engine,
                camera_provider=CamerasModuleProvider(
                    session_factory=self._camera_capture_session,
                    camera_name=config.VISION_CAMERA_NAME or None,
                ),
                timeout_s=voice_config["session_timeout_s"],
                history_turns=voice_config["session_history_turns"],
                cancel_callback=self._cancel_current_ai_task,
                tts_enabled=voice_config["tts_enabled"],
                wake_feedback=WakeFeedback(
                    enabled=bool(voice_config.get("wake_feedback_enabled", True)),
                    text=str(voice_config.get("wake_feedback_text") or "明德博士在，请说。"),
                ),
            )

        except Exception as e:
            logger.warning("AI 组件初始化失败: %s", e)

    def _get_chat_client(self, provider: Optional[str] = None):
        if self._llm_registry is not None:
            return self._llm_registry.get_chat_client(provider)
        return self._llm_client

    def _camera_capture_session(self):
        return self._services.camera_access.open(
            "websocket-voice-capture"
        )

    def _get_planner_client(self, provider: Optional[str] = None):
        if self._llm_registry is not None:
            return self._llm_registry.get_planner_client(provider)
        return self._planner_client

    # ------------------------------------------------------------------
    # 连接处理
    # ------------------------------------------------------------------

    async def _handler(self, websocket) -> None:
        """所有连接统一进入主控处理器，通过 action 字段分发指令。"""
        await self._handle_frontend_ws(_BoundedWebSocket(
            websocket,
            self._send_timeout_seconds,
        ))

    async def _handle_frontend_ws(self, websocket) -> None:
        """处理前端主控 WebSocket 连接"""
        remote = getattr(websocket, "remote_address", None)
        client_id = self._register_client(websocket, remote)
        logger.info(
            "前端客户端已连接: client_id=%s remote=%s",
            client_id,
            remote,
        )
        await websocket.send(self._json_msg({
            "event": "connected",
            "client_id": client_id,
            "authentication_configured": (
                self._access.authentication_configured
            ),
            "control_lease_seconds": (
                self._access.control_lease_seconds
            ),
            "api_version_required": True,
        }))

        try:
            async for raw in websocket:
                logger.debug(
                    "收到 WebSocket 消息: client_id=%s bytes=%d",
                    client_id,
                    len(raw),
                )

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(self._json_msg(
                        {
                            "event": "error",
                            "code": (
                                WebSocketErrorCode.INVALID_REQUEST.value
                            ),
                            "message": "无效的 JSON 格式",
                        }
                    ))
                    continue

                await self._dispatch(websocket, data)

        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "前端客户端断开: client_id=%s remote=%s",
                client_id,
                remote,
            )
        finally:
            await self._unregister_client(
                websocket,
                reason="disconnect",
            )


    # ------------------------------------------------------------------
    # 指令分发
    # ------------------------------------------------------------------

    def _build_routes(self) -> dict[str, _WebSocketRoute]:
        public = WebSocketAccessLevel.PUBLIC
        authenticated = WebSocketAccessLevel.AUTHENTICATED
        control = WebSocketAccessLevel.CONTROL
        return {
            # 安全会话
            "authenticate": _WebSocketRoute(
                self._handle_authenticate,
                public,
            ),
            "control_status": _WebSocketRoute(
                self._handle_control_status,
                public,
                audited=False,
            ),
            "acquire_control": _WebSocketRoute(
                self._handle_acquire_control,
                authenticated,
            ),
            "control_heartbeat": _WebSocketRoute(
                self._handle_control_heartbeat,
                control,
            ),
            "release_control": _WebSocketRoute(
                self._handle_release_control,
                control,
            ),
            # 执行控制
            "execute": _WebSocketRoute(self._handle_execute, control),
            "execute_task": _WebSocketRoute(
                self._handle_execute_task,
                control,
            ),
            "stop": _WebSocketRoute(self._handle_stop, control),
            "quick_stop": _WebSocketRoute(
                self._handle_quick_stop,
                control,
            ),
            "emergency_stop": _WebSocketRoute(
                self._handle_emergency_stop,
                control,
            ),
            "pause": _WebSocketRoute(self._handle_pause, control),
            "resume": _WebSocketRoute(self._handle_resume, control),
            # 动作库管理
            "list_actions": _WebSocketRoute(
                self._handle_list_actions,
                public,
                audited=False,
            ),
            "get_action_schema": _WebSocketRoute(
                self._handle_get_action_schema,
                public,
                audited=False,
            ),
            "create_action": _WebSocketRoute(
                self._handle_create_action,
                control,
            ),
            "delete_action": _WebSocketRoute(
                self._handle_delete_action,
                control,
            ),
            "update_action": _WebSocketRoute(
                self._handle_update_action,
                control,
            ),
            # 序列编排
            "get_sequence": _WebSocketRoute(
                self._handle_get_sequence,
                public,
                audited=False,
            ),
            "add_to_sequence": _WebSocketRoute(
                self._handle_add_to_sequence,
                control,
            ),
            "remove_from_sequence": _WebSocketRoute(
                self._handle_remove_from_sequence,
                control,
            ),
            "move_in_sequence": _WebSocketRoute(
                self._handle_move_in_sequence,
                control,
            ),
            "clear_sequence": _WebSocketRoute(
                self._handle_clear_sequence,
                control,
            ),
            # 任务持久化
            "list_tasks": _WebSocketRoute(
                self._handle_list_tasks,
                public,
                audited=False,
            ),
            "save_task": _WebSocketRoute(
                self._handle_save_task,
                control,
            ),
            "load_task": _WebSocketRoute(
                self._handle_load_task,
                control,
            ),
            "delete_task": _WebSocketRoute(
                self._handle_delete_task,
                control,
            ),
            "get_task_detail": _WebSocketRoute(
                self._handle_get_task_detail,
                public,
                audited=False,
            ),
            "rename_task": _WebSocketRoute(
                self._handle_rename_task,
                control,
            ),
            "add_to_task": _WebSocketRoute(
                self._handle_add_to_task,
                control,
            ),
            "remove_from_task": _WebSocketRoute(
                self._handle_remove_from_task,
                control,
            ),
            "move_in_task": _WebSocketRoute(
                self._handle_move_in_task,
                control,
            ),
            # AI 助手
            "ai_chat": _WebSocketRoute(self._handle_ai_chat, control),
            "ai_confirm": _WebSocketRoute(
                self._handle_ai_confirm,
                control,
            ),
            "ai_cancel": _WebSocketRoute(
                self._handle_ai_cancel,
                control,
            ),
            "ai_status": _WebSocketRoute(
                self._handle_ai_status,
                public,
                audited=False,
            ),
            "list_skills": _WebSocketRoute(
                self._handle_list_skills,
                public,
                audited=False,
            ),
            # 设备管理
            "status": _WebSocketRoute(
                self._handle_status,
                public,
                audited=False,
            ),
            "init_robots": _WebSocketRoute(
                self._handle_init_robots,
                control,
            ),
            "init_body": _WebSocketRoute(
                self._handle_init_body,
                control,
            ),
            "disconnect": _WebSocketRoute(
                self._handle_disconnect,
                control,
            ),
            "test_camera": _WebSocketRoute(
                self._handle_test_camera,
                control,
            ),
            # 相机读取会话
            "camera_status": _WebSocketRoute(
                self._handle_camera_status,
                public,
                audited=False,
            ),
            "subscribe_camera_frames": _WebSocketRoute(
                self._handle_subscribe_camera_frames,
                authenticated,
            ),
            "unsubscribe_camera_frames": _WebSocketRoute(
                self._handle_unsubscribe_camera_frames,
                authenticated,
            ),
            # LLM 聊天
            "chat_connect": _WebSocketRoute(
                self._handle_chat_connect,
                authenticated,
            ),
            "chat_disconnect": _WebSocketRoute(
                self._handle_chat_disconnect,
                authenticated,
            ),
            "chat": _WebSocketRoute(
                self._handle_chat_send,
                authenticated,
            ),
            "minicpm_status": _WebSocketRoute(
                self._handle_minicpm_status,
                public,
                audited=False,
            ),
            # 遥操作与数据采集
            "teleop_init": _WebSocketRoute(
                self._handle_teleop_init,
                control,
            ),
            "teleop_start": _WebSocketRoute(
                self._handle_teleop_start,
                control,
            ),
            "teleop_joint": _WebSocketRoute(
                self._handle_teleop_joint,
                control,
            ),
            "teleop_stop": _WebSocketRoute(
                self._handle_teleop_stop,
                control,
            ),
            "demo_session_start": _WebSocketRoute(
                self._handle_demo_session_start,
                control,
            ),
            "demo_record_start": _WebSocketRoute(
                self._handle_demo_record_start,
                control,
            ),
            "demo_record_stop": _WebSocketRoute(
                self._handle_demo_record_stop,
                control,
            ),
            "demo_session_end": _WebSocketRoute(
                self._handle_demo_session_end,
                control,
            ),
        }

    async def _dispatch(self, websocket, data: object) -> None:
        """根据 action 字段分发到对应处理函数"""
        if not isinstance(data, dict):
            await websocket.send(self._json_msg(
                {
                    "event": "error",
                    "code": WebSocketErrorCode.INVALID_REQUEST.value,
                    "message": "请求必须是 JSON 对象",
                }
            ))
            return

        request_id = self._request_id(data)
        if request_id is None:
            await websocket.send(self._json_msg({
                "event": "error",
                "code": WebSocketErrorCode.INVALID_REQUEST_ID.value,
                "message": (
                    "request_id 只能包含字母、数字、点、下划线、冒号或连字符，"
                    "长度为 1..128"
                ),
            }))
            return

        action_value = data.get("action")
        action = action_value if isinstance(action_value, str) else ""
        api_version = data.get("api_version")
        if api_version != WEBSOCKET_API_VERSION:
            code = (
                WebSocketErrorCode.API_VERSION_REQUIRED.value
                if api_version is None
                else WebSocketErrorCode.UNSUPPORTED_API_VERSION.value
            )
            await websocket.send(self._json_msg({
                "event": "error",
                "code": code,
                "action": action,
                "request_id": request_id,
                "message": (
                    f"请求必须声明 api_version={WEBSOCKET_API_VERSION}"
                ),
                "supported_api_versions": [WEBSOCKET_API_VERSION],
            }))
            return

        route = self._routes.get(action)
        if route is None:
            await websocket.send(self._json_msg({
                "event": "error",
                "code": WebSocketErrorCode.UNKNOWN_ACTION.value,
                "action": action,
                "request_id": request_id,
                "message": f"未知的 action: {action}",
            }))
            return

        client_id = self._client_id(websocket)
        request = dict(data)
        request["request_id"] = request_id
        initial_session = self._access.session(client_id)
        request_context = WebSocketRequestContext(
            correlation=RequestCorrelation(
                client_id=client_id,
                principal=initial_session.principal,
                action=action,
                request_id=request_id,
            )
        )
        context_token = _CURRENT_REQUEST.set(request_context)
        admission = self._request_limiter.admit(client_id)
        try:
            if not admission.accepted:
                await websocket.send(self._json_msg({
                    "event": "error",
                    "code": admission.code,
                    "message": (
                        "请求频率超过限制"
                        if admission.code
                        == WebSocketErrorCode.RATE_LIMITED.value
                        else "服务器并发请求已达到上限"
                    ),
                    "retry_after_seconds": (
                        admission.retry_after_seconds
                    ),
                }))
                if route.audited:
                    self._audit(
                        client_id=client_id,
                        action=action,
                        request_id=request_id,
                        outcome="rejected",
                        code=admission.code,
                    )
                return

            expired_client_id = self._access.expire_control()
            if expired_client_id is not None:
                await self._release_control_side_effects(
                    expired_client_id,
                    reason="expired",
                )

            session = self._access.authorize(
                client_id,
                route.access_level,
            )
            request_context.correlation = RequestCorrelation(
                client_id=client_id,
                principal=session.principal,
                action=action,
                request_id=request_id,
            )
            await route.handler(websocket, request)
        except WebSocketAccessError as exc:
            await websocket.send(self._json_msg({
                "event": "access_denied",
                "code": exc.code,
                "message": str(exc),
            }))
            if route.audited:
                self._audit(
                    client_id=client_id,
                    action=action,
                    request_id=request_id,
                    outcome="denied",
                    code=exc.code,
                )
        except Exception as exc:
            logger.exception(
                "WebSocket 请求处理异常: client_id=%s action=%s "
                "request_id=%s",
                client_id,
                action,
                request_id,
            )
            await websocket.send(self._json_msg({
                "event": "error",
                "code": WebSocketErrorCode.INTERNAL_ERROR.value,
                "message": "请求处理发生内部错误",
            }))
            if route.audited:
                self._audit(
                    client_id=client_id,
                    action=action,
                    request_id=request_id,
                    outcome="failed",
                    code=type(exc).__name__,
                )
        else:
            if route.audited and not request_context.initial_audit_recorded:
                outcome = (
                    "accepted"
                    if request_context.run_id is not None
                    else (
                        "rejected"
                        if request_context.error_code is not None
                        else "completed"
                    )
                )
                self._audit(
                    client_id=client_id,
                    action=action,
                    request_id=request_id,
                    outcome=outcome,
                    code=request_context.error_code,
                    run_id=request_context.run_id,
                )
        finally:
            if admission.accepted:
                self._request_limiter.release(client_id)
            _CURRENT_REQUEST.reset(context_token)

    def _register_client(self, websocket: Any, remote: object) -> str:
        client_id = uuid4().hex
        self._clients.add(websocket)
        self._client_ids[websocket] = client_id
        self._access.register(client_id, str(remote or "unknown"))
        return client_id

    async def _unregister_client(
        self,
        websocket: Any,
        *,
        reason: str,
    ) -> None:
        self._clients.discard(websocket)
        self._camera_frame_subs.discard(websocket)
        self._stop_camera_if_idle()
        client_id = self._client_ids.pop(websocket, None)
        if client_id is not None:
            self._request_limiter.unregister(client_id)
            if self._access.unregister(client_id):
                await self._release_control_side_effects(
                    client_id,
                    reason=reason,
                )
        await self._close_minicpm_session(websocket)

    async def _release_control_side_effects(
        self,
        client_id: str,
        *,
        reason: str,
    ) -> None:
        try:
            if (
                self._demo_recorder is not None
                or self._demo_camera_session is not None
            ):
                await self._close_demo_recorder()
            elif self._services.teleoperation.active:
                self._services.teleoperation.stop()
        except Exception as exc:
            logger.error(
                "释放控制客户端会话失败: client_id=%s reason=%s error=%s",
                client_id,
                reason,
                exc,
            )
        finally:
            for arm_name in self._teleop_modes:
                self._teleop_modes[arm_name] = False
        await self._broadcast({
            "event": "control_released",
            "client_id": client_id,
            "reason": reason,
        })

    async def _control_lease_monitor(self) -> None:
        interval_seconds = max(
            0.1,
            min(1.0, self._access.control_lease_seconds / 4),
        )
        while True:
            await asyncio.sleep(interval_seconds)
            expired_client_id = self._access.expire_control()
            if expired_client_id is not None:
                await self._release_control_side_effects(
                    expired_client_id,
                    reason="expired",
                )

    def _client_id(self, websocket: Any) -> str:
        try:
            return self._client_ids[websocket]
        except KeyError as exc:
            raise WebSocketAccessError(
                "unknown_client",
                "WebSocket 客户端尚未注册",
            ) from exc

    @staticmethod
    def _request_id(data: dict[str, Any]) -> str | None:
        value = data.get("request_id")
        if value is None:
            return uuid4().hex
        if not isinstance(value, str):
            return None
        return value if _REQUEST_ID_PATTERN.fullmatch(value) else None

    def _audit(
        self,
        *,
        client_id: str,
        action: str,
        request_id: str,
        outcome: str,
        code: str | None = None,
        principal: str | None = None,
        run_id: str | None = None,
    ) -> None:
        if principal is None:
            try:
                principal = self._access.session(client_id).principal
            except WebSocketAccessError:
                principal = None
        event = WebSocketAuditEvent.create(
            client_id=client_id,
            principal=principal,
            action=action,
            request_id=request_id,
            outcome=outcome,
            code=code,
            run_id=run_id,
        )
        try:
            self._audit_sink(event)
        except Exception as exc:
            logger.error(
                "WebSocket 安全审计写入失败: %s",
                type(exc).__name__,
            )

    async def _handle_authenticate(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        session = self._access.authenticate(
            self._client_id(websocket),
            data.get("token"),
        )
        await websocket.send(self._json_msg({
            "event": "authenticated",
            "client_id": session.client_id,
            "principal": session.principal,
            "request_id": data["request_id"],
        }))

    async def _handle_control_status(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        client_id = self._client_id(websocket)
        session = self._access.session(client_id)
        lease = self._access.control_snapshot()
        await websocket.send(self._json_msg({
            "event": "control_status",
            "client_id": client_id,
            "authenticated": session.authenticated,
            "authentication_configured": (
                self._access.authentication_configured
            ),
            "control_lease": lease.to_dict() if lease else None,
            "request_id": data["request_id"],
        }))

    async def _handle_acquire_control(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        lease = self._access.acquire_control(
            self._client_id(websocket)
        )
        await websocket.send(self._json_msg({
            "event": "control_acquired",
            "control_lease": lease.to_dict(),
            "request_id": data["request_id"],
        }))

    async def _handle_control_heartbeat(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        lease = self._access.renew_control(
            self._client_id(websocket)
        )
        await websocket.send(self._json_msg({
            "event": "control_heartbeat",
            "control_lease": lease.to_dict(),
            "request_id": data["request_id"],
        }))

    async def _handle_release_control(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        client_id = self._client_id(websocket)
        released = self._access.release_control(client_id)
        if released:
            await self._release_control_side_effects(
                client_id,
                reason="released",
            )
        await websocket.send(self._json_msg({
            "event": "control_release_completed",
            "released": released,
            "request_id": data["request_id"],
        }))

    # ==================================================================
    # 执行控制
    # ==================================================================

    async def _submit_execution(
        self,
        websocket,
        sequence: list,
        *,
        origin: str,
        message: str,
        steps: int,
    ) -> bool:
        gate_lock = threading.Lock()
        pending_events: list[ExecutionEvent] = []
        events_released = False

        def listener(event: ExecutionEvent) -> None:
            nonlocal events_released
            with gate_lock:
                if not events_released:
                    pending_events.append(event)
                    return
            self._on_execution_event(event)

        try:
            handle = self._services.execution.start(
                sequence,
                origin=origin,
                listener=listener,
            )
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "error",
                "message": f"提交执行失败: {exc}",
            }))
            return False
        request_context = _CURRENT_REQUEST.get()
        if request_context is not None:
            request_context.run_id = handle.run_id
            with self._execution_requests_lock:
                self._execution_requests[handle.run_id] = (
                    request_context.execution_correlation()
                )
            correlation = request_context.correlation
            self._audit(
                client_id=correlation.client_id,
                principal=correlation.principal,
                action=correlation.action,
                request_id=correlation.request_id,
                outcome="accepted",
                run_id=handle.run_id,
            )
            request_context.initial_audit_recorded = True
        await self._broadcast({
            "event": "accepted",
            "run_id": handle.run_id,
            "message": message,
            "steps": steps,
        })
        with gate_lock:
            for event in pending_events:
                self._on_execution_event(event)
            pending_events.clear()
            events_released = True
        return True

    async def _handle_execute(self, websocket, data: dict) -> None:
        """
        执行动作序列
        请求: {"action": "execute", "sequence": [...]}
        如果 sequence 省略，则执行当前编排的序列
        """
        if self._services.execution.snapshot().active:
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
            sequence = list(
                self._services.composition.sequence_entries()
            )

        if not sequence:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "序列为空，请先添加动作"}
            ))
            return

        # 重置状态
        total_steps = 0
        for entry in sequence:
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for child in entry.items:
                    child.status = SequenceItemStatus.PENDING
                total_steps += len(entry.items) * entry.repeat_count
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING
                total_steps += 1

        await self._submit_execution(
            websocket,
            sequence,
            origin="websocket",
            message="开始执行",
            steps=total_steps,
        )

    async def _handle_execute_task(self, websocket, data: dict) -> None:
        """
        加载并执行已保存的任务
        请求: {"action": "execute_task", "name": "xxx.task"}
        """
        if self._services.execution.snapshot().active:
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

        try:
            entries = await asyncio.to_thread(
                self._services.composition.load_task,
                task_name,
            )
        except (FileNotFoundError, ValueError):
            entries = ()
        if not entries:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        for entry in entries:
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for child in entry.items:
                    child.status = SequenceItemStatus.PENDING
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING

        total_steps = sum(
            len(e.items) * e.repeat_count if isinstance(e, LoopBlock) else 1
            for e in entries
        )

        await self._submit_execution(
            websocket,
            entries,
            origin="websocket-task",
            message=f"加载任务 '{task_name}'，开始执行",
            steps=total_steps,
        )

    async def _handle_stop(self, websocket, data: dict) -> None:
        """请求协作式停止任务；该接口不会触发设备硬件急停。"""
        if self._services.execution.snapshot().active:
            if self._ai_execution_pending:
                self._execution_had_failure = True  # 人工停止视为未成功完成
            self._services.execution.cancel()
            await websocket.send(self._json_msg(
                {
                    "event": "stopped",
                    "message": "已发送任务停止请求（非硬件急停）",
                }
            ))
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "当前没有正在执行的序列"}
            ))

    async def _handle_quick_stop(self, websocket, data: dict) -> None:
        del data
        await self._handle_safety_stop(websocket, StopMode.QUICK)

    async def _handle_emergency_stop(self, websocket, data: dict) -> None:
        del data
        await self._handle_safety_stop(websocket, StopMode.EMERGENCY)

    async def _handle_safety_stop(
        self,
        websocket,
        mode: StopMode,
    ) -> None:
        report = await asyncio.to_thread(self._services.safety.stop, mode)
        if report.execution_before.active and self._ai_execution_pending:
            self._execution_had_failure = True
        for arm_name in self._teleop_modes:
            self._teleop_modes[arm_name] = False
        await websocket.send(
            self._json_msg(
                {
                    "event": "safety_stop_completed",
                    "report": report.to_dict(),
                }
            )
        )

    async def _handle_pause(self, websocket, data: dict) -> None:
        """暂停执行"""
        snapshot = self._services.execution.snapshot()
        if snapshot.state is ExecutionState.RUNNING:
            self._services.execution.pause()
            await websocket.send(self._json_msg(
                {"event": "paused", "message": "执行已暂停"}
            ))
        else:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "无法暂停：未在执行或已暂停"}
            ))

    async def _handle_resume(self, websocket, data: dict) -> None:
        """恢复执行"""
        snapshot = self._services.execution.snapshot()
        if snapshot.state is ExecutionState.PAUSED:
            self._services.execution.resume()
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
        all_actions = self._services.composition.list_actions()

        # 按类型分组（与 GUI 左侧 Tab 对应）
        grouped = {
            action_type.value: []
            for action_type in ActionType
        }
        for a in all_actions:
            grouped[a.type.value].append(a.to_dict())

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
                            "容量":   {"type": "number", "min": 0, "max": 10000, "default": 500, "unit": "ul", "label": "容量"},
                            "吸液速度": {"type": "number", "min": 1, "max": 9999, "default": 1200, "unit": "ul/s", "label": "吸液速度"},
                            "吐液速度": {"type": "number", "min": 1, "max": 9999, "default": 800, "unit": "ul/s", "label": "吐液速度"},
                            "吐液容量模式": {"type": "select", "options": ["指定容量", "全吐"], "default": "指定容量", "label": "吐液容量"}
                        }
                    },
                    "右臂转圈注液": {
                        "description": "Robot2 以给定位姿的 x/y 为圆心画圆，同时控制吸液枪吐液",
                        "fields": {
                            "执行器": {"type": "select", "options": ["右臂转圈注液"], "default": "右臂转圈注液", "label": "执行器"},
                            "位姿": {"type": "text", "placeholder": "例如: [-0.058,-0.412,-0.154,-2.934,0.428,-2.722]", "label": "圆心位姿", "required": True},
                            "半径R": {"type": "number", "min": 0.1, "max": 500, "default": 10, "unit": "mm", "label": "半径R"},
                            "吐液速度": {"type": "number", "min": 1, "max": 9999, "default": 800, "unit": "ul/s", "label": "吐液速度"},
                            "吐液量": {"type": "number", "min": 1, "max": 10000, "default": 500, "unit": "ul", "label": "吐液量"},
                            "圈数": {"type": "number", "min": 0.1, "max": 20, "default": 1, "label": "圈数"},
                            "分段数": {"type": "number", "min": 8, "max": 360, "default": 72, "label": "每圈分段"},
                            "过渡半径": {"type": "number", "min": 0, "max": 100, "default": 20, "label": "过渡半径"},
                            "运动速度": {"type": "number", "min": 1, "max": 100, "default": 10, "label": "运动速度"},
                            "连续运动": {"type": "boolean", "default": True, "label": "连续运动"},
                            "顺时针": {"type": "boolean", "default": False, "label": "顺时针"}
                        }
                    },
                    "加粉装置": {
                        "description": "手动控制加粉装置夹爪、升降和旋转",
                        "fields": {
                            "执行器": {"type": "select", "options": ["加粉装置"], "default": "加粉装置", "label": "执行器"},
                            "操作": {"type": "select", "options": ["使能", "夹爪移动到", "夹爪闭合", "夹爪张开", "针下降", "针上升", "针正转", "针反转", "针停止", "针旋转停止"], "default": "使能", "label": "操作"},
                            "步数": {"type": "number", "min": -500000, "max": 500000, "default": 5000, "unit": "步", "label": "步数"},
                            "开度": {"type": "number", "min": 0, "max": 100, "default": 50, "unit": "%", "label": "夹爪开度"}
                        }
                    },
                    "智能加粉": {
                        "description": "读取天平并闭环控制加粉装置，直到达到目标加粉量",
                        "fields": {
                            "执行器": {"type": "select", "options": ["智能加粉"], "default": "智能加粉", "label": "执行器"},
                            "操作": {"type": "select", "options": ["加粉到目标重量"], "default": "加粉到目标重量", "label": "操作"},
                            "目标重量mg": {"type": "number", "min": 0.1, "max": 100000, "default": 100, "unit": "mg", "label": "目标重量"},
                            "容差mg": {"type": "number", "min": 0.1, "max": 10000, "default": 5, "unit": "mg", "label": "容差"},
                            "最大轮次": {"type": "number", "min": 1, "max": 200, "default": 20, "label": "最大轮次"},
                            "稳定等待秒数": {"type": "number", "min": 0, "max": 60, "default": 2, "unit": "s", "label": "稳定等待"},
                            "安全位置步数": {"type": "number", "min": -500000, "max": 500000, "default": 0, "unit": "步", "label": "安全位置"},
                            "加粉位置步数": {"type": "number", "min": -500000, "max": 500000, "default": 50000, "unit": "步", "label": "加粉位置"},
                            "旋转原点步数": {"type": "number", "min": -500000, "max": 500000, "default": 0, "unit": "步", "label": "旋转原点"},
                            "大步步数": {"type": "number", "min": 1, "max": 500000, "default": 20000, "unit": "步", "label": "大步"},
                            "中步步数": {"type": "number", "min": 1, "max": 500000, "default": 8000, "unit": "步", "label": "中步"},
                            "小步步数": {"type": "number", "min": 1, "max": 500000, "default": 2000, "unit": "步", "label": "小步"},
                            "微步步数": {"type": "number", "min": 1, "max": 500000, "default": 500, "unit": "步", "label": "微步"}
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
            },
            "VISION_RELOCALIZE": {
                "label": "视觉重定位",
                "description": "移动到拍照位，识别 Tag，并更新本次任务的工位定位状态",
                "fields": {
                    "action_mode": {"type": "select", "options": ["run", "teach"], "default": "run", "label": "动作模式"},
                    "arm": {"type": "select", "options": ["left", "right"], "default": "left", "label": "机械臂"},
                    "station_name": {"type": "text", "label": "工位名称", "required": True},
                    "photo_pose": {"type": "text", "label": "示教拍照位姿"},
                    "camera_name": {"type": "text", "label": "示教相机名称"},
                    "marker_width": {"type": "number", "min": 0.000001, "default": 0.158, "label": "示教marker宽度(同位姿单位)"},
                    "marker_height": {"type": "number", "min": 0.000001, "default": 0.158, "label": "示教marker高度(同位姿单位)"},
                    "move_mode": {"type": "select", "options": ["move_j", "move_l"], "default": "move_j", "label": "移动模式"}
                },
                "note": "工位名称是唯一用户输入；内部兼容 station_id。photo_pose、camera_name、marker 宽高只在 action_mode=teach 时填写；run 时只需要选择已保存的工位"
            },
            "TRAJECTORY": {
                "label": "轨迹类",
                "description": "执行已录制的机械臂轨迹文件",
                "fields": {
                    "robot": {"type": "select", "options": ["robot1", "robot2"], "default": "robot1", "label": "机械臂"},
                    "file_path": {"type": "text", "label": "轨迹文件", "required": True}
                }
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

        try:
            action_def = await asyncio.to_thread(
                self._services.composition.create_action,
                action_def,
                origin=self._composition_origin(websocket),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send(self._json_msg(
                {"event": "error", "message": str(exc)}
            ))
            return

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

        try:
            await asyncio.to_thread(
                self._services.composition.delete_action,
                action_id,
                origin=self._composition_origin(websocket),
            )
        except KeyError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"未找到 id 为 '{action_id}' 的动作"}
            ))
            return

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

        try:
            target = self._services.composition.get_action(action_id)
        except KeyError:
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

        try:
            target = await asyncio.to_thread(
                self._services.composition.update_action,
                action_id,
                target,
                origin=self._composition_origin(websocket),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send(self._json_msg(
                {"event": "error", "message": str(exc)}
            ))
            return
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
        entries = self._services.composition.sequence_entries()
        await websocket.send(self._json_msg({
            "event": "sequence",
            "sequence": [entry.to_dict() for entry in entries],
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
        action_ids = data.get("action_ids", [])
        items = data.get("items", [])
        if not action_ids and not items:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "请提供 items 或 action_ids"}
            ))
            return

        additions: list[SequenceEntry] = []
        if action_ids:
            all_actions = self._services.composition.list_actions()
            action_map = {a.id: a for a in all_actions}
            for aid in action_ids:
                if aid in action_map:
                    additions.append(
                        SequenceItem.from_definition(action_map[aid])
                    )
                else:
                    await websocket.send(self._json_msg(
                        {"event": "error", "message": f"动作库中不存在 id: {aid}"}
                    ))
                    return

        if items:
            try:
                additions.extend(self._parse_sequence(items))
            except (KeyError, TypeError, ValueError) as exc:
                await websocket.send(self._json_msg(
                    {
                        "event": "error",
                        "message": f"动作解析失败: {exc}",
                    }
                ))
                return

        sequence = self._services.composition.append_sequence(
            additions,
            origin=self._composition_origin(websocket),
        )

        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [entry.to_dict() for entry in sequence],
        }))

    async def _handle_remove_from_sequence(self, websocket, data: dict) -> None:
        """
        删除序列中的某项
        请求: {"action": "remove_from_sequence", "index": 0}
        """
        index = data.get("index")
        try:
            removed = self._services.composition.remove_sequence_entry(
                index,
                origin=self._composition_origin(websocket),
            )
        except (IndexError, TypeError):
            sequence_length = len(
                self._services.composition.sequence_entries()
            )
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的索引: {index}，序列长度: {sequence_length}"}
            ))
            return

        sequence = self._services.composition.sequence_entries()
        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "removed": removed.to_dict(),
            "sequence": [entry.to_dict() for entry in sequence],
        }))

    async def _handle_move_in_sequence(self, websocket, data: dict) -> None:
        """
        移动序列项位置
        请求: {"action": "move_in_sequence", "from": 0, "to": 1}
        """
        from_idx = data.get("from")
        to_idx = data.get("to")

        if from_idx is None or to_idx is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "需要提供 from 和 to 索引"}
            ))
            return

        try:
            sequence = self._services.composition.move_sequence_entry(
                from_idx,
                to_idx,
                origin=self._composition_origin(websocket),
            )
        except (IndexError, TypeError):
            sequence_length = len(
                self._services.composition.sequence_entries()
            )
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"索引越界，序列长度: {sequence_length}"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [entry.to_dict() for entry in sequence],
        }))

    async def _handle_clear_sequence(self, websocket, data: dict) -> None:
        """清空序列"""
        self._services.composition.clear_sequence(
            origin=self._composition_origin(websocket),
        )
        await websocket.send(self._json_msg({
            "event": "sequence_updated",
            "sequence": [],
        }))

    # ==================================================================
    # 任务持久化
    # ==================================================================

    async def _handle_list_tasks(self, websocket, data: dict) -> None:
        """返回所有已保存的任务文件名"""
        summaries = await asyncio.to_thread(
            self._services.composition.list_tasks
        )
        await websocket.send(self._json_msg({
            "event": "tasks_list",
            "tasks": [summary.name for summary in summaries],
            "summaries": [
                {
                    "name": summary.name,
                    "steps": summary.step_count,
                }
                for summary in summaries
            ],
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

        try:
            stored_name = (
                await asyncio.to_thread(
                    self._services.composition.save_current_task,
                    task_name,
                    origin=self._composition_origin(websocket),
                )
            )
        except ValueError as exc:
            await websocket.send(self._json_msg(
                {"event": "error", "message": str(exc)}
            ))
            return
        steps = len(self._services.composition.sequence_entries())
        await websocket.send(self._json_msg({
            "event": "task_saved",
            "name": stored_name,
            "steps": steps,
        }))
        logger.info("任务已保存: %s", stored_name)

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

        try:
            entries = (
                await asyncio.to_thread(
                    self._services.composition.load_task_into_sequence,
                    task_name,
                    origin=self._composition_origin(websocket),
                )
            )
        except (FileNotFoundError, ValueError):
            entries = ()
        if not entries:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_loaded",
            "name": task_name,
            "sequence": [entry.to_dict() for entry in entries],
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

        try:
            deleted_name = await asyncio.to_thread(
                self._services.composition.delete_task,
                task_name,
                origin=self._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_deleted",
            "name": deleted_name,
        }))
        logger.info("任务已删除: %s", deleted_name)

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

        try:
            entries = await asyncio.to_thread(
                self._services.composition.load_task,
                task_name,
            )
        except FileNotFoundError:
            entries = ()
        if not entries:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_detail",
            "name": Path(task_name).with_suffix(".task").name,
            "sequence": [entry.to_dict() for entry in entries],
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

        try:
            old_name, stored_new_name = (
                await asyncio.to_thread(
                    self._services.composition.rename_task,
                    task_name,
                    new_name,
                    origin=self._composition_origin(websocket),
                )
            )
        except FileNotFoundError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return
        except FileExistsError as exc:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{exc.args[0]}' 已存在"}
            ))
            return
        await websocket.send(self._json_msg({
            "event": "task_renamed",
            "name": old_name,
            "new_name": stored_new_name,
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

        insert_items: list[SequenceEntry] = []
        action_ids = data.get("action_ids", [])
        if action_ids:
            all_actions = self._services.composition.list_actions()
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
            except (KeyError, TypeError, ValueError) as exc:
                await websocket.send(self._json_msg(
                    {"event": "error", "message": f"动作解析失败: {exc}"}
                ))
                return

        if not insert_items:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "请提供 items 或 action_ids"}
            ))
            return

        index = data.get("index")
        try:
            sequence = (
                await asyncio.to_thread(
                    self._services.composition.insert_task_entries,
                    task_name,
                    insert_items,
                    index=index,
                    origin=self._composition_origin(websocket),
                )
            )
        except FileNotFoundError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return
        except (IndexError, TypeError):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的插入位置: {index}"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": Path(task_name).with_suffix(".task").name,
            "sequence": [entry.to_dict() for entry in sequence],
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

        try:
            removed, sequence = (
                await asyncio.to_thread(
                    self._services.composition.remove_task_entry,
                    task_name,
                    index,
                    origin=self._composition_origin(websocket),
                )
            )
        except FileNotFoundError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return
        except (IndexError, TypeError):
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"无效的索引: {index}"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": Path(task_name).with_suffix(".task").name,
            "removed": removed.to_dict(),
            "sequence": [entry.to_dict() for entry in sequence],
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

        try:
            sequence = await asyncio.to_thread(
                self._services.composition.move_task_entry,
                task_name,
                from_idx,
                to_idx,
                origin=self._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
            ))
            return
        except (IndexError, TypeError):
            await websocket.send(self._json_msg(
                {"event": "error", "message": "from/to 索引无效或越界"}
            ))
            return

        await websocket.send(self._json_msg({
            "event": "task_updated",
            "name": Path(task_name).with_suffix(".task").name,
            "sequence": [entry.to_dict() for entry in sequence],
        }))

    # ==================================================================
    # AI 助手
    # ==================================================================

    async def _handle_ai_chat(self, websocket, data: dict) -> None:
        """
        远程文本意图入口。
        请求: {"action": "ai_chat", "text": "帮我抓一个瓶子"}
        流程: text → voice_interaction → chat / vision / command / session_control
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

        if self._interaction_controller is None:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "语音/意图交互模块未初始化，请检查 LLM 配置"}
            ))
            return

        if not await self._run_interaction_text(text):
            await websocket.send(self._json_msg(
                {"event": "error", "message": "请求未能启动，请稍后重试"}
            ))

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
        if not self._ai_preview_validated:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "AI 规划序列未通过校验，拒绝执行"}
            ))
            return

        if self._services.execution.snapshot().active:
            await websocket.send(self._json_msg(
                {"event": "error", "message": "已有序列正在执行，请先停止"}
            ))
            return

        sequence = self._ai_preview_sequence
        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        self._services.composition.replace_sequence(
            sequence,
            origin=self._composition_origin(websocket),
        )

        # 标记本次为 AI 触发执行，以便 _on_finished 发送 ai_execution_finished 事件
        self._ai_execution_pending = True
        self._execution_had_failure = False

        accepted = await self._submit_execution(
            websocket,
            sequence,
            origin="websocket-ai",
            message="AI 序列开始执行",
            steps=len(sequence),
        )
        if not accepted:
            self._ai_execution_pending = False
            return

        # 清空预览状态
        self._clear_ai_preview()

    async def _handle_ai_cancel(self, websocket, data: dict) -> None:
        """取消 AI 规划"""
        self._clear_ai_preview()
        await websocket.send(self._json_msg({
            "event": "ai_cancelled",
            "message": "AI 规划已取消",
        }))

    async def _handle_ai_status(self, websocket, data: dict) -> None:
        """查询 AI/LLM 状态"""
        planner_client = self._get_planner_client()
        chat_client = self._get_chat_client()
        planner_available = planner_client is not None and planner_client.is_available()
        chat_available = chat_client is not None and chat_client.is_available()
        llm_available = planner_available
        model_name = planner_client.get_model_name() if planner_client else "未配置"
        capabilities = (
            [cap.value for cap in chat_client.capabilities()]
            if chat_client else []
        )

        try:
            config = Config.get_instance()
            provider = (
                self._llm_registry.default_provider.upper()
                if self._llm_registry
                else config.LLM_DEFAULT_PROVIDER.upper()
            )
            api_key_set = Config.is_api_key_set()
        except Exception:
            provider = "未知"
            api_key_set = False

        await websocket.send(self._json_msg({
            "event": "ai_status",
            "llm_available": llm_available,
            "api_key_set": api_key_set,
            "model": model_name,
            "provider": provider,
            "default_provider": self._llm_registry.default_provider if self._llm_registry else "未配置",
            "providers": list(self._llm_registry.provider_names) if self._llm_registry else [],
            "loaded_providers": list(self._llm_registry.loaded_provider_names) if self._llm_registry else [],
            "capabilities": capabilities,
            "chat_available": chat_available,
            "chat_provider": chat_client.get_provider_name() if chat_client else "未配置",
            "chat_model": chat_client.get_model_name() if chat_client else "未配置",
            "planner_available": planner_available,
            "planner_provider": planner_client.get_provider_name() if planner_client else "未配置",
            "planner_model": planner_client.get_model_name() if planner_client else "未配置",
            "processing": self._ai_processing,
            "has_preview": bool(
                self._ai_preview_sequence and self._ai_preview_validated
            ),
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

    async def _run_interaction_text(
        self,
        text: str,
        *,
        emit_minicpm_instruction: bool = False,
    ) -> bool:
        """通过 voice_interaction 处理远程文本输入。"""
        if self._ai_processing:
            return False
        if self._interaction_controller is None:
            return False

        self._clear_ai_preview()
        self._ai_processing = True
        await self._broadcast({"event": "ai_status_changed", "status": "分析中..."})
        if emit_minicpm_instruction:
            await self._broadcast({"event": "minicpm_instruction", "instruction": text})

        try:
            async for event in self._interaction_controller.handle_text(
                text,
                require_awake=False,
            ):
                await self._emit_interaction_event(event.to_dict())
        except Exception as exc:
            logger.error("远程文本意图处理失败: %s", exc, exc_info=True)
            await self._broadcast({
                "event": "error",
                "message": f"远程文本意图处理失败: {exc}",
            })
        finally:
            self._ai_processing = False
        return True

    async def _emit_interaction_event(self, event: Dict[str, Any]) -> None:
        """将 voice_interaction 统一事件映射为 WebSocket 协议事件。"""
        event_type = event.get("type", "")
        text = event.get("text") or ""
        data = event.get("data") or {}
        intent = event.get("intent")
        interaction_data = dict(data)
        if event_type in ("text_delta", "audio_delta", "done"):
            interaction_data.pop("raw", None)

        if event_type != "command_preview":
            await self._broadcast({
                "event": "interaction_event",
                "type": event_type,
                "text": text,
                "text_delta": event.get("text_delta") or "",
                "intent": intent,
                "data": interaction_data,
            })

        if event_type == "intent":
            intent_name = (intent or {}).get("intent", "unknown")
            await self._broadcast({
                "event": "ai_intent",
                "intent": intent,
                "input": data.get("input"),
            })
            if intent_name == "command":
                await self._broadcast({"event": "ai_status_changed", "status": "规划中..."})
            elif intent_name == "vision_question":
                await self._broadcast({"event": "ai_status_changed", "status": "观察中..."})
            else:
                await self._broadcast({"event": "ai_status_changed", "status": "回复中..."})
            return

        if event_type == "command_preview":
            self._clear_ai_preview()
            sequence_dicts = data.get("sequence") or []
            try:
                sequence = [
                    SequenceItem.from_dict(item)
                    for item in sequence_dicts
                    if isinstance(item, dict)
                ]
            except Exception as exc:
                logger.error("命令预览序列解析失败: %s", exc, exc_info=True)
                await self._broadcast({
                    "event": "error",
                    "message": f"命令预览序列解析失败: {exc}",
                })
                return

            validation = data.get("validation") or {}
            validation_passed = (
                validation.get("is_valid") is True
                and validation.get("code") == "valid"
            )
            confirmation_required = data.get("requires_confirmation") is True
            if not sequence or not validation_passed or not confirmation_required:
                await self._broadcast({
                    "event": "error",
                    "message": "动作预览未经校验或缺少显式确认要求，已拒绝",
                })
                await self._broadcast({
                    "event": "ai_status_changed",
                    "status": "预览无效",
                })
                return

            plan = data.get("plan") or {}
            skill_info = data.get("skill_info") or {}
            self._ai_preview_sequence = sequence
            self._ai_preview_skill_info = skill_info
            self._ai_preview_validated = True
            await self._broadcast({
                "event": "interaction_event",
                "type": event_type,
                "text": text,
                "text_delta": event.get("text_delta") or "",
                "intent": intent,
                "data": interaction_data,
            })

            if plan:
                await self._broadcast({
                    "event": "ai_skill_matched",
                    "skill_id": plan.get("skill_id"),
                    "skill_name": plan.get("skill_name"),
                    "confidence": plan.get("confidence"),
                    "params": plan.get("parameters") or {},
                    "reasoning": plan.get("reasoning") or "",
                })

            await self._broadcast({
                "event": "ai_preview_ready",
                "sequence": [item.to_dict() for item in sequence],
                "skill_info": skill_info,
                "plan": plan,
                "validation": validation,
                "requires_confirmation": True,
                "message": text,
            })
            await self._broadcast({"event": "ai_status_changed", "status": "预览就绪"})
            logger.info("远程文本生成动作预览: %d 个动作", len(sequence))
            return

        if event_type == "text_delta":
            await self._broadcast({
                "event": "chat_data",
                "type": "chunk",
                "text_delta": event.get("text_delta") or "",
                "source": "voice_interaction",
                "packet": data.get("raw"),
            })
            return

        if event_type == "audio_delta":
            await self._broadcast({
                "event": "chat_data",
                "type": "chunk",
                "audio_data": event.get("audio_data"),
                "source": "voice_interaction",
                "packet": data.get("raw"),
            })
            return

        if event_type == "done":
            await self._broadcast({
                "event": "chat_data",
                "type": "done",
                "text": text,
                "audio_data": event.get("audio_data"),
                "source": "voice_interaction",
                "metrics": data.get("metrics"),
                "packet": data.get("raw"),
            })
            if not self._ai_preview_sequence:
                await self._broadcast({"event": "ai_status_changed", "status": "完成"})
            return

        if event_type == "error":
            await self._broadcast({
                "event": "error",
                "message": text or "语音/意图交互处理失败",
            })
            await self._broadcast({"event": "ai_status_changed", "status": "失败"})
            return

        if event_type == "ignored":
            await self._broadcast({
                "event": "ai_ignored",
                "message": text or "已忽略本次输入",
                "intent": intent,
            })
            await self._broadcast({"event": "ai_status_changed", "status": "已忽略"})

    async def _cancel_current_ai_task(self) -> None:
        """供 voice_interaction 的 session_control.cancel_task 调用。"""
        self._clear_ai_preview()
        if self._services.execution.snapshot().active:
            self._services.execution.cancel()

    def _clear_ai_preview(self) -> None:
        """Discard the pending AI preview and its validation state."""
        self._ai_preview_sequence = []
        self._ai_preview_skill_info = {}
        self._ai_preview_validated = False

    # ==================================================================
    # 设备管理
    # ==================================================================

    async def _handle_status(self, websocket, data: dict) -> None:
        """查询设备和执行状态"""
        execution = self._services.execution.snapshot()
        devices = self._services.devices.status()
        camera = self._camera_manager
        await websocket.send(self._json_msg({
            "event": "status",
            "devices": devices,
            "executor": {
                "run_id": execution.run_id,
                "state": execution.state.value,
                "running": execution.active,
                "paused": execution.state is ExecutionState.PAUSED,
                "error": execution.error,
                "error_code": execution.error_code,
                "error_operation": execution.error_operation,
                "error_device_id": execution.error_device_id,
            },
            "sequence_length": len(
                self._services.composition.sequence_entries()
            ),
            "ai_processing": self._ai_processing,
            "camera": {
                "available": camera is not None and camera.camera_count > 0,
                "camera_count": camera.camera_count if camera else 0,
                "cameras": camera.get_cameras_info() if camera else [],
            },
            "minicpm": {
                "configured": self._minicpm_cfg is not None,
                "gateway": (
                    f"{self._minicpm_cfg.ws_scheme}://"
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
        await websocket.send(self._json_msg(
            {"event": "log", "level": "info", "message": "开始初始化机械臂..."}
        ))
        try:
            await asyncio.to_thread(
                self._services.devices.initialize,
                ROBOT_SYSTEM,
            )
            await self._broadcast({
                "event": "device_status_changed",
                "devices": self._services.devices.status(),
            })
        except Exception as exc:
            await websocket.send(self._json_msg(
                {"event": "error", "message": f"机械臂初始化异常: {exc}"}
            ))

    async def _handle_init_body(self, websocket, data: dict) -> None:
        """
        初始化身体（升降平台）
        请求: {"action": "init_body"}
        """
        try:
            await asyncio.to_thread(
                self._services.devices.initialize,
                BODY_AXIS,
            )

            await websocket.send(self._json_msg({
                "event": "log",
                "level": "info",
                "message": "身体控制器初始化成功",
            }))
            await websocket.send(self._json_msg({
                "event": "device_status_changed",
                "devices": self._services.devices.status(),
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
        results = await asyncio.to_thread(
            self._services.devices.shutdown_all,
        )
        await websocket.send(self._json_msg({
            "event": "disconnected",
            "results": results,
            "devices": self._services.devices.status(),
        }))

    async def _handle_test_camera(self, websocket, data: dict) -> None:
        """
        通过 DeviceRuntime 测试相机（与视觉抓取使用同一实例）。
        请求: {"action": "test_camera"}
        """
        def _do_test():
            session = None
            try:
                import time

                config = Config.get_instance()
                camera_name = config.VISION_CAMERA_NAME or None

                session = self._services.camera_access.open(
                    "websocket-test"
                )
                mgr = session.camera

                # 等待至少一路相机上线
                deadline = time.time() + 10
                online = []
                while time.time() < deadline:
                    info = mgr.get_cameras_info()
                    online = [c for c in info if c.get("online")]
                    if online:
                        break
                    time.sleep(0.3)
                else:
                    all_info = mgr.get_cameras_info()
                    errors = []
                    for c in all_info:
                        if not c.get("online"):
                            errors.append(f"{c.get('name', '?')}: {c.get('error', '未知')}")
                    if errors:
                        self._broadcast_threadsafe({
                            "event": "camera_test_result",
                            "success": False,
                            "message": f"相机启动失败: {'; '.join(errors)}",
                        })
                    else:
                        self._broadcast_threadsafe({
                            "event": "camera_test_result",
                            "success": False,
                            "message": "未检测到在线相机",
                        })
                    return

                # 尝试取帧
                deadline = time.time() + 10
                while time.time() < deadline:
                    if hasattr(mgr, "get_latest_raw_frames"):
                        raw = mgr.get_latest_raw_frames(camera_name)
                        if raw is not None:
                            color, depth, intr = raw
                            if color is not None and depth is not None:
                                h, w = color.shape[:2]
                                center_dist = float(depth[h // 2, w // 2])
                                actual_name = camera_name or online[0]["name"]
                                sn = ""
                                for c in online:
                                    if c["name"] == actual_name:
                                        sn = f" SN={c['serial']}"
                                        break
                                msg = (f"相机测试成功: color={w}x{h}  "
                                       f"depth(center)={center_dist / 1000:.3f}m  "
                                       f"(camera={actual_name}{sn})")
                                self._broadcast_threadsafe({
                                    "event": "camera_test_result",
                                    "success": True,
                                    "message": msg,
                                })
                                return
                    else:
                        jpegs = mgr.get_latest_jpegs()
                        if jpegs:
                            if camera_name:
                                matched = [(n, len(b)) for s, n, b in jpegs if n == camera_name]
                                if matched:
                                    self._broadcast_threadsafe({
                                        "event": "camera_test_result",
                                        "success": True,
                                        "message": f"本地摄像头测试成功: camera={matched[0][0]}",
                                    })
                                    return
                            else:
                                name = jpegs[0][1]
                                self._broadcast_threadsafe({
                                    "event": "camera_test_result",
                                    "success": True,
                                    "message": f"本地摄像头测试成功: camera={name}",
                                })
                                return
                    time.sleep(0.2)

                self._broadcast_threadsafe({
                    "event": "camera_test_result",
                    "success": False,
                    "message": "取帧超时（10 秒内未获得有效帧）",
                })

            except Exception as e:
                self._broadcast_threadsafe({
                    "event": "camera_test_result",
                    "success": False,
                    "message": f"测试异常: {str(e)}",
                })
            finally:
                if session is not None:
                    session.close()

        threading.Thread(target=_do_test, daemon=True, name="TestCamera").start()
        await websocket.send(self._json_msg({"event": "log", "level": "info", "message": "正在测试相机..."}))

    # ==================================================================
    # MiniCPM / LLM 聊天配置
    # ==================================================================

    def _init_minicpm_config(self) -> None:
        """从 Config 加载 MiniCPM 相关配置。"""
        try:
            cfg_dict = Config.get_minicpm_config()
            self._minicpm_cfg = MiniCPMChatConfig(**cfg_dict)
            logger.info(
                "MiniCPM 配置已加载: %s://%s%s%s",
                self._minicpm_cfg.ws_scheme,
                self._minicpm_cfg.gateway_host,
                self._minicpm_cfg._port_suffix,
                self._minicpm_cfg.gateway_path_prefix,
            )
        except Exception as exc:
            logger.warning("MiniCPM 配置加载失败: %s", exc)
            self._minicpm_cfg = None

    async def _handle_minicpm_status(self, websocket, data: dict) -> None:
        """
        查询 MiniCPM 网关配置与聊天状态
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
            "gateway": f"{cfg.ws_scheme}://{cfg.gateway_host}{cfg._port_suffix}{cfg.gateway_path_prefix}",
            "realtime_path": cfg.realtime_path,
            "ask_enabled": cfg.ask_enabled,
            "chat_action": "chat_connect / chat / chat_disconnect",
        }))

    # ==================================================================
    # 相机管理器
    # ==================================================================

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
        if self._camera_preview_session is None:
            try:
                self._camera_preview_session = (
                    self._services.camera_access.open(
                        "websocket-preview"
                    )
                )
            except Exception as exc:
                await websocket.send(self._json_msg({
                    "event": "camera_error",
                    "message": f"相机资源不可用: {exc}",
                    "cameras": [],
                }))
                return

        camera = self._camera_preview_session.camera
        if not camera.camera_count:
            await websocket.send(self._json_msg({
                "event": "camera_error",
                "message": "所有配置相机均不可用",
                "cameras": camera.get_cameras_info(),
            }))
            self._camera_preview_session.close()
            self._camera_preview_session = None
            return

        self._camera_frame_subs.add(websocket)
        await websocket.send(self._json_msg({"event": "camera_subscribed"}))

        if self._camera_push_task is None or self._camera_push_task.done():
            self._camera_push_task = self._schedule_background_task(
                self._camera_push_loop(),
                name="WebSocketCameraPush",
            )
        logger.info("客户端订阅相机帧: %s", websocket.remote_address)

    async def _handle_unsubscribe_camera_frames(self, websocket, data: dict) -> None:
        """取消相机帧订阅。
        请求: {"action": "unsubscribe_camera_frames"}
        """
        self._camera_frame_subs.discard(websocket)
        await websocket.send(self._json_msg({"event": "camera_unsubscribed"}))
        if not self._camera_frame_subs:
            self._stop_camera_if_idle()

    async def _camera_push_loop(self) -> None:
        """后台任务：以 30fps 向所有订阅客户端推送相机帧。"""
        interval = 1.0 / 30
        try:
            while self._camera_frame_subs:
                session = self._camera_preview_session
                if session is not None and session.active:
                    jpegs = session.camera.get_latest_jpegs()
                    if jpegs:
                        payload = {
                            "event": "camera_frames",
                            "frames": [
                                {
                                    "serial": serial,
                                    "name": name,
                                    "index": idx,
                                    "data": base64.b64encode(jpeg).decode(
                                        "ascii"
                                    ),
                                }
                                for idx, (serial, name, jpeg) in enumerate(
                                    jpegs
                                )
                            ],
                        }
                        await self._send_to_subscribers(
                            payload,
                            self._camera_frame_subs,
                        )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("相机预览推送失败: %s", exc)
        finally:
            self._camera_frame_subs.clear()
            self._camera_push_task = None
            self._stop_camera_if_idle()

    def _stop_camera_if_idle(self) -> None:
        """Release the preview lease after the last subscriber leaves."""
        if self._camera_frame_subs:
            return
        session = self._camera_preview_session
        self._camera_preview_session = None
        if session is not None:
            session.close()

    # ==================================================================
    # LLM 聊天（dispatch 模式）
    # ==================================================================

    async def _handle_chat_connect(self, websocket, data: dict) -> None:
        """标记聊天会话激活（不预先连接网关）。
        请求: {"action": "chat_connect"}
        成功: {"event": "chat_connected"}
        """
        provider = data.get("provider")
        chat_client = None
        if self._llm_registry is not None:
            try:
                chat_client = self._llm_registry.get_chat_client(provider)
            except Exception as exc:
                await websocket.send(self._json_msg({
                    "event": "error", "message": f"LLM provider 选择失败: {exc}"
                }))
                return
        else:
            chat_client = self._llm_client

        if chat_client is None or not chat_client.is_available():
            await websocket.send(self._json_msg({
                "event": "error", "message": "LLM 聊天模型不可用，请检查模型配置"
            }))
            return
        if LLMCapability.STREAM_CHAT not in chat_client.capabilities():
            await websocket.send(self._json_msg({
                "event": "error", "message": "当前 LLM 不支持流式聊天"
            }))
            return
        if id(websocket) in self._minicpm_sessions:
            await websocket.send(self._json_msg({
                "event": "error", "message": "聊天会话已连接，请先断开"
            }))
            return

        self._minicpm_sessions[id(websocket)] = {
            "active": True,
            "provider": provider,
        }
        await websocket.send(self._json_msg({
            "event": "chat_connected",
            "provider": chat_client.get_provider_name(),
            "model": chat_client.get_model_name(),
        }))
        logger.info("LLM 聊天会话已就绪: %s", websocket.remote_address)

    async def _handle_chat_disconnect(self, websocket, data: dict) -> None:
        """断开 LLM 聊天会话。
        请求: {"action": "chat_disconnect"}
        """
        self._minicpm_sessions.pop(id(websocket), None)
        await websocket.send(self._json_msg({"event": "chat_disconnected"}))

    async def _handle_chat_send(self, websocket, data: dict) -> None:
        """发送聊天消息。
        请求: {"action": "chat", "messages": [...], "streaming": true, ...}
        服务端持续推送规范化的 chat_data 事件。上游模型连接由 LLM provider 维护。
        """
        session = self._minicpm_sessions.get(id(websocket))
        if session is None:
            await websocket.send(self._json_msg({
                "event": "error", "message": "请先发送 chat_connect 建立聊天会话"
            }))
            return

        provider = data.get("provider") or session.get("provider")
        chat_client = None
        if self._llm_registry is not None:
            try:
                chat_client = self._llm_registry.get_chat_client(provider)
            except Exception as exc:
                await websocket.send(self._json_msg({
                    "event": "error", "message": f"LLM provider 选择失败: {exc}"
                }))
                return
        else:
            chat_client = self._llm_client

        if chat_client is None or not chat_client.is_available():
            await websocket.send(self._json_msg({
                "event": "error", "message": "LLM 聊天模型不可用，请检查模型配置"
            }))
            return
        if LLMCapability.STREAM_CHAT not in chat_client.capabilities():
            await websocket.send(self._json_msg({
                "event": "error", "message": "当前 LLM 不支持流式聊天"
            }))
            return

        payload = {k: v for k, v in data.items() if k != "action"}
        try:
            messages = self._parse_llm_messages(payload)
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "error", "message": f"聊天消息解析失败: {exc}"
            }))
            return

        if not messages:
            await websocket.send(self._json_msg({
                "event": "error", "message": "messages 不能为空"
            }))
            return

        # chat 默认只做纯 LLM 聊天；需要远程控制当前机器人时，使用 ai_chat，
        # 或显式传 route_to_interaction / robot_interaction 复用同一段用户文本。
        try:
            user_text = _extract_user_text(payload)
            if user_text and (payload.get("route_to_interaction") or payload.get("robot_interaction")):
                self._schedule_background_task(
                    self._on_chat_user_text(user_text),
                    name="WebSocketChatRouting",
                )
        except Exception:
            pass

        options = self._extract_llm_options(payload)
        try:
            async for event in chat_client.stream_chat(messages, **options):
                await websocket.send(
                    self._json_msg(self._llm_stream_event_to_chat_data(event))
                )
                if event.type == "error":
                    break
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "error", "message": f"LLM 聊天失败: {exc}"
            }))

    @staticmethod
    def _parse_llm_messages(payload: dict) -> List[LLMMessage]:
        """将前端 chat payload 转换为统一 LLMMessage。"""
        raw_messages = payload.get("messages")
        if raw_messages is None and payload.get("role"):
            raw_messages = [{
                "role": payload.get("role"),
                "content": payload.get("content", ""),
            }]

        if not isinstance(raw_messages, list):
            return []

        messages: List[LLMMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            role = raw.get("role", "user")
            if role not in ("system", "user", "assistant"):
                role = "user"
            content = raw.get("content", "")
            messages.append(LLMMessage(role=role, content=RobotWebSocketServer._parse_llm_content(content)))
        return messages

    @staticmethod
    def _parse_llm_content(content) -> str | List[LLMContentPart]:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)

        parts: List[LLMContentPart] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type", "text")
            if part_type == "text":
                parts.append(LLMContentPart(type="text", text=part.get("text", "")))
            elif part_type == "image":
                parts.append(LLMContentPart(
                    type="image",
                    data=part.get("data") or part.get("image") or part.get("url"),
                    mime_type=part.get("mime_type"),
                ))
            elif part_type == "audio":
                parts.append(LLMContentPart(
                    type="audio",
                    data=part.get("data") or part.get("audio"),
                    mime_type=part.get("mime_type"),
                ))
        return parts

    @staticmethod
    def _extract_llm_options(payload: dict) -> dict:
        options = {
            "streaming": payload.get("streaming", True),
        }
        for src, dest in (
            ("temperature", "temperature"),
            ("max_tokens", "max_tokens"),
            ("max_new_tokens", "max_new_tokens"),
            ("length_penalty", "length_penalty"),
            ("image_max_slice_nums", "image_max_slice_nums"),
            ("omni_mode", "omni_mode"),
            ("tts_enabled", "tts_enabled"),
            ("tts", "tts"),
            ("use_tts_template", "use_tts_template"),
            ("enable_thinking", "enable_thinking"),
        ):
            if src in payload:
                options[dest] = payload[src]
        return options

    @staticmethod
    def _llm_stream_event_to_chat_data(event: LLMStreamEvent) -> dict:
        base_event = {"event": "chat_data", "packet": event.raw}
        if event.type == "session_started":
            return {**base_event, "type": "session_started"}
        if event.type == "text_delta":
            return {
                **base_event,
                "type": "chunk",
                "text_delta": event.text_delta,
            }
        if event.type == "audio_delta":
            return {
                **base_event,
                "type": "chunk",
                "audio_data": event.audio_data,
            }
        if event.type == "done":
            return {
                **base_event,
                "type": "done",
                "text": event.text,
                "audio_data": event.audio_data,
                "metrics": event.metrics,
            }
        if event.type == "error":
            return {
                "event": "error",
                "message": event.error or "LLM 聊天失败",
                "packet": event.raw,
            }
        return {
            **base_event,
            "type": event.type,
            "text": event.text,
            "metrics": event.metrics,
        }

    async def _close_minicpm_session(self, websocket) -> None:
        """清理指定客户端的 LLM 聊天会话标记。"""
        self._minicpm_sessions.pop(id(websocket), None)

    async def _on_chat_user_text(self, text: str) -> None:
        """把聊天消息显式路由到 voice_interaction。"""
        if not text.strip():
            return
        logger.info("聊天消息显式路由到 voice_interaction: %s", text[:80])
        if not await self._run_interaction_text(text, emit_minicpm_instruction=True):
            logger.debug("voice_interaction 未启动（处理中或组件不可用），输入: %s", text[:80])

    # ==================================================================
    # 序列解析
    # ==================================================================

    def _parse_sequence(self, raw: list) -> list:
        """
        将前端传来的 JSON 数组转换为 SequenceEntry 列表（含 LoopBlock）

        支持三种格式:
        1. 循环块:  {"kind": "loop", "uuid": "...", "items": [...], "repeat_count": N}
        2. 完整格式: {"uuid": "...", "definition": {...}, "status": "PENDING"}
        3. 简化格式: {"name": "...", "type": "MOVE_TO_POINT", "parameters": {...}}
        """
        sequence = []
        for item_data in raw:
            if item_data.get("kind") == "loop":
                loop = LoopBlock.from_dict(item_data)
                for child in loop.items:
                    child.status = SequenceItemStatus.PENDING
                sequence.append(loop)
            elif "definition" in item_data:
                seq_item = SequenceItem.from_dict(item_data)
                seq_item.status = SequenceItemStatus.PENDING
                sequence.append(seq_item)
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

    def _on_execution_event(self, event: ExecutionEvent) -> None:
        """Translate execution-domain events to the WebSocket protocol."""
        event_type = event.event_type
        metadata = self._execution_metadata(event)
        if event_type is ExecutionEventType.STEP_STARTED:
            self._on_step_started(
                event.index or 0,
                event.item,
                event.data,
                metadata,
            )
        elif event_type is ExecutionEventType.STEP_COMPLETED:
            self._on_step_completed(
                event.index or 0,
                event.item,
                metadata,
            )
        elif event_type is ExecutionEventType.STEP_FAILED:
            self._on_step_failed(
                event.index or 0,
                event.item,
                event.message,
                event.data,
                metadata,
            )
        elif event_type is ExecutionEventType.LOOP_PROGRESS:
            self._on_loop_progress(
                str(event.data["loop_uuid"]),
                int(event.data["current_iteration"]),
                int(event.data["total_iterations"]),
                metadata,
            )
        elif event_type is ExecutionEventType.LOG:
            self._on_log(event.message, event.level, metadata)
        elif event_type is ExecutionEventType.FAILED:
            self._execution_had_failure = True
            if event.message:
                self._on_log(event.message, "error", metadata)
            self._on_finished(event, metadata)
        elif event_type is ExecutionEventType.CANCELLED:
            self._execution_had_failure = True
            self._on_finished(event, metadata)
        elif event_type is ExecutionEventType.SUCCEEDED:
            self._on_finished(event, metadata)

    def _execution_metadata(
        self,
        event: ExecutionEvent,
    ) -> dict[str, Any]:
        with self._execution_requests_lock:
            correlation = self._execution_requests.get(event.run_id)
        metadata: dict[str, Any] = {
            "run_id": event.run_id,
            "origin": event.origin,
        }
        if correlation is not None:
            metadata.update({
                "request_id": correlation.request_id,
                "action": correlation.action,
            })
        return metadata

    def _on_step_started(
        self,
        index: int,
        item: SequenceItem,
        control_policy: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self._broadcast_threadsafe({
            "event": "step_started",
            "index": index,
            "name": item.definition.name,
            "status": item.status.value,
            "control_policy": control_policy,
            **metadata,
        })

    def _on_step_completed(
        self,
        index: int,
        item: SequenceItem,
        metadata: dict[str, Any],
    ) -> None:
        self._broadcast_threadsafe({
            "event": "step_completed",
            "index": index,
            "name": item.definition.name,
            **metadata,
        })

    def _on_step_failed(
        self,
        index: int,
        item: SequenceItem,
        error: str,
        failure: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self._execution_had_failure = True
        self._broadcast_threadsafe({
            "event": "step_failed",
            "index": index,
            "name": item.definition.name,
            "error": error,
            "failure": failure,
            **metadata,
        })

    def _on_loop_progress(
        self,
        loop_uuid: str,
        current_iteration: int,
        total_iterations: int,
        metadata: dict[str, Any],
    ) -> None:
        self._broadcast_threadsafe({
            "event": "loop_progress",
            "loop_uuid": loop_uuid,
            "current_iteration": current_iteration,
            "total_iterations": total_iterations,
            **metadata,
        })

    def _on_log(
        self,
        message: str,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        log_fn = {"warn": logger.warning, "error": logger.error}.get(level, logger.info)
        log_fn(message)
        self._broadcast_threadsafe({
            "event": "log",
            "level": level,
            "message": message,
            **(metadata or {}),
        })

    def _on_finished(
        self,
        event: ExecutionEvent,
        metadata: dict[str, Any],
    ) -> None:
        succeeded = event.event_type is ExecutionEventType.SUCCEEDED
        if self._ai_execution_pending:
            self._ai_execution_pending = False
            success = not self._execution_had_failure
            self._broadcast_threadsafe({
                "event": "ai_execution_finished",
                "success": success,
                "message": "AI 序列执行完成" if success else "AI 序列执行失败",
                **metadata,
            })
        self._broadcast_threadsafe({
            "event": "execution_finished",
            "state": event.event_type.value,
            "success": succeeded,
            "error": event.message or None,
            "failure": {
                "code": event.data.get("code") or None,
                "operation": event.data.get("operation") or None,
                "device_id": event.data.get("device_id") or None,
            },
            **metadata,
        })
        with self._execution_requests_lock:
            correlation = self._execution_requests.pop(
                event.run_id,
                None,
            )
        if correlation is not None:
            self._audit(
                client_id=correlation.client_id,
                principal=correlation.principal,
                action=correlation.action,
                request_id=correlation.request_id,
                run_id=event.run_id,
                outcome=event.event_type.value,
                code=event.data.get("code") or None,
            )

    # ==================================================================
    # 广播工具
    # ==================================================================

    def _broadcast_threadsafe(self, data: dict) -> None:
        """从任意线程安全地广播消息到所有客户端"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def schedule() -> None:
            self._schedule_background_task(
                self._broadcast(data),
                name="WebSocketBroadcast",
            )

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            logger.debug("WebSocket loop closed before broadcast scheduling")

    async def _broadcast(self, data: dict) -> None:
        """广播消息到所有已连接的客户端"""
        disconnected = await self._deliver(
            data,
            tuple(self._clients),
        )
        for client in disconnected:
            await self._unregister_client(
                client,
                reason="send_failed",
            )

    async def _send_to_subscribers(
        self,
        data: dict[str, Any],
        subscribers: set[Any],
    ) -> None:
        """Send an event only to an explicit subscription set."""
        disconnected = await self._deliver(
            data,
            tuple(subscribers),
        )
        for client in disconnected:
            await self._unregister_client(
                client,
                reason="send_failed",
            )

    async def _deliver(
        self,
        data: dict[str, Any],
        recipients: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        if not recipients:
            return ()
        message = self._json_msg(data)

        async def send(client: Any) -> Any | None:
            try:
                await client.send(message)
                return None
            except Exception as exc:
                logger.warning(
                    "WebSocket 事件发送失败: client_id=%s error=%s",
                    self._client_ids.get(client, "unknown"),
                    type(exc).__name__,
                )
                return client

        results = await asyncio.gather(
            *(send(client) for client in recipients),
        )
        return tuple(
            client
            for client in results
            if client is not None
        )

    # ==================================================================
    # 遥操作控制
    # ==================================================================

    async def _handle_teleop_init(self, websocket, data: dict) -> None:
        """
        遥操作初始化：移动机械臂到指定关节姿态
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_init", "arm": "左", "joints": [j1,j2,j3,j4,j5,j6]}
        - 双臂: {"action": "teleop_init", "joints": {"左": [j1,j2,j3,j4,j5,j6], "右": [j1,j2,j3,j4,j5,j6]}}
        响应: {"event": "teleop_init_completed", "arm": "左", "message": "初始化完成"}
        """
        arm = data.get("arm")
        joints_data = data.get("joints")

        # 检查机械臂是否已连接
        if self._robot_system is None:
            await websocket.send(self._json_msg({
                "event": "error",
                "message": "机械臂控制器未初始化"
            }))
            return

        # 判断单臂还是双臂
        if arm:
            # 单臂模式：joints是列表
            if not isinstance(joints_data, list):
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": "单臂模式需要joints为列表"
                }))
                return

            joints = joints_data
            if len(joints) != 6:
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": f"关节角度数量错误：需要6个，实际{len(joints)}个"
                }))
                return

            logger.info("遥操作初始化: %s臂移动到 %s", arm, joints)

            try:
                success = self._services.manual_control.initialize_teleoperation(
                    arm,
                    joints,
                )
                if success:
                    logger.info("遥操作初始化完成: %s臂", arm)
                    await websocket.send(self._json_msg({
                        "event": "teleop_init_completed",
                        "arm": arm,
                        "message": "初始化完成"
                    }))
                else:
                    await websocket.send(self._json_msg({
                        "event": "error",
                        "message": "初始化移动失败"
                    }))
            except Exception as e:
                logger.error("遥操作初始化异常: %s", str(e))
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": f"初始化异常: {str(e)}"
                }))

        else:
            # 双臂模式：joints是字典
            if not isinstance(joints_data, dict):
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": "双臂模式需要joints为字典"
                }))
                return

            # 验证每个臂的关节角度
            for arm_name, joints in joints_data.items():
                if arm_name not in ["左", "右"]:
                    await websocket.send(self._json_msg({
                        "event": "error",
                        "message": f"未知的臂名称: {arm_name}"
                    }))
                    return

                if len(joints) != 6:
                    await websocket.send(self._json_msg({
                        "event": "error",
                        "message": f"{arm_name}臂关节角度数量错误：需要6个，实际{len(joints)}个"
                    }))
                    return

            logger.info("双臂遥操作初始化: 左=%s, 右=%s",
                       joints_data.get("左"), joints_data.get("右"))

            # 并行执行双臂初始化
            success_results = {}
            try:
                for arm_name, joints in joints_data.items():
                    success = (
                        self._services.manual_control.initialize_teleoperation(
                            arm_name,
                            joints,
                        )
                    )
                    success_results[arm_name] = success

                if all(success_results.values()):
                    logger.info("双臂遥操作初始化完成")
                    await websocket.send(self._json_msg({
                        "event": "teleop_init_completed_dual",
                        "message": "双臂初始化完成"
                    }))
                else:
                    failed_arms = [arm for arm, success in success_results.items() if not success]
                    await websocket.send(self._json_msg({
                        "event": "error",
                        "message": f"部分臂初始化失败: {failed_arms}"
                    }))
            except Exception as e:
                logger.error("双臂遥操作初始化异常: %s", str(e))
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": f"初始化异常: {str(e)}"
                }))

    async def _handle_teleop_start(self, websocket, data: dict) -> None:
        """
        启动遥操作模式
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_start", "arm": "左"}
        - 多臂: {"action": "teleop_start", "arms": ["左", "右"]}
        - 双臂: {"action": "teleop_start"} (无参数，默认启动所有臂)
        响应: {"event": "teleop_started", "arms": ["左"], "message": "遥操作模式已启动"}
        """
        arm = data.get("arm")
        arms_list = data.get("arms")

        # 检查是否正在执行其他任务
        if self._services.execution.snapshot().active:
            await websocket.send(self._json_msg({
                "event": "error",
                "message": "有任务正在执行，无法启动遥操作"
            }))
            return

        # 检查机械臂是否已连接
        if self._robot_system is None:
            await websocket.send(self._json_msg({
                "event": "error",
                "message": "机械臂控制器未初始化"
            }))
            return

        # 确定要启动的臂
        if arm:
            # 单臂模式
            arms_to_start = [arm]
        elif arms_list:
            # 多臂模式
            arms_to_start = arms_list
        else:
            # 默认启动所有臂（双臂）
            arms_to_start = ["左", "右"]

        # 验证臂名称
        for arm_name in arms_to_start:
            if arm_name not in ["左", "右"]:
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": f"未知的臂名称: {arm_name}"
                }))
                return

        try:
            self._services.teleoperation.start()
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "error",
                "message": f"遥操作资源申请失败: {exc}",
            }))
            return

        # 启动指定臂的遥操作模式
        for arm_name in arms_to_start:
            self._teleop_modes[arm_name] = True
            self._teleop_msg_counts[arm_name] = 0

        logger.info("遥操作模式已启动: %s", arms_to_start)
        await websocket.send(self._json_msg({
            "event": "teleop_started",
            "arms": arms_to_start,
            "message": "遥操作模式已启动"
        }))

    async def _execute_grip_async(self, arm: str, position: int) -> None:
        """在线程池中异步执行夹爪位置指令，不阻塞关节指令流"""
        if self._robot_system is None:
            return
        position = max(0, min(1000, int(position)))
        try:
            await asyncio.to_thread(
                self._services.teleoperation.set_gripper,
                arm,
                position,
            )
            logger.info("遥操作夹爪位置: %s臂 %d", arm, position)
        except Exception as e:
            logger.error("遥操作夹爪执行异常: arm=%s, error=%s", arm, str(e))

    async def _handle_teleop_joint(self, websocket, data: dict) -> None:
        """
        处理遥操作关节指令（50Hz）
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_joint", "arm": "左", "joints": [j1,j2,j3,j4,j5,j6], "follow": true}
        - 双臂: {"action": "teleop_joint", "joints": {"左": [j1,j2,j3,j4,j5,j6], "右": [j1,j2,j3,j4,j5,j6]}, "follow": false}
        响应: 仅在执行失败时返回 {"event": "teleop_error", "message": "..."}
        """
        arm = data.get("arm")
        joints_data = data.get("joints")
        follow = data.get("follow", False)  # 默认False（平滑模式）
        trajectory_mode = data.get("trajectory_mode", 0)
        grip = data.get("grip")  # 夹爪位置原始值（0=闭合，1000=完全张开），仅在值变化时执行

        # 判断单臂还是双臂
        if arm:
            # 单臂模式
            if not isinstance(joints_data, list):
                await websocket.send(self._json_msg({
                    "event": "teleop_error",
                    "message": "单臂模式需要joints为列表"
                }))
                return

            joints = joints_data
            if len(joints) != 6:
                await websocket.send(self._json_msg({
                    "event": "teleop_error",
                    "message": f"关节角度数量错误：需要6个，实际{len(joints)}个"
                }))
                return

            # 检查该臂是否已启动遥操作
            if not self._teleop_modes.get(arm):
                await websocket.send(self._json_msg({
                    "event": "teleop_error",
                    "message": f"{arm}臂未启动遥操作模式"
                }))
                return

            # 采样日志：每10条记录一次
            self._teleop_msg_counts[arm] += 1
            if self._teleop_msg_counts[arm] % 10 == 0:
                logger.debug("遥操作指令 #%d: arm=%s, joints=%s",
                            self._teleop_msg_counts[arm], arm, joints)

            # 立即发送到机械臂
            if self._robot_system:
                try:
                    success = (
                        self._services.teleoperation.follow(
                            arm,
                            joints,
                            follow=follow,
                            trajectory_mode=trajectory_mode,
                        )
                    )
                    if not success:
                        logger.warning("遥操作指令 #%d 执行失败", self._teleop_msg_counts[arm])
                        await websocket.send(self._json_msg({
                            "event": "teleop_error",
                            "message": "关节指令执行失败"
                        }))
                except Exception as e:
                    logger.error("遥操作执行异常 #%d: %s", self._teleop_msg_counts[arm], str(e))
                    await websocket.send(self._json_msg({
                        "event": "teleop_error",
                        "message": f"执行异常: {str(e)}"
                    }))

            # 处理夹爪指令（直接传原始位置值，仅在值变化时触发）
            if grip is not None and grip != self._last_grip.get(arm):
                self._last_grip[arm] = grip
                self._schedule_background_task(
                    self._execute_grip_async(arm, grip),
                    name=f"WebSocketGrip-{arm}",
                )

        else:
            # 双臂模式
            if not isinstance(joints_data, dict):
                await websocket.send(self._json_msg({
                    "event": "teleop_error",
                    "message": "双臂模式需要joints为字典"
                }))
                return

            # 验证每个臂的关节角度
            for arm_name, joints in joints_data.items():
                if arm_name not in ["左", "右"]:
                    await websocket.send(self._json_msg({
                        "event": "teleop_error",
                        "message": f"未知的臂名称: {arm_name}"
                    }))
                    return

                if len(joints) != 6:
                    await websocket.send(self._json_msg({
                        "event": "teleop_error",
                        "message": f"{arm_name}臂关节角度数量错误：需要6个，实际{len(joints)}个"
                    }))
                    return

                # 检查该臂是否已启动遥操作
                if not self._teleop_modes.get(arm_name):
                    await websocket.send(self._json_msg({
                        "event": "teleop_error",
                        "message": f"{arm_name}臂未启动遥操作模式"
                    }))
                    return

            # 采样日志：每10条记录一次（使用左臂计数）
            self._teleop_msg_counts["左"] += 1
            self._teleop_msg_counts["右"] += 1
            if self._teleop_msg_counts["左"] % 10 == 0:
                logger.debug("双臂遥操作指令 #%d: 左=%s, 右=%s",
                            self._teleop_msg_counts["左"],
                            joints_data.get("左"),
                            joints_data.get("右"))

            # 并行执行双臂指令
            if self._robot_system:
                try:
                    success_results = {}
                    for arm_name, joints in joints_data.items():
                        success = (
                            self._services.teleoperation.follow(
                                arm_name,
                                joints,
                                follow=follow,
                                trajectory_mode=trajectory_mode,
                            )
                        )
                        success_results[arm_name] = success

                    if not all(success_results.values()):
                        failed_arms = [arm for arm, success in success_results.items() if not success]
                        logger.warning("双臂遥操作指令 #%d 部分执行失败: %s",
                                      self._teleop_msg_counts["左"], failed_arms)
                        await websocket.send(self._json_msg({
                            "event": "teleop_error",
                            "message": f"部分臂执行失败: {failed_arms}"
                        }))
                except Exception as e:
                    logger.error("双臂遥操作执行异常 #%d: %s", self._teleop_msg_counts["左"], str(e))
                    await websocket.send(self._json_msg({
                        "event": "teleop_error",
                        "message": f"执行异常: {str(e)}"
                    }))

            # 处理双臂夹爪指令
            if isinstance(grip, dict):
                for arm_name, grip_val in grip.items():
                    if arm_name in self._last_grip and grip_val is not None and grip_val != self._last_grip.get(arm_name):
                        self._last_grip[arm_name] = grip_val
                        self._schedule_background_task(
                            self._execute_grip_async(arm_name, grip_val),
                            name=f"WebSocketGrip-{arm_name}",
                        )

    async def _handle_teleop_stop(self, websocket, data: dict) -> None:
        """
        停止遥操作模式
        支持单臂和双臂两种方式：
        - 单臂: {"action": "teleop_stop", "arm": "左"}
        - 多臂: {"action": "teleop_stop", "arms": ["左", "右"]}
        - 双臂: {"action": "teleop_stop"} (无参数，停止所有臂)
        响应: {"event": "teleop_stopped", "arms": ["左"], "total_counts": {"左": 100}, "message": "遥操作模式已停止"}
        """
        arm = data.get("arm")
        arms_list = data.get("arms")

        # 确定要停止的臂
        if arm:
            # 单臂模式
            arms_to_stop = [arm]
        elif arms_list:
            # 多臂模式
            arms_to_stop = arms_list
        else:
            # 默认停止所有臂（双臂）
            arms_to_stop = ["左", "右"]

        # 验证臂名称
        for arm_name in arms_to_stop:
            if arm_name not in ["左", "右"]:
                await websocket.send(self._json_msg({
                    "event": "error",
                    "message": f"未知的臂名称: {arm_name}"
                }))
                return

        # 记录停止前的总计数
        total_counts = {}
        for arm_name in arms_to_stop:
            total_counts[arm_name] = self._teleop_msg_counts[arm_name]

        # 停止指定臂的遥操作模式
        for arm_name in arms_to_stop:
            self._teleop_modes[arm_name] = False
            self._teleop_msg_counts[arm_name] = 0
            self._last_grip[arm_name] = None  # 重置夹爪跟踪状态
        if not any(self._teleop_modes.values()):
            self._services.teleoperation.stop()

        logger.info("遥操作模式已停止: %s，共执行指令 %s", arms_to_stop, total_counts)
        await websocket.send(self._json_msg({
            "event": "teleop_stopped",
            "arms": arms_to_stop,
            "total_counts": total_counts,
            "message": "遥操作模式已停止"
        }))

    # ==================================================================
    # 数据采集控制
    # ==================================================================

    async def _handle_demo_session_start(self, websocket, data: dict) -> None:
        """
        开始数据采集会话
        请求: {"action": "demo_session_start", "task": "pick_bottle", "description": "抓取瓶子"}
        响应: {"event": "demo_session_started", "task": "pick_bottle", "next_episode_id": 0}
        """
        task = data.get("task")
        description = data.get("description", "")

        if not task:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "缺少task参数"
            }))
            return

        if (
            self._demo_recorder is not None
            or self._demo_camera_session is not None
        ):
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "数据采集会话已经启动",
            }))
            return

        # 初始化数据采集器（延迟初始化）
        camera_session = None
        try:
            from ..data_collection import RLBenchRecorder
            from ..data_collection.config import DataCollectionConfig

            config = DataCollectionConfig()
            camera_session = self._services.camera_access.open_depth(
                "websocket-data-collection"
            )
            self._services.devices.initialize(ROBOT_SYSTEM)
            recorder = RLBenchRecorder(
                robot_state_reader=(
                    self._services.robot_query.state_reader()
                ),
                camera_source=camera_session.camera,
                config=config,
            )
            result = recorder.start_session(task, description)
        except Exception as exc:
            if camera_session is not None:
                camera_session.close()
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": f"数据采集会话启动失败: {exc}",
            }))
            return

        if result.get("success"):
            self._demo_recorder = recorder
            self._demo_camera_session = camera_session
            # 更新会话状态
            self._demo_session["active"] = True
            self._demo_session["task"] = task
            self._demo_session["description"] = description
            self._demo_session["next_episode_id"] = result["next_episode_id"]

            logger.info(f"数据采集会话已启动: task={task}, next_episode_id={result['next_episode_id']}")

            await websocket.send(self._json_msg({
                "event": "demo_session_started",
                "task": task,
                "next_episode_id": result["next_episode_id"],
                "message": result["message"]
            }))
        else:
            camera_session.close()
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": result.get("message", "会话启动失败")
            }))

    async def _handle_demo_record_start(self, websocket, data: dict) -> None:
        """
        开始记录单条episode（自动启动遥操作模式）
        请求: {"action": "demo_record_start"}
        响应: {"event": "demo_record_started", "episode_id": 0}
        """
        if not self._demo_session["active"]:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "会话未启动，请先发送demo_session_start"
            }))
            return
        recorder = self._demo_recorder
        if recorder is None:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "数据采集器不可用",
            }))
            return

        teleoperation_was_active = self._services.teleoperation.active
        try:
            self._services.teleoperation.start()
        except Exception as exc:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": f"遥操作资源申请失败: {exc}",
            }))
            return

        try:
            result = recorder.start_recording()
        except Exception as exc:
            if not teleoperation_was_active:
                self._services.teleoperation.stop()
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": f"启动数据记录失败: {exc}",
            }))
            return

        if result.get("success"):
            episode_id = result["episode_id"]

            # 启动双臂遥操作模式
            for arm_name in ["左", "右"]:
                self._teleop_modes[arm_name] = True
                self._teleop_msg_counts[arm_name] = 0
                self._last_grip[arm_name] = None  # 重置夹爪跟踪状态

            logger.info("数据采集已自动启动遥操作模式: 双臂")

            logger.info(f"episode {episode_id} 开始记录（已自动启动遥操作）")

            await websocket.send(self._json_msg({
                "event": "demo_record_started",
                "episode_id": episode_id,
                "message": result["message"] + "（已自动启动遥操作模式）"
            }))
        else:
            if not teleoperation_was_active:
                self._services.teleoperation.stop()
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": result.get("message", "记录启动失败")
            }))

    async def _handle_demo_record_stop(self, websocket, data: dict) -> None:
        """
        结束记录单条episode并保存
        请求: {"action": "demo_record_stop"}
        响应: {"event": "demo_record_stopped", "episode_id": 0, "frames": 1500}
        """
        if not self._demo_session["active"]:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "会话未启动"
            }))
            return

        recorder = self._demo_recorder
        if recorder is None:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "数据采集器不可用",
            }))
            return

        # 结束记录并保存
        result = recorder.stop_recording()

        if result.get("success"):
            episode_id = result["episode_id"]
            frames = result["frames"]

            logger.info(f"episode {episode_id} 已保存，共{frames}帧")

            await websocket.send(self._json_msg({
                "event": "demo_record_stopped",
                "episode_id": episode_id,
                "frames": frames,
                "message": result["message"]
            }))
        else:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "episode_id": result.get("episode_id"),
                "frames": result.get("frames", 0),
                "message": result.get("message", "保存失败")
            }))

    async def _handle_demo_session_end(self, websocket, data: dict) -> None:
        """
        结束数据采集会话（自动停止遥操作模式）
        请求: {"action": "demo_session_end"}
        响应: {"event": "demo_session_ended", "message": "会话已结束"}
        """
        if not self._demo_session["active"]:
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "会话未启动"
            }))
            return

        recorder = self._demo_recorder
        camera_session = self._demo_camera_session
        if recorder is None or camera_session is None:
            try:
                await self._close_demo_recorder()
            except Exception as exc:
                logger.warning(
                    "清理不完整的数据采集会话失败: %s",
                    exc,
                )
            await websocket.send(self._json_msg({
                "event": "demo_record_error",
                "message": "数据采集会话状态不完整，已执行清理",
            }))
            return

        try:
            recorder.stop_recording()
            result = recorder.end_session()
        except Exception as exc:
            result = {
                "success": False,
                "message": f"结束数据采集会话失败: {exc}",
            }
        finally:
            try:
                self._services.teleoperation.stop()
            finally:
                camera_session.close()
                self._demo_recorder = None
                self._demo_camera_session = None
                for arm_name in ["左", "右"]:
                    self._teleop_modes[arm_name] = False
                    self._teleop_msg_counts[arm_name] = 0
                    self._last_grip[arm_name] = None

                logger.info(
                    "数据采集会话已自动停止遥操作模式: 双臂"
                )
                self._demo_session["active"] = False
                self._demo_session["task"] = None
                self._demo_session["description"] = None

        logger.info("数据采集会话已结束（已自动停止遥操作）")

        event = (
            "demo_session_ended"
            if result.get("success")
            else "demo_record_error"
        )
        await websocket.send(self._json_msg({
            "event": event,
            "message": result["message"] + "（已自动停止遥操作模式）",
        }))

    def _json_msg(self, data: dict[str, Any]) -> str:
        request_context = _CURRENT_REQUEST.get()
        payload = (
            request_context.decorate(data)
            if request_context is not None
            else dict(data)
        )
        payload.setdefault("api_version", WEBSOCKET_API_VERSION)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _composition_origin(websocket) -> str:
        return f"websocket:{id(websocket)}"

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
    uv run robot-llm
"""

import asyncio
from contextlib import suppress
import json
import logging
import threading
from collections.abc import Coroutine, Mapping
from typing import (
    Any,
    Dict,
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
    WEBSOCKET_TELEOPERATION_OWNER_PREFIX,
    websocket_teleoperation_owner,
)
from ..core.logging_config import bind_log_context, reset_log_context
from ..devices.runtime.ids import BODY_AXIS, CAMERA, ROBOT_SYSTEM
from .access_control import (
    AuditSink,
    WebSocketAccessController,
    WebSocketAccessError,
    WebSocketAccessLevel,
    WebSocketAuditEvent,
    log_websocket_audit_event,
)
from .handlers import (
    CompositionWebSocketHandler,
    DeviceWebSocketHandler,
    ExecutionWebSocketHandler,
    InteractionWebSocketHandler,
    TeleoperationWebSocketHandler,
)
from .metrics import WebSocketMetrics
from .protocol import (
    CURRENT_WEBSOCKET_REQUEST,
    WEBSOCKET_API_VERSION,
    RequestCorrelation,
    WebSocketErrorCode,
    WebSocketRequest,
    WebSocketRequestContext,
    WebSocketRequestError,
    WebSocketResponse,
)
from .request_limits import WebSocketRequestLimiter
from .routing import WebSocketRoute, WebSocketRouteRegistry
from .transport_security import (
    create_server_ssl_context,
    normalize_allowed_origins,
)

logger = logging.getLogger(__name__)


class _BoundedWebSocket:
    """Apply a send deadline while preserving the websocket interface."""

    def __init__(
        self,
        websocket: Any,
        timeout_seconds: float,
        metrics: WebSocketMetrics,
    ) -> None:
        self._websocket = websocket
        self._timeout_seconds = timeout_seconds
        self._metrics = metrics

    def __aiter__(self):
        return self._websocket.__aiter__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._websocket, name)

    async def send(self, message: str) -> None:
        started_at = self._metrics.send_started()
        try:
            await asyncio.wait_for(
                self._websocket.send(message),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            self._metrics.send_failed(started_at, timed_out=True)
            raise
        except Exception:
            self._metrics.send_failed(started_at, timed_out=False)
            raise
        else:
            self._metrics.send_succeeded(started_at)


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
        slow_send_threshold_seconds: float = 0.5,
        allowed_origins: tuple[str, ...] = (),
        tls_certificate_path: str = "",
        tls_private_key_path: str = "",
        reverse_proxy_mode: bool = False,
        teleoperation_command_timeout_seconds: float = 1.0,
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
        if slow_send_threshold_seconds <= 0:
            raise ValueError("slow_send_threshold_seconds must be positive")
        if slow_send_threshold_seconds > send_timeout_seconds:
            raise ValueError("slow_send_threshold_seconds must not exceed send_timeout_seconds")
        if teleoperation_command_timeout_seconds <= 0:
            raise ValueError("teleoperation_command_timeout_seconds must be positive")

        self._services = services
        self._host = normalized_host
        self._port = port
        self._max_message_size_bytes = max_message_size_bytes
        self._max_queued_messages = max_queued_messages
        self._send_timeout_seconds = send_timeout_seconds
        self._allowed_origins = normalize_allowed_origins(allowed_origins)
        self._ssl_context = create_server_ssl_context(
            tls_certificate_path,
            tls_private_key_path,
        )
        self._reverse_proxy_mode = bool(reverse_proxy_mode)
        self._validate_transport_boundary(auth_token)
        self._teleoperation_command_timeout_seconds = teleoperation_command_timeout_seconds
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
        self._metrics = WebSocketMetrics(
            slow_send_threshold_seconds=slow_send_threshold_seconds,
        )

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # AI 相关状态
        self._ai_processing = False

        # WebSocket 只持有交互会话，LLM Provider 生命周期属于 ApplicationServices。
        self._interaction_controller: Any = None

        # 相机帧订阅：通过 subscribe_camera_frames action 注册
        self._camera_frame_subs: Set = set()
        self._camera_push_task: Optional[asyncio.Task] = None
        self._camera_preview_session: Optional[CameraSession] = None

        # MiniCPM 代理配置（延迟初始化）
        self._minicpm_cfg: Any = None

        # LLM 聊天会话：id(websocket) -> {"active": True}
        self._minicpm_sessions: Dict[int, Dict] = {}

        # AI 执行跟踪（用于发送 ai_execution_finished 事件）
        self._ai_execution_pending = False
        self._execution_had_failure = False

        self._execution_handler = ExecutionWebSocketHandler(self)
        self._composition_handler = CompositionWebSocketHandler(self)
        self._interaction_handler = InteractionWebSocketHandler(self)
        self._device_handler = DeviceWebSocketHandler(self)
        self._teleoperation_handler = TeleoperationWebSocketHandler(self)
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
        scheme = "wss" if self._ssl_context else "ws"
        return f"{scheme}://{self._host}:{self._port}/"

    def _validate_transport_boundary(self, auth_token: str) -> None:
        is_loopback = self._host.lower() in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
        if self._reverse_proxy_mode and not is_loopback:
            raise ValueError("reverse proxy mode requires a loopback binding")
        if self._reverse_proxy_mode and self._ssl_context is not None:
            raise ValueError("reverse proxy mode and direct TLS are mutually exclusive")
        if not is_loopback and self._ssl_context is None:
            raise ValueError("non-loopback WebSocket binding requires TLS")
        externally_exposed = (
            not is_loopback or self._reverse_proxy_mode or self._ssl_context is not None
        )
        if externally_exposed and not auth_token.strip():
            raise ValueError("remote or proxy WebSocket deployment requires authentication")
        if externally_exposed and not self._allowed_origins:
            raise ValueError("remote or proxy WebSocket deployment requires allowed origins")

    async def start(self) -> None:
        """Bind the socket and return after the service is ready."""
        if websockets is None:
            raise RuntimeError("websockets 库未安装，无法启动 WebSocket 服务")
        if self._server is not None:
            raise RuntimeError("WebSocket service is already running")
        self._loop = asyncio.get_running_loop()
        self._composition_unsubscribe = self._services.composition.subscribe(
            self._on_composition_event
        )

        # 初始化 AI 组件
        self._interaction_handler._init_ai()

        # 相机在视觉动作、测试或订阅实时预览时按需启动。

        # 加载 MiniCPM 代理配置
        self._interaction_handler._init_minicpm_config()

        self._server = await websockets.serve(
            self._handler,
            self._host,
            self._port,
            max_size=self._max_message_size_bytes,
            max_queue=self._max_queued_messages,
            origins=((*self._allowed_origins, None) if self._allowed_origins else None),
            ssl=self._ssl_context,
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
        self._device_handler._stop_camera_if_idle()
        self._interaction_handler._close_interaction_session()
        await self._teleoperation_handler.close_data_collection()
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
            logger.debug("WebSocket loop closed before composition event scheduling")

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
            payload["sequence"] = [entry.to_dict() for entry in composition.sequence_entries()]
        elif event.change_type is CompositionChangeType.ACTIONS:
            payload["actions"] = [action.to_dict() for action in composition.list_actions()]
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
        tasks = tuple(task for task in self._background_tasks if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._camera_push_task = None

    # ------------------------------------------------------------------
    # AI 初始化（不依赖 Qt）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 连接处理
    # ------------------------------------------------------------------

    async def _handler(self, websocket) -> None:
        """所有连接统一进入主控处理器，通过 action 字段分发指令。"""
        try:
            await self._handle_frontend_ws(
                _BoundedWebSocket(
                    websocket,
                    self._send_timeout_seconds,
                    self._metrics,
                )
            )
        except TimeoutError:
            with suppress(Exception):
                await websocket.close(code=1011, reason="send timeout")

    async def _handle_frontend_ws(self, websocket) -> None:
        """处理前端主控 WebSocket 连接"""
        remote = getattr(websocket, "remote_address", None)
        client_id = self._register_client(websocket, remote)
        logger.info(
            "前端客户端已连接: client_id=%s remote=%s",
            client_id,
            remote,
        )
        try:
            await websocket.send(
                self._json_msg(
                    {
                        "event": "connected",
                        "client_id": client_id,
                        "authentication_configured": (self._access.authentication_configured),
                        "control_lease_seconds": (self._access.control_lease_seconds),
                        "api_version_required": True,
                    }
                )
            )
            async for raw in websocket:
                logger.debug(
                    "收到 WebSocket 消息: client_id=%s bytes=%d",
                    client_id,
                    len(raw),
                )

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(
                        self._json_msg(
                            {
                                "event": "error",
                                "code": (WebSocketErrorCode.INVALID_REQUEST.value),
                                "message": "无效的 JSON 格式",
                            }
                        )
                    )
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

    def _build_routes(self) -> dict[str, WebSocketRoute]:
        public = WebSocketAccessLevel.PUBLIC
        authenticated = WebSocketAccessLevel.AUTHENTICATED
        control = WebSocketAccessLevel.CONTROL
        routes = {
            # 安全会话
            "authenticate": WebSocketRoute(
                self._handle_authenticate,
                public,
            ),
            "control_status": WebSocketRoute(
                self._handle_control_status,
                public,
                audited=False,
            ),
            "server_metrics": WebSocketRoute(
                self._handle_server_metrics,
                authenticated,
                audited=False,
            ),
            "acquire_control": WebSocketRoute(
                self._handle_acquire_control,
                authenticated,
            ),
            "control_heartbeat": WebSocketRoute(
                self._handle_control_heartbeat,
                control,
            ),
            "release_control": WebSocketRoute(
                self._handle_release_control,
                control,
            ),
            # 执行控制
            "execute": WebSocketRoute(self._execution_handler._handle_execute, control),
            "execute_task": WebSocketRoute(
                self._execution_handler._handle_execute_task,
                control,
            ),
            "stop": WebSocketRoute(self._execution_handler._handle_stop, control),
            "quick_stop": WebSocketRoute(
                self._execution_handler._handle_quick_stop,
                control,
            ),
            "emergency_stop": WebSocketRoute(
                self._execution_handler._handle_emergency_stop,
                control,
            ),
            "pause": WebSocketRoute(self._execution_handler._handle_pause, control),
            "resume": WebSocketRoute(self._execution_handler._handle_resume, control),
            # 动作库管理
            "list_actions": WebSocketRoute(
                self._composition_handler._handle_list_actions,
                public,
                audited=False,
            ),
            "get_action_schema": WebSocketRoute(
                self._composition_handler._handle_get_action_schema,
                public,
                audited=False,
            ),
            "create_action": WebSocketRoute(
                self._composition_handler._handle_create_action,
                control,
            ),
            "delete_action": WebSocketRoute(
                self._composition_handler._handle_delete_action,
                control,
            ),
            "update_action": WebSocketRoute(
                self._composition_handler._handle_update_action,
                control,
            ),
            # 序列编排
            "get_sequence": WebSocketRoute(
                self._composition_handler._handle_get_sequence,
                public,
                audited=False,
            ),
            "add_to_sequence": WebSocketRoute(
                self._composition_handler._handle_add_to_sequence,
                control,
            ),
            "remove_from_sequence": WebSocketRoute(
                self._composition_handler._handle_remove_from_sequence,
                control,
            ),
            "move_in_sequence": WebSocketRoute(
                self._composition_handler._handle_move_in_sequence,
                control,
            ),
            "clear_sequence": WebSocketRoute(
                self._composition_handler._handle_clear_sequence,
                control,
            ),
            # 任务持久化
            "list_tasks": WebSocketRoute(
                self._composition_handler._handle_list_tasks,
                public,
                audited=False,
            ),
            "save_task": WebSocketRoute(
                self._composition_handler._handle_save_task,
                control,
            ),
            "load_task": WebSocketRoute(
                self._composition_handler._handle_load_task,
                control,
            ),
            "delete_task": WebSocketRoute(
                self._composition_handler._handle_delete_task,
                control,
            ),
            "get_task_detail": WebSocketRoute(
                self._composition_handler._handle_get_task_detail,
                public,
                audited=False,
            ),
            "rename_task": WebSocketRoute(
                self._composition_handler._handle_rename_task,
                control,
            ),
            "add_to_task": WebSocketRoute(
                self._composition_handler._handle_add_to_task,
                control,
            ),
            "remove_from_task": WebSocketRoute(
                self._composition_handler._handle_remove_from_task,
                control,
            ),
            "move_in_task": WebSocketRoute(
                self._composition_handler._handle_move_in_task,
                control,
            ),
            # AI 助手
            "ai_chat": WebSocketRoute(self._interaction_handler._handle_ai_chat, control),
            "ai_confirm": WebSocketRoute(
                self._interaction_handler._handle_ai_confirm,
                control,
            ),
            "ai_cancel": WebSocketRoute(
                self._interaction_handler._handle_ai_cancel,
                control,
            ),
            "ai_status": WebSocketRoute(
                self._interaction_handler._handle_ai_status,
                public,
                audited=False,
            ),
            "list_skills": WebSocketRoute(
                self._interaction_handler._handle_list_skills,
                public,
                audited=False,
            ),
            # 设备管理
            "status": WebSocketRoute(
                self._device_handler._handle_status,
                public,
                audited=False,
            ),
            "init_robots": WebSocketRoute(
                self._device_handler._handle_init_robots,
                control,
            ),
            "init_body": WebSocketRoute(
                self._device_handler._handle_init_body,
                control,
            ),
            "disconnect": WebSocketRoute(
                self._device_handler._handle_disconnect,
                control,
            ),
            "test_camera": WebSocketRoute(
                self._device_handler._handle_test_camera,
                control,
            ),
            # 相机读取会话
            "camera_status": WebSocketRoute(
                self._device_handler._handle_camera_status,
                public,
                audited=False,
            ),
            "subscribe_camera_frames": WebSocketRoute(
                self._device_handler._handle_subscribe_camera_frames,
                authenticated,
            ),
            "unsubscribe_camera_frames": WebSocketRoute(
                self._device_handler._handle_unsubscribe_camera_frames,
                authenticated,
            ),
            # LLM 聊天
            "chat_connect": WebSocketRoute(
                self._interaction_handler._handle_chat_connect,
                authenticated,
            ),
            "chat_disconnect": WebSocketRoute(
                self._interaction_handler._handle_chat_disconnect,
                authenticated,
            ),
            "chat": WebSocketRoute(
                self._interaction_handler._handle_chat_send,
                authenticated,
            ),
            "minicpm_status": WebSocketRoute(
                self._interaction_handler._handle_minicpm_status,
                public,
                audited=False,
            ),
            # 遥操作与数据采集
            "teleop_init": WebSocketRoute(
                self._teleoperation_handler._handle_teleop_init,
                control,
            ),
            "teleop_start": WebSocketRoute(
                self._teleoperation_handler._handle_teleop_start,
                control,
            ),
            "teleop_joint": WebSocketRoute(
                self._teleoperation_handler._handle_teleop_joint,
                control,
            ),
            "teleop_stop": WebSocketRoute(
                self._teleoperation_handler._handle_teleop_stop,
                control,
            ),
            "demo_session_start": WebSocketRoute(
                self._teleoperation_handler._handle_demo_session_start,
                control,
            ),
            "demo_record_start": WebSocketRoute(
                self._teleoperation_handler._handle_demo_record_start,
                control,
            ),
            "demo_record_stop": WebSocketRoute(
                self._teleoperation_handler._handle_demo_record_stop,
                control,
            ),
            "demo_session_end": WebSocketRoute(
                self._teleoperation_handler._handle_demo_session_end,
                control,
            ),
        }
        domains = {
            "access": {
                "authenticate",
                "control_status",
                "server_metrics",
                "acquire_control",
                "control_heartbeat",
                "release_control",
            },
            "execution": {
                "execute",
                "execute_task",
                "stop",
                "quick_stop",
                "emergency_stop",
                "pause",
                "resume",
            },
            "composition": {
                "list_actions",
                "get_action_schema",
                "create_action",
                "delete_action",
                "update_action",
                "get_sequence",
                "add_to_sequence",
                "remove_from_sequence",
                "move_in_sequence",
                "clear_sequence",
                "list_tasks",
                "save_task",
                "load_task",
                "delete_task",
                "get_task_detail",
                "rename_task",
                "add_to_task",
                "remove_from_task",
                "move_in_task",
            },
            "interaction": {
                "ai_chat",
                "ai_confirm",
                "ai_cancel",
                "ai_status",
                "list_skills",
                "chat_connect",
                "chat_disconnect",
                "chat",
                "minicpm_status",
            },
            "device": {
                "status",
                "init_robots",
                "init_body",
                "disconnect",
                "test_camera",
                "camera_status",
                "subscribe_camera_frames",
                "unsubscribe_camera_frames",
            },
            "teleoperation": {
                "teleop_init",
                "teleop_start",
                "teleop_joint",
                "teleop_stop",
                "demo_session_start",
                "demo_record_start",
                "demo_record_stop",
                "demo_session_end",
            },
        }
        registry = WebSocketRouteRegistry()
        for domain, actions in domains.items():
            registry.register(
                {action: routes[action] for action in actions},
                domain=domain,
            )
        return dict(registry.freeze())

    async def _dispatch(self, websocket, data: object) -> None:
        started_at = self._metrics.request_started()
        try:
            await self._dispatch_request(websocket, data)
        finally:
            self._metrics.request_finished(started_at)

    async def _dispatch_request(self, websocket, data: object) -> None:
        """Validate a typed request, then dispatch it to a domain handler."""
        try:
            request = WebSocketRequest.parse(
                data,
                known_actions=set(self._routes),
            )
        except WebSocketRequestError as exc:
            self._metrics.record_invalid_request()
            error_payload: dict[str, Any] = {
                "event": "error",
                "code": exc.code.value,
                "message": str(exc),
            }
            if exc.action:
                error_payload["action"] = exc.action
            if exc.request_id is not None:
                error_payload["request_id"] = exc.request_id
            if exc.code in {
                WebSocketErrorCode.API_VERSION_REQUIRED,
                WebSocketErrorCode.UNSUPPORTED_API_VERSION,
            }:
                error_payload["supported_api_versions"] = [WEBSOCKET_API_VERSION]
            await websocket.send(self._json_msg(error_payload))
            return

        action = request.action
        request_id = request.request_id
        route = self._routes[action]
        client_id = self._client_id(websocket)
        initial_session = self._access.session(client_id)
        request_context = WebSocketRequestContext(
            correlation=RequestCorrelation(
                client_id=client_id,
                principal=initial_session.principal,
                action=action,
                request_id=request_id,
            )
        )
        context_token = CURRENT_WEBSOCKET_REQUEST.set(request_context)
        log_context_token = bind_log_context(
            request_id=request_id,
            operation=action,
        )
        admission = self._request_limiter.admit(client_id)
        try:
            if not admission.accepted:
                if admission.code == WebSocketErrorCode.RATE_LIMITED.value:
                    self._metrics.record_rate_limited()
                else:
                    self._metrics.record_server_busy()
                await websocket.send(
                    self._json_msg(
                        {
                            "event": "error",
                            "code": admission.code,
                            "message": (
                                "请求频率超过限制"
                                if admission.code == WebSocketErrorCode.RATE_LIMITED.value
                                else "服务器并发请求已达到上限"
                            ),
                            "retry_after_seconds": (admission.retry_after_seconds),
                        }
                    )
                )
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
        except TimeoutError:
            raise
        except WebSocketAccessError as exc:
            self._metrics.record_access_denied()
            await websocket.send(
                self._json_msg(
                    {
                        "event": "access_denied",
                        "code": exc.code,
                        "message": str(exc),
                    }
                )
            )
            if route.audited:
                self._audit(
                    client_id=client_id,
                    action=action,
                    request_id=request_id,
                    outcome="denied",
                    code=exc.code,
                )
        except Exception as exc:
            self._metrics.record_internal_error()
            logger.exception(
                "WebSocket 请求处理异常: client_id=%s action=%s request_id=%s",
                client_id,
                action,
                request_id,
                extra={"request_id": request_id, "operation": action},
            )
            await websocket.send(
                self._json_msg(
                    {
                        "event": "error",
                        "code": WebSocketErrorCode.INTERNAL_ERROR.value,
                        "message": "请求处理发生内部错误",
                    }
                )
            )
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
                    else ("rejected" if request_context.error_code is not None else "completed")
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
            reset_log_context(log_context_token)
            CURRENT_WEBSOCKET_REQUEST.reset(context_token)

    def _register_client(self, websocket: Any, remote: object) -> str:
        client_id = uuid4().hex
        self._clients.add(websocket)
        self._client_ids[websocket] = client_id
        self._access.register(client_id, str(remote or "unknown"))
        self._metrics.connection_opened()
        return client_id

    async def _unregister_client(
        self,
        websocket: Any,
        *,
        reason: str,
    ) -> None:
        if reason == "send_failed":
            with suppress(Exception):
                await websocket.close(code=1011, reason="send failed")
        self._clients.discard(websocket)
        self._camera_frame_subs.discard(websocket)
        self._device_handler._stop_camera_if_idle()
        client_id = self._client_ids.pop(websocket, None)
        if client_id is not None:
            self._metrics.connection_closed()
            self._request_limiter.unregister(client_id)
            if self._access.unregister(client_id):
                await self._release_control_side_effects(
                    client_id,
                    reason=reason,
                )
        await self._interaction_handler._close_minicpm_session(websocket)

    async def _release_control_side_effects(
        self,
        client_id: str,
        *,
        reason: str,
    ) -> None:
        try:
            await self._teleoperation_handler.close_data_collection()
            await asyncio.to_thread(
                self._services.teleoperation.stop,
                websocket_teleoperation_owner(client_id),
            )
        except Exception as exc:
            logger.error(
                "释放控制客户端会话失败: client_id=%s reason=%s error=%s",
                client_id,
                reason,
                exc,
            )
        await self._broadcast(
            {
                "event": "control_released",
                "client_id": client_id,
                "reason": reason,
            }
        )

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
            stale_owners = await asyncio.to_thread(
                self._services.teleoperation.expire_stale_owners,
                owner_prefix=WEBSOCKET_TELEOPERATION_OWNER_PREFIX,
                timeout_seconds=self._teleoperation_command_timeout_seconds,
            )
            for owner_id in stale_owners:
                stale_client_id = owner_id.removeprefix(WEBSOCKET_TELEOPERATION_OWNER_PREFIX)
                try:
                    self._access.release_control(stale_client_id)
                except WebSocketAccessError:
                    pass
                await self._release_control_side_effects(
                    stale_client_id,
                    reason="teleoperation_watchdog",
                )

    def _client_id(self, websocket: Any) -> str:
        try:
            return self._client_ids[websocket]
        except KeyError as exc:
            raise WebSocketAccessError(
                "unknown_client",
                "WebSocket 客户端尚未注册",
            ) from exc

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
        await websocket.send(
            self._json_msg(
                {
                    "event": "authenticated",
                    "client_id": session.client_id,
                    "principal": session.principal,
                    "request_id": data["request_id"],
                }
            )
        )

    async def _handle_control_status(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        client_id = self._client_id(websocket)
        session = self._access.session(client_id)
        lease = self._access.control_snapshot()
        teleoperation = self._services.teleoperation.snapshot()
        await websocket.send(
            self._json_msg(
                {
                    "event": "control_status",
                    "client_id": client_id,
                    "authenticated": session.authenticated,
                    "authentication_configured": (self._access.authentication_configured),
                    "control_lease": lease.to_dict() if lease else None,
                    "teleoperation": teleoperation.to_dict(),
                    "request_id": data["request_id"],
                }
            )
        )

    async def _handle_server_metrics(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        await websocket.send(
            self._json_msg(
                {
                    "event": "server_metrics",
                    "metrics": self._metrics.snapshot().to_dict(),
                    "teleoperation_metrics": (
                        self._services.teleoperation.metrics_snapshot().to_dict()
                    ),
                    "vision_metrics": self._services.vision.metrics_snapshot().to_dict(),
                    "llm_metrics": self._services.llm.metrics_snapshot().to_dict(),
                    "request_id": data["request_id"],
                }
            )
        )

    async def _handle_acquire_control(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        lease = self._access.acquire_control(self._client_id(websocket))
        await websocket.send(
            self._json_msg(
                {
                    "event": "control_acquired",
                    "control_lease": lease.to_dict(),
                    "request_id": data["request_id"],
                }
            )
        )

    async def _handle_control_heartbeat(
        self,
        websocket: Any,
        data: dict[str, Any],
    ) -> None:
        lease = self._access.renew_control(self._client_id(websocket))
        await websocket.send(
            self._json_msg(
                {
                    "event": "control_heartbeat",
                    "control_lease": lease.to_dict(),
                    "request_id": data["request_id"],
                }
            )
        )

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
        await websocket.send(
            self._json_msg(
                {
                    "event": "control_release_completed",
                    "released": released,
                    "request_id": data["request_id"],
                }
            )
        )

    # ==================================================================
    # 执行控制
    # ==================================================================

    # ==================================================================
    # 动作库管理
    # ==================================================================

    # ==================================================================
    # 序列编排（对应 GUI 右侧序列列表）
    # ==================================================================

    # ==================================================================
    # 任务持久化
    # ==================================================================

    # ==================================================================
    # AI 助手
    # ==================================================================

    # ==================================================================
    # 设备管理
    # ==================================================================

    # ==================================================================
    # MiniCPM / LLM 聊天配置
    # ==================================================================

    # ==================================================================
    # 相机管理器
    # ==================================================================

    # ==================================================================
    # 相机帧订阅（dispatch 模式，替代独立 /camera/frames WebSocket）
    # ==================================================================

    # ==================================================================
    # LLM 聊天（dispatch 模式）
    # ==================================================================

    # ==================================================================
    # 序列解析
    # ==================================================================

    # ==================================================================
    # 执行器回调 → 广播到所有客户端
    # ==================================================================

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
        return tuple(client for client in results if client is not None)

    # ==================================================================
    # 遥操作控制
    # ==================================================================

    # ==================================================================
    # 数据采集控制
    # ==================================================================

    def _parse_sequence(self, raw: list[Any]) -> list[Any]:
        """Share the single protocol-to-sequence decoder across handlers."""
        return self._execution_handler._parse_sequence(raw)

    async def _submit_execution(
        self,
        websocket: Any,
        sequence: list[Any],
        *,
        origin: str,
        message: str,
        steps: int,
    ) -> bool:
        """Coordinate interaction plans with the execution domain handler."""
        return await self._execution_handler._submit_execution(
            websocket,
            sequence,
            origin=origin,
            message=message,
            steps=steps,
        )

    def _json_msg(
        self,
        data: Mapping[str, Any] | WebSocketResponse,
    ) -> str:
        response = (
            data if isinstance(data, WebSocketResponse) else WebSocketResponse.from_payload(data)
        )
        request_context = CURRENT_WEBSOCKET_REQUEST.get()
        response_payload = response.to_dict()
        payload = (
            request_context.decorate(response_payload)
            if request_context is not None
            else response_payload
        )
        payload.setdefault("api_version", WEBSOCKET_API_VERSION)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _composition_origin(websocket) -> str:
        return f"websocket:{id(websocket)}"

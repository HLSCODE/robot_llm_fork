from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...application import CommandRuntimeError
from ...llm import (
    LLMCapability,
    LLMContentPart,
    LLMMessage,
    LLMStreamEvent,
)
from ...voice_interaction import (
    CamerasModuleProvider,
    VoiceInteractionController,
    WakeFeedback,
)
from ..protocol import WebSocketRequest
from .base import WebSocketHandlerHost

logger = logging.getLogger(__name__)


def _extract_user_text(data: dict) -> Optional[str]:
    def text_from_content(content: object) -> Optional[str]:
        if isinstance(content, str):
            return content.strip() or None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text", "")).strip()
                    if text:
                        return text
        return None

    messages = data.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            result = text_from_content(message.get("content", ""))
            if result:
                return result
    if data.get("role") == "user":
        return text_from_content(data.get("content", ""))
    return None


class MiniCPMChatConfig:
    """Validated MiniCPM endpoint settings used for status reporting."""

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
        normalized = (scheme or "wss").strip().lower()
        if normalized in ("https", "wss"):
            return "wss"
        if normalized in ("http", "ws"):
            return "ws"
        return "wss"


class InteractionWebSocketHandler:
    def __init__(self, server: WebSocketHandlerHost) -> None:
        self._server = server

    async def _handle_ai_chat(self, websocket, data: WebSocketRequest) -> None:
        """
        远程文本意图入口。
        请求: {"action": "ai_chat", "text": "帮我抓一个瓶子"}
        流程: text → voice_interaction → chat / vision / command / session_control
        """
        text = data.get("text", "").strip()
        if not text:
            await websocket.send(
                self._server._json_msg({"event": "error", "message": "text 不能为空"})
            )
            return

        if self._server._ai_processing:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "正在处理上一次请求，请稍候"}
                )
            )
            return

        if self._server._interaction_controller is None:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": "语音/意图交互模块未初始化，请检查 LLM 配置",
                    }
                )
            )
            return

        if not await self._run_interaction_text(text):
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "请求未能启动，请稍后重试"}
                )
            )

    async def _handle_ai_confirm(self, websocket, data: WebSocketRequest) -> None:
        """Confirm the exact preview ID/version and submit it once."""
        preview_id = str(data.get("preview_id") or "").strip()
        version = data.get("version")
        if not preview_id or type(version) is not int:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "code": "invalid_preview_reference",
                        "message": "ai_confirm 必须包含 preview_id 和整数 version",
                    }
                )
            )
            return
        try:
            command = self._server._services.commands.confirm(
                preview_id,
                version,
                risk_acknowledged=(data.get("risk_acknowledged") is True),
                expected_source="websocket-ai",
            )
        except CommandRuntimeError as exc:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "code": exc.code,
                        "message": str(exc),
                    }
                )
            )
            return

        sequence = list(command.sequence)
        self._server._services.composition.replace_sequence(
            sequence,
            origin=self._server._composition_origin(websocket),
        )
        self._server._ai_execution_pending = True
        self._server._execution_had_failure = False
        accepted = await self._server._submit_execution(
            websocket,
            sequence,
            origin=command.source,
            message="AI 序列开始执行",
            steps=len(sequence),
        )
        if not accepted:
            self._server._ai_execution_pending = False

    async def _handle_ai_cancel(self, websocket, data: WebSocketRequest) -> None:
        """取消 AI 规划"""
        if self._server._interaction_controller is not None:
            self._server._interaction_controller.cancel_active_turn()
        preview_id = data.get("preview_id")
        version = data.get("version")
        if version is not None and type(version) is not int:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "code": "invalid_preview_reference",
                        "message": "version 必须是整数",
                    }
                )
            )
            return
        try:
            cancelled = self._server._services.commands.cancel_preview(
                str(preview_id) if preview_id is not None else None,
                version,
                expected_source="websocket-ai",
            )
        except CommandRuntimeError as exc:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "code": exc.code,
                        "message": str(exc),
                    }
                )
            )
            return
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "ai_cancelled",
                    "cancelled": cancelled,
                    "message": "AI 规划已取消" if cancelled else "没有待取消的预览",
                }
            )
        )

    async def _handle_ai_status(self, websocket, data: WebSocketRequest) -> None:
        """查询 AI/LLM 状态"""
        registry = self._server._services.llm
        planner_client = self._get_planner_client()
        chat_client = self._get_chat_client()
        planner_available = planner_client is not None and planner_client.is_available()
        chat_available = chat_client is not None and chat_client.is_available()
        llm_available = planner_available
        model_name = planner_client.get_model_name() if planner_client else "未配置"
        capabilities = (
            [cap.value for cap in chat_client.capabilities()] if chat_client else []
        )

        try:
            settings = self._server._services.settings
            provider = registry.default_provider.upper()
            provider_key = {
                "openai": settings.secrets.openai_api_key,
                "deepseek": settings.secrets.deepseek_api_key,
                "dashscope": settings.secrets.dashscope_api_key,
            }.get(provider.lower(), "")
            api_key_set = (
                bool(settings.llm.minicpm_gateway_host)
                if provider.lower() == "minicpm"
                else bool(provider_key)
            )
        except Exception as exc:
            logger.debug(
                "读取 AI 状态配置失败，返回未配置状态: %s",
                type(exc).__name__,
            )
            provider = "未知"
            api_key_set = False

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "ai_status",
                    "llm_available": llm_available,
                    "api_key_set": api_key_set,
                    "model": model_name,
                    "provider": provider,
                    "default_provider": registry.default_provider,
                    "providers": list(registry.provider_names),
                    "loaded_providers": list(registry.loaded_provider_names),
                    "provider_health": registry.get_provider_health(),
                    "metrics": registry.metrics_snapshot().to_dict(),
                    "capabilities": capabilities,
                    "chat_available": chat_available,
                    "chat_provider": chat_client.get_provider_name()
                    if chat_client
                    else "未配置",
                    "chat_model": chat_client.get_model_name()
                    if chat_client
                    else "未配置",
                    "planner_available": planner_available,
                    "planner_provider": planner_client.get_provider_name()
                    if planner_client
                    else "未配置",
                    "planner_model": planner_client.get_model_name()
                    if planner_client
                    else "未配置",
                    "processing": self._server._ai_processing,
                    "command_runtime": self._server._services.commands.status(
                        expected_source="websocket-ai"
                    ),
                    "has_preview": self._server._services.commands.pending(
                        expected_source="websocket-ai"
                    )
                    is not None,
                }
            )
        )

    async def _handle_list_skills(self, websocket, data: WebSocketRequest) -> None:
        """获取可用技能列表"""
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "skills_list",
                    "skills": self._server._services.commands.list_skills(),
                }
            )
        )

    async def _handle_minicpm_status(self, websocket, data: WebSocketRequest) -> None:
        """
        查询 MiniCPM 网关配置与聊天状态
        请求: {"action": "minicpm_status"}
        响应: {"event": "minicpm_status", "configured": bool,
               "gateway": "https://host:port",
               "ask_enabled": bool}
        """
        if self._server._minicpm_cfg is None:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "minicpm_status",
                        "configured": False,
                    }
                )
            )
            return

        cfg = self._server._minicpm_cfg
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "minicpm_status",
                    "configured": True,
                    "gateway": f"{cfg.ws_scheme}://{cfg.gateway_host}{cfg._port_suffix}{cfg.gateway_path_prefix}",
                    "realtime_path": cfg.realtime_path,
                    "ask_enabled": cfg.ask_enabled,
                    "chat_action": "chat_connect / chat / chat_disconnect",
                }
            )
        )

    async def _handle_chat_connect(self, websocket, data: WebSocketRequest) -> None:
        """标记聊天会话激活（不预先连接网关）。
        请求: {"action": "chat_connect"}
        成功: {"event": "chat_connected"}
        """
        provider = data.get("provider")
        try:
            chat_client = self._server._services.llm.get_chat_client(provider)
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"LLM provider 选择失败: {exc}"}
                )
            )
            return

        if chat_client is None or not chat_client.is_available():
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "LLM 聊天模型不可用，请检查模型配置"}
                )
            )
            return
        if LLMCapability.STREAM_CHAT not in chat_client.capabilities():
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "当前 LLM 不支持流式聊天"}
                )
            )
            return
        if id(websocket) in self._server._minicpm_sessions:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "聊天会话已连接，请先断开"}
                )
            )
            return

        self._server._minicpm_sessions[id(websocket)] = {
            "active": True,
            "provider": provider,
        }
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "chat_connected",
                    "provider": chat_client.get_provider_name(),
                    "model": chat_client.get_model_name(),
                }
            )
        )
        logger.info("LLM 聊天会话已就绪: %s", websocket.remote_address)

    async def _handle_chat_disconnect(self, websocket, data: WebSocketRequest) -> None:
        """断开 LLM 聊天会话。
        请求: {"action": "chat_disconnect"}
        """
        self._server._minicpm_sessions.pop(id(websocket), None)
        await websocket.send(self._server._json_msg({"event": "chat_disconnected"}))

    async def _handle_chat_send(self, websocket, data: WebSocketRequest) -> None:
        """发送聊天消息。
        请求: {"action": "chat", "messages": [...], "streaming": true, ...}
        服务端持续推送规范化的 chat_data 事件。上游模型连接由 LLM provider 维护。
        """
        session = self._server._minicpm_sessions.get(id(websocket))
        if session is None:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "请先发送 chat_connect 建立聊天会话"}
                )
            )
            return

        provider = data.get("provider") or session.get("provider")
        try:
            chat_client = self._server._services.llm.get_chat_client(provider)
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"LLM provider 选择失败: {exc}"}
                )
            )
            return

        if chat_client is None or not chat_client.is_available():
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "LLM 聊天模型不可用，请检查模型配置"}
                )
            )
            return
        if LLMCapability.STREAM_CHAT not in chat_client.capabilities():
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "当前 LLM 不支持流式聊天"}
                )
            )
            return

        payload = {k: v for k, v in data.items() if k != "action"}
        try:
            messages = self._parse_llm_messages(payload)
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"聊天消息解析失败: {exc}"}
                )
            )
            return

        if not messages:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "messages 不能为空"}
                )
            )
            return

        # chat 默认只做纯 LLM 聊天；需要远程控制当前机器人时，使用 ai_chat，
        # 或显式传 route_to_interaction / robot_interaction 复用同一段用户文本。
        user_text = _extract_user_text(payload)
        if user_text and (
            payload.get("route_to_interaction") or payload.get("robot_interaction")
        ):
            self._server._schedule_background_task(
                self._on_chat_user_text(user_text),
                name="WebSocketChatRouting",
            )

        options = self._extract_llm_options(payload)
        try:
            async for event in chat_client.stream_chat(messages, **options):
                await websocket.send(
                    self._server._json_msg(self._llm_stream_event_to_chat_data(event))
                )
                if event.type == "error":
                    break
        except Exception as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"LLM 聊天失败: {exc}"}
                )
            )

    def _close_interaction_session(self) -> None:
        controller = self._server._interaction_controller
        self._server._interaction_controller = None
        if controller is not None:
            controller.cancel_active_turn()

    def _init_ai(self) -> None:
        """初始化 LLM 客户端和技能引擎"""
        try:
            services = self._server._services
            settings = services.settings
            registry = services.llm
            logger.info(
                "使用应用级 LLMRegistry: default=%s, providers=%s",
                registry.default_provider,
                registry.describe_providers(),
            )

            voice_config = settings.voice.as_runtime_mapping()
            self._server._interaction_controller = VoiceInteractionController(
                llm_registry=registry,
                command_runtime=services.commands,
                source="websocket-ai",
                camera_provider=CamerasModuleProvider(
                    session_factory=self._camera_capture_session,
                    camera_name=settings.vision.vision_camera_name or None,
                ),
                timeout_s=voice_config["session_timeout_s"],
                turn_timeout_s=settings.runtime.interaction_turn_timeout_s,
                history_turns=voice_config["session_history_turns"],
                tts_enabled=voice_config["tts_enabled"],
                wake_feedback=WakeFeedback(
                    enabled=bool(voice_config.get("wake_feedback_enabled", True)),
                    text=str(
                        voice_config.get("wake_feedback_text") or "明德博士在，请说。"
                    ),
                ),
            )

        except Exception as e:
            logger.warning("AI 组件初始化失败: %s", e)

    def _get_chat_client(self, provider: Optional[str] = None):
        return self._server._services.llm.get_chat_client(provider)

    def _camera_capture_session(self):
        return self._server._services.camera_access.open("websocket-voice-capture")

    def _get_planner_client(self, provider: Optional[str] = None):
        return self._server._services.llm.get_planner_client(provider)

    async def _run_interaction_text(
        self,
        text: str,
        *,
        emit_minicpm_instruction: bool = False,
    ) -> bool:
        """通过 voice_interaction 处理远程文本输入。"""
        if self._server._ai_processing:
            return False
        if self._server._interaction_controller is None:
            return False

        self._server._services.commands.cancel_preview(expected_source="websocket-ai")
        self._server._ai_processing = True
        await self._server._broadcast(
            {"event": "ai_status_changed", "status": "分析中..."}
        )
        if emit_minicpm_instruction:
            await self._server._broadcast(
                {"event": "minicpm_instruction", "instruction": text}
            )

        try:
            async for event in self._server._interaction_controller.handle_text(
                text,
                require_awake=False,
            ):
                await self._emit_interaction_event(event.to_dict())
        except Exception as exc:
            logger.error("远程文本意图处理失败: %s", exc, exc_info=True)
            await self._server._broadcast(
                {
                    "event": "error",
                    "message": f"远程文本意图处理失败: {exc}",
                }
            )
        finally:
            self._server._ai_processing = False
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
            await self._server._broadcast(
                {
                    "event": "interaction_event",
                    "type": event_type,
                    "text": text,
                    "text_delta": event.get("text_delta") or "",
                    "intent": intent,
                    "data": interaction_data,
                }
            )

        if event_type == "intent":
            intent_name = (intent or {}).get("intent", "unknown")
            await self._server._broadcast(
                {
                    "event": "ai_intent",
                    "intent": intent,
                    "input": data.get("input"),
                }
            )
            if intent_name == "command":
                await self._server._broadcast(
                    {"event": "ai_status_changed", "status": "规划中..."}
                )
            elif intent_name == "vision_question":
                await self._server._broadcast(
                    {"event": "ai_status_changed", "status": "观察中..."}
                )
            else:
                await self._server._broadcast(
                    {"event": "ai_status_changed", "status": "回复中..."}
                )
            return

        if event_type == "command_preview":
            preview_id = data.get("preview_id")
            version = data.get("version")
            if (
                not preview_id
                or not isinstance(version, int)
                or not data.get("sequence")
            ):
                await self._server._broadcast(
                    {
                        "event": "error",
                        "message": "动作预览缺少 ID、版本或动作序列",
                    }
                )
                return

            plan = data.get("plan") or {}
            await self._server._broadcast(
                {
                    "event": "interaction_event",
                    "type": event_type,
                    "text": text,
                    "text_delta": event.get("text_delta") or "",
                    "intent": intent,
                    "data": interaction_data,
                }
            )

            if plan:
                await self._server._broadcast(
                    {
                        "event": "ai_skill_matched",
                        "skill_id": plan.get("skill_id"),
                        "skill_name": plan.get("skill_name"),
                        "confidence": plan.get("confidence"),
                        "params": plan.get("parameters") or {},
                        "reasoning": plan.get("reasoning") or "",
                    }
                )

            await self._server._broadcast(
                {
                    "event": "ai_preview_ready",
                    **interaction_data,
                    "message": text,
                }
            )
            await self._server._broadcast(
                {"event": "ai_status_changed", "status": "预览就绪"}
            )
            logger.info(
                "远程文本生成动作预览: id=%s version=%s",
                preview_id,
                version,
            )
            return

        if event_type == "text_delta":
            await self._server._broadcast(
                {
                    "event": "chat_data",
                    "type": "chunk",
                    "text_delta": event.get("text_delta") or "",
                    "source": "voice_interaction",
                    "packet": data.get("raw"),
                }
            )
            return

        if event_type == "audio_delta":
            await self._server._broadcast(
                {
                    "event": "chat_data",
                    "type": "chunk",
                    "audio_data": event.get("audio_data"),
                    "source": "voice_interaction",
                    "packet": data.get("raw"),
                }
            )
            return

        if event_type == "done":
            await self._server._broadcast(
                {
                    "event": "chat_data",
                    "type": "done",
                    "text": text,
                    "audio_data": event.get("audio_data"),
                    "source": "voice_interaction",
                    "metrics": data.get("metrics"),
                    "packet": data.get("raw"),
                }
            )
            if (
                self._server._services.commands.pending(expected_source="websocket-ai")
                is None
            ):
                await self._server._broadcast(
                    {"event": "ai_status_changed", "status": "完成"}
                )
            return

        if event_type == "error":
            await self._server._broadcast(
                {
                    "event": "error",
                    "message": text or "语音/意图交互处理失败",
                }
            )
            await self._server._broadcast(
                {"event": "ai_status_changed", "status": "失败"}
            )
            return

        if event_type == "ignored":
            await self._server._broadcast(
                {
                    "event": "ai_ignored",
                    "message": text or "已忽略本次输入",
                    "intent": intent,
                }
            )
            await self._server._broadcast(
                {"event": "ai_status_changed", "status": "已忽略"}
            )

    def _init_minicpm_config(self) -> None:
        """Load MiniCPM settings from the application snapshot."""
        try:
            settings = self._server._services.settings
            self._server._minicpm_cfg = MiniCPMChatConfig(
                gateway_host=settings.llm.minicpm_gateway_host,
                gateway_port=settings.llm.minicpm_gateway_port,
                ws_scheme=settings.llm.minicpm_ws_scheme,
                gateway_path_prefix=settings.llm.minicpm_gateway_path_prefix,
                realtime_path=settings.llm.minicpm_realtime_path,
                ask_enabled=settings.llm.minicpm_ask_enabled,
                ask_api_key=(
                    settings.secrets.minicpm_ask_api_key
                    or settings.secrets.openai_api_key
                ),
                ask_base_url=(
                    settings.llm.minicpm_ask_base_url
                    or settings.llm.openai_base_url
                ),
                ask_model=settings.llm.minicpm_ask_model,
            )
            logger.info(
                "MiniCPM 配置已加载: %s://%s%s%s",
                self._server._minicpm_cfg.ws_scheme,
                self._server._minicpm_cfg.gateway_host,
                self._server._minicpm_cfg._port_suffix,
                self._server._minicpm_cfg.gateway_path_prefix,
            )
        except Exception as exc:
            logger.warning("MiniCPM 配置加载失败: %s", exc)
            self._server._minicpm_cfg = None

    @staticmethod
    def _parse_llm_messages(payload: dict) -> List[LLMMessage]:
        """将前端 chat payload 转换为统一 LLMMessage。"""
        raw_messages = payload.get("messages")
        if raw_messages is None and payload.get("role"):
            raw_messages = [
                {
                    "role": payload.get("role"),
                    "content": payload.get("content", ""),
                }
            ]

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
            messages.append(
                LLMMessage(
                    role=role,
                    content=InteractionWebSocketHandler._parse_llm_content(content),
                )
            )
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
                parts.append(
                    LLMContentPart(
                        type="image",
                        data=part.get("data") or part.get("image") or part.get("url"),
                        mime_type=part.get("mime_type"),
                    )
                )
            elif part_type == "audio":
                parts.append(
                    LLMContentPart(
                        type="audio",
                        data=part.get("data") or part.get("audio"),
                        mime_type=part.get("mime_type"),
                    )
                )
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
        base_event = {
            "event": "chat_data",
            "packet": event.raw,
            "provenance": (
                event.provenance.to_dict()
                if event.provenance is not None
                else None
            ),
        }
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
                "provenance": base_event["provenance"],
            }
        return {
            **base_event,
            "type": event.type,
            "text": event.text,
            "metrics": event.metrics,
        }

    async def _close_minicpm_session(self, websocket) -> None:
        """清理指定客户端的 LLM 聊天会话标记。"""
        self._server._minicpm_sessions.pop(id(websocket), None)

    async def _on_chat_user_text(self, text: str) -> None:
        """把聊天消息显式路由到 voice_interaction。"""
        if not text.strip():
            return
        logger.info("聊天消息显式路由到 voice_interaction: %s", text[:80])
        if not await self._run_interaction_text(text, emit_minicpm_instruction=True):
            logger.debug(
                "voice_interaction 未启动（处理中或组件不可用），输入: %s", text[:80]
            )

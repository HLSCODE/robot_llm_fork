"""MiniCPM WebSocket 代理 — 系统提示注入。

将前端的 /ws/chat、/ws/duplex/*、/ws/half_duplex/* 连接透明代理到
MiniCPM 网关，同时在每个会话中注入 SCRIPT_SYSTEM_PROMPT。

用法（在 ws_server.py 中）:
    cfg = MiniCPMProxyConfig(gateway_host="application.himat.wiat.ac.cn", gateway_path_prefix="/minicpm")
    await proxy_chat(client_ws, cfg, on_user_message=self._on_minicpm_user_message)
    await proxy_duplex(client_ws, cfg, path_suffix, mode)
"""

import asyncio
import json
import logging
import ssl
from typing import Awaitable, Callable, Optional

try:
    import websockets
    import websockets.exceptions
except ImportError:
    websockets = None

from .interceptor import OutgoingInjector, ScriptStreamFilter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class MiniCPMProxyConfig:
    """MiniCPM 代理配置。"""

    def __init__(
        self,
        gateway_host: str = "localhost",
        gateway_port: int = 8006,
        gateway_scheme: str = "https",   # "http" 或 "https"
        gateway_path_prefix: str = "",   # 外网反向代理路径前缀，如 "/minicpm"
        ask_enabled: bool = True,
        ask_api_key: str = "",
        ask_base_url: str = "https://api.openai.com/v1",
        ask_model: str = "gpt-4o-mini",
    ) -> None:
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.gateway_scheme = gateway_scheme
        self.gateway_path_prefix = gateway_path_prefix.rstrip("/")
        self.ask_enabled = ask_enabled
        self.ask_api_key = ask_api_key
        self.ask_base_url = ask_base_url
        self.ask_model = ask_model

    @property
    def ws_scheme(self) -> str:
        return "wss" if self.gateway_scheme == "https" else "ws"

    @property
    def _port_suffix(self) -> str:
        """仅当端口非默认值时才附加 :port。"""
        default = 443 if self.gateway_scheme == "https" else 80
        return "" if self.gateway_port == default else f":{self.gateway_port}"

    @property
    def gateway_ws_base(self) -> str:
        return f"{self.ws_scheme}://{self.gateway_host}{self._port_suffix}{self.gateway_path_prefix}"

    def ssl_ctx(self) -> Optional[ssl.SSLContext]:
        """连接 HTTPS 网关时跳过证书验证（自签名证书）。"""
        if self.gateway_scheme != "https":
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


# ---------------------------------------------------------------------------
# 内部：用户输入提取
# ---------------------------------------------------------------------------

def _extract_user_text(data: dict) -> Optional[str]:
    """从聊天消息体中提取最后一条用户文本。

    支持格式:
        {"messages": [{"role": "user", "content": "文本"}, ...]}
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "文本"}]}, ...]}
        {"role": "user", "content": "文本"}                          (单条消息)
        {"role": "user", "content": [{"type": "text", "text": "..."}]}  (单条消息)
    """
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

    # 标准 messages 数组格式
    messages = data.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            result = _text_from_content(msg.get("content", ""))
            if result:
                return result

    # 单条消息格式 {"role": "user", "content": ...}
    if data.get("role") == "user":
        return _text_from_content(data.get("content", ""))

    return None


# ---------------------------------------------------------------------------
# 公开入口：聊天模式代理
# ---------------------------------------------------------------------------

async def proxy_chat(
    client_ws,
    cfg: MiniCPMProxyConfig,
    path_suffix: str = "ws/chat",
    query: str = "",
    on_user_message: Optional[Callable[[str], Awaitable[None]]] = None,
) -> None:
    """代理 /ws/chat 连接。

    on_user_message: 每当检测到用户发出的文本时调用（异步），可用于分类 + 规划
    """
    if websockets is None:
        logger.error("websockets 库未安装，无法代理 MiniCPM 连接")
        return

    gw_url = f"{cfg.gateway_ws_base}/{path_suffix}"
    if query:
        gw_url = f"{gw_url}?{query}"

    injector = OutgoingInjector("chat")

    try:
        async with websockets.connect(
            gw_url,
            ssl=cfg.ssl_ctx(),
            max_size=100 * 1024 * 1024,
            open_timeout=30,
        ) as gw_ws:
            await _run_ws_proxy_chat(client_ws, gw_ws, injector, on_user_message)
    except Exception as exc:
        logger.warning("MiniCPM chat 代理错误: %s", exc)
        try:
            await client_ws.close(1011, str(exc)[:120])
        except Exception:
            pass


async def _run_ws_proxy_chat(client_ws, gw_ws, injector, on_user_message=None):
    async def client_to_gw():
        try:
            async for raw in client_ws:
                if isinstance(raw, bytes):
                    print(raw)
                    await gw_ws.send(raw)
                else:
                    # 注入系统提示
                    processed = injector.process(raw)
                    await gw_ws.send(processed)
                    # 拦截用户输入文本，触发回调
                    if on_user_message:
                        try:
                            data = json.loads(raw)
                            user_text = _extract_user_text(data)
                            if user_text:
                                await on_user_message(user_text)
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            try:
                await gw_ws.close()
            except Exception:
                pass

    async def gw_to_client():
        try:
            async for raw in gw_ws:
                await client_ws.send(raw)
        except Exception:
            pass
        finally:
            try:
                await client_ws.close()
            except Exception:
                pass

    await asyncio.gather(
        asyncio.create_task(client_to_gw()),
        asyncio.create_task(gw_to_client()),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# 公开入口：双工 / 半双工模式代理
# ---------------------------------------------------------------------------

async def proxy_duplex(
    client_ws,
    cfg: MiniCPMProxyConfig,
    path_suffix: str,
    mode: str,          # "duplex" 或 "half_duplex"
    query: str = "",
) -> None:
    """代理 /ws/duplex/* 或 /ws/half_duplex/* 连接。"""
    if websockets is None:
        logger.error("websockets 库未安装，无法代理 MiniCPM 连接")
        return

    gw_url = f"{cfg.gateway_ws_base}/{path_suffix}"
    if query:
        gw_url = f"{gw_url}?{query}"

    injector = OutgoingInjector(mode)
    script_filter = ScriptStreamFilter()

    try:
        async with websockets.connect(
            gw_url,
            ssl=cfg.ssl_ctx(),
            max_size=100 * 1024 * 1024,
            open_timeout=30,
        ) as gw_ws:
            await _run_ws_proxy_duplex(client_ws, gw_ws, injector, script_filter)
    except Exception as exc:
        logger.warning("MiniCPM duplex 代理错误 [%s]: %s", path_suffix, exc)
        try:
            await client_ws.close(1011, str(exc)[:120])
        except Exception:
            pass


async def _run_ws_proxy_duplex(client_ws, gw_ws, injector, script_filter):
    async def client_to_gw():
        try:
            async for raw in client_ws:
                if isinstance(raw, bytes):
                    await gw_ws.send(raw)
                else:
                    await gw_ws.send(injector.process(raw))
        except Exception:
            pass
        finally:
            try:
                await gw_ws.close()
            except Exception:
                pass

    async def gw_to_client():
        try:
            async for raw in gw_ws:
                if isinstance(raw, bytes):
                    await client_ws.send(raw)
                else:
                    # 过滤脚本块后转发
                    try:
                        data = json.loads(raw)
                        if isinstance(data.get("text"), str):
                            data["text"] = script_filter.process(data["text"])
                        cleaned = json.dumps(data, ensure_ascii=False)
                    except Exception:
                        cleaned = raw
                    await client_ws.send(cleaned)
        except Exception:
            pass
        finally:
            try:
                await client_ws.close()
            except Exception:
                pass

    await asyncio.gather(
        asyncio.create_task(client_to_gw()),
        asyncio.create_task(gw_to_client()),
        return_exceptions=True,
    )

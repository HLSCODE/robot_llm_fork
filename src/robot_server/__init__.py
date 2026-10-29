"""Network adapters. Importing common contracts does not load a transport."""

from typing import Any

from .protocol import WEBSOCKET_API_VERSION

__all__ = ["RobotWebSocketServer", "RobotTcpServer", "WEBSOCKET_API_VERSION"]


def __getattr__(name: str) -> Any:
    if name == "RobotWebSocketServer":
        from .ws_server import RobotWebSocketServer
        return RobotWebSocketServer
    if name == "RobotTcpServer":
        from .tcp import RobotTcpServer
        return RobotTcpServer
    raise AttributeError(name)

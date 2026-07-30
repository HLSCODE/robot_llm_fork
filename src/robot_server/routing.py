from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .access_control import WebSocketAccessLevel
from .protocol import ACTION_REQUEST_SCHEMAS, WebSocketRequest

WebSocketHandler = Callable[
    [Any, WebSocketRequest],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class WebSocketRoute:
    handler: WebSocketHandler
    access_level: WebSocketAccessLevel
    audited: bool = True


class WebSocketRouteRegistry:
    """Owns the unique action-to-handler mapping for the transport."""

    def __init__(self) -> None:
        self._routes: dict[str, WebSocketRoute] = {}

    def register(
        self,
        routes: Mapping[str, WebSocketRoute],
        *,
        domain: str,
    ) -> None:
        duplicates = sorted(set(routes) & set(self._routes))
        if duplicates:
            raise ValueError(
                f"WebSocket domain '{domain}' 重复注册 action: {', '.join(duplicates)}"
            )
        missing_schemas = sorted(
            action for action in routes if action not in ACTION_REQUEST_SCHEMAS
        )
        if missing_schemas:
            raise ValueError(
                f"WebSocket domain '{domain}' 的 action 缺少请求 schema: "
                f"{', '.join(missing_schemas)}"
            )
        self._routes.update(routes)

    def freeze(self) -> Mapping[str, WebSocketRoute]:
        return dict(self._routes)

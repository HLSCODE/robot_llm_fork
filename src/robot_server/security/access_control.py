"""Authentication, authorization, control ownership, and security audit."""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock


security_audit_logger = logging.getLogger("security.websocket")


class WebSocketAccessLevel(str, Enum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    CONTROL = "control"


class WebSocketAccessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ClientSessionSnapshot:
    client_id: str
    remote_address: str
    authenticated: bool
    principal: str | None


@dataclass(frozen=True, slots=True)
class ControlLeaseSnapshot:
    owner_client_id: str
    principal: str
    expires_in_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "owner_client_id": self.owner_client_id,
            "principal": self.principal,
            "expires_in_seconds": round(self.expires_in_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class WebSocketAuditEvent:
    timestamp_utc: str
    client_id: str
    principal: str | None
    action: str
    request_id: str
    outcome: str
    code: str | None = None
    run_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        client_id: str,
        principal: str | None,
        action: str,
        request_id: str,
        outcome: str,
        code: str | None = None,
        run_id: str | None = None,
    ) -> "WebSocketAuditEvent":
        return cls(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            client_id=client_id,
            principal=principal,
            action=action,
            request_id=request_id,
            outcome=outcome,
            code=code,
            run_id=run_id,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "timestamp_utc": self.timestamp_utc,
            "client_id": self.client_id,
            "principal": self.principal,
            "action": self.action,
            "request_id": self.request_id,
            "outcome": self.outcome,
        }
        if self.code is not None:
            payload["code"] = self.code
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        return payload


AuditSink = Callable[[WebSocketAuditEvent], None]


def log_websocket_audit_event(event: WebSocketAuditEvent) -> None:
    security_audit_logger.info(
        "%s",
        json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True),
    )


@dataclass(slots=True)
class _ClientSession:
    client_id: str
    remote_address: str
    authenticated: bool = False
    principal: str | None = None

    def snapshot(self) -> ClientSessionSnapshot:
        return ClientSessionSnapshot(
            client_id=self.client_id,
            remote_address=self.remote_address,
            authenticated=self.authenticated,
            principal=self.principal,
        )


@dataclass(slots=True)
class _ControlLease:
    owner_client_id: str
    principal: str
    expires_at_seconds: float


class WebSocketAccessController:
    """Own authentication sessions and the single WebSocket control lease."""

    def __init__(
        self,
        auth_token: str,
        *,
        security_enabled: bool = True,
        control_lease_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if control_lease_seconds <= 0:
            raise ValueError("control_lease_seconds must be positive")
        self._security_enabled = bool(security_enabled)
        self._auth_token = auth_token if self._security_enabled else ""
        self._control_lease_seconds = float(control_lease_seconds)
        self._clock = clock
        self._sessions: dict[str, _ClientSession] = {}
        self._control_lease: _ControlLease | None = None
        self._lock = RLock()

    @property
    def authentication_configured(self) -> bool:
        return self._security_enabled and bool(self._auth_token)

    @property
    def control_lease_seconds(self) -> float:
        return self._control_lease_seconds

    def register(self, client_id: str, remote_address: str) -> None:
        if not client_id:
            raise ValueError("client_id must not be empty")
        with self._lock:
            if client_id in self._sessions:
                raise ValueError(f"duplicate client_id: {client_id}")
            session = _ClientSession(
                client_id=client_id,
                remote_address=remote_address,
            )
            if not self._security_enabled:
                session.authenticated = True
                session.principal = "security-disabled"
            self._sessions[client_id] = session

    def unregister(self, client_id: str) -> bool:
        with self._lock:
            self._sessions.pop(client_id, None)
            if (
                self._control_lease is None
                or self._control_lease.owner_client_id != client_id
            ):
                return False
            self._control_lease = None
            return True

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._control_lease = None

    def authenticate(
        self,
        client_id: str,
        provided_token: object,
    ) -> ClientSessionSnapshot:
        with self._lock:
            session = self._require_session_unlocked(client_id)
            if not self._security_enabled:
                return session.snapshot()
            if not self._auth_token:
                raise WebSocketAccessError(
                    "authentication_not_configured",
                    "WebSocket 写操作认证尚未配置",
                )
            if not isinstance(provided_token, str) or not provided_token:
                raise WebSocketAccessError(
                    "invalid_credentials",
                    "认证凭据无效",
                )
            token_matches = hmac.compare_digest(
                provided_token.encode("utf-8"),
                self._auth_token.encode("utf-8"),
            )
            if not token_matches:
                raise WebSocketAccessError(
                    "invalid_credentials",
                    "认证凭据无效",
                )
            session.authenticated = True
            session.principal = "websocket-api-key"
            return session.snapshot()

    def acquire_control(self, client_id: str) -> ControlLeaseSnapshot:
        with self._lock:
            session = self._require_authenticated_unlocked(client_id)
            self._expire_control_unlocked()
            lease = self._control_lease
            if lease is not None and lease.owner_client_id != client_id:
                raise WebSocketAccessError(
                    "control_busy",
                    "控制权当前由另一个客户端持有",
                )
            self._control_lease = _ControlLease(
                owner_client_id=client_id,
                principal=session.principal or "websocket-api-key",
                expires_at_seconds=(
                    self._clock() + self._control_lease_seconds
                ),
            )
            return self._control_snapshot_unlocked()

    def renew_control(self, client_id: str) -> ControlLeaseSnapshot:
        with self._lock:
            self._require_authenticated_unlocked(client_id)
            lease = self._require_control_unlocked(client_id)
            lease.expires_at_seconds = (
                self._clock() + self._control_lease_seconds
            )
            return self._control_snapshot_unlocked()

    def release_control(self, client_id: str) -> bool:
        with self._lock:
            self._require_authenticated_unlocked(client_id)
            lease = self._control_lease
            if lease is None:
                return False
            if lease.owner_client_id != client_id:
                raise WebSocketAccessError(
                    "control_not_owned",
                    "当前客户端不持有控制权",
                )
            self._control_lease = None
            return True

    def authorize(
        self,
        client_id: str,
        access_level: WebSocketAccessLevel,
    ) -> ClientSessionSnapshot:
        with self._lock:
            session = self._require_session_unlocked(client_id)
            if access_level is WebSocketAccessLevel.PUBLIC:
                return session.snapshot()
            self._require_authenticated_unlocked(client_id)
            if access_level is WebSocketAccessLevel.CONTROL:
                lease = self._require_control_unlocked(client_id)
                lease.expires_at_seconds = (
                    self._clock() + self._control_lease_seconds
                )
            return session.snapshot()

    def session(self, client_id: str) -> ClientSessionSnapshot:
        with self._lock:
            return self._require_session_unlocked(client_id).snapshot()

    def control_snapshot(self) -> ControlLeaseSnapshot | None:
        with self._lock:
            self._expire_control_unlocked()
            if self._control_lease is None:
                return None
            return self._control_snapshot_unlocked()

    def expire_control(self) -> str | None:
        with self._lock:
            return self._expire_control_unlocked()

    def _expire_control_unlocked(self) -> str | None:
        lease = self._control_lease
        if (
            lease is None
            or lease.expires_at_seconds > self._clock()
        ):
            return None
        self._control_lease = None
        return lease.owner_client_id

    def _require_session_unlocked(self, client_id: str) -> _ClientSession:
        try:
            return self._sessions[client_id]
        except KeyError as exc:
            raise WebSocketAccessError(
                "unknown_client",
                "WebSocket 客户端会话不存在",
            ) from exc

    def _require_authenticated_unlocked(
        self,
        client_id: str,
    ) -> _ClientSession:
        session = self._require_session_unlocked(client_id)
        if not session.authenticated:
            raise WebSocketAccessError(
                "authentication_required",
                "此操作需要先完成认证",
            )
        return session

    def _require_control_unlocked(self, client_id: str) -> _ControlLease:
        expired_owner = self._expire_control_unlocked()
        if expired_owner == client_id:
            raise WebSocketAccessError(
                "control_lease_expired",
                "当前客户端的控制权租约已过期",
            )
        lease = self._control_lease
        if lease is None or lease.owner_client_id != client_id:
            raise WebSocketAccessError(
                "control_required",
                "此操作需要先获取控制权",
            )
        return lease

    def _control_snapshot_unlocked(self) -> ControlLeaseSnapshot:
        lease = self._control_lease
        if lease is None:
            raise RuntimeError("control lease is not active")
        return ControlLeaseSnapshot(
            owner_client_id=lease.owner_client_id,
            principal=lease.principal,
            expires_in_seconds=max(
                0.0,
                lease.expires_at_seconds - self._clock(),
            ),
        )

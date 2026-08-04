"""TLS and Origin validation for the WebSocket transport boundary."""

from __future__ import annotations

from pathlib import Path
import ssl
from urllib.parse import urlsplit


def normalize_allowed_origins(origins: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for origin in origins:
        value = origin.strip()
        if not value:
            continue
        _validate_origin(value)
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def create_server_ssl_context(
    certificate_path: str,
    private_key_path: str,
) -> ssl.SSLContext | None:
    certificate = certificate_path.strip()
    private_key = private_key_path.strip()
    if not certificate and not private_key:
        return None
    if not certificate or not private_key:
        raise ValueError("TLS certificate and private key must be configured together")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=str(Path(certificate).expanduser()),
        keyfile=str(Path(private_key).expanduser()),
    )
    return context


def _validate_origin(origin: str) -> None:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid WebSocket Origin: {origin}")
    if parsed.username or parsed.password:
        raise ValueError("WebSocket Origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("WebSocket Origin must not contain path, query, or fragment")

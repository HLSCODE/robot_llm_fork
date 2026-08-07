from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from types import TracebackType

from .client import DgusClient, TraceCallback
from .config import DgusSdkConfig, load_config
from .controls import AnimationIconConfig, AnimationIconControl
from .services import ExpressionSwitcher


class T5LDgusSdk:
    def __init__(
        self,
        config: DgusSdkConfig,
        *,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ):
        self.config = config
        self._closed = False
        self.client = DgusClient.open_serial(
            config.serial.port,
            config.serial.baudrate,
            timeout=config.serial.timeout,
            write_timeout=config.serial.write_timeout,
            tx_delay=tx_delay,
            trace=trace,
        )
        self.animation_icon = AnimationIconControl(self.client, config.animation_icon)
        self.expression_service = ExpressionSwitcher(
            self.animation_icon,
            config.expression_models(),
            clear_vps=config.clear_vps,
            test_interval=config.test_interval,
        )

    def close(self) -> None:
        if not self._closed:
            self.client.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "T5LDgusSdk":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class T5LServiceContainer:
    """
    Thread-safe SDK service container.

    The container creates a new SDK before replacing the old one, so a failed
    initialization does not close or corrupt the currently cached service.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._sdk: T5LDgusSdk | None = None
        self._key: tuple[object, ...] | None = None

    @staticmethod
    def _key_for(
        config_path: str | Path | None,
        *,
        config: DgusSdkConfig | None = None,
        port: str | None = None,
        baudrate: int | None = None,
        vp_addr: int | None = None,
        sp_addr: int | None = None,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ) -> tuple[object, ...]:
        if config is not None:
            config_key: object = id(config)
        elif config_path is None:
            config_key = None
        else:
            path = Path(config_path).resolve()
            stat = path.stat()
            config_key = (str(path), stat.st_mtime_ns, stat.st_size)

        return (
            config_key,
            port,
            baudrate,
            vp_addr,
            sp_addr,
            tx_delay,
            id(trace) if trace is not None else None,
        )

    def get_sdk(
        self,
        config_path: str | Path | None = "t5l_config.json",
        *,
        config: DgusSdkConfig | None = None,
        port: str | None = None,
        baudrate: int | None = None,
        vp_addr: int | None = None,
        sp_addr: int | None = None,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ) -> T5LDgusSdk:
        key = self._key_for(
            config_path,
            config=config,
            port=port,
            baudrate=baudrate,
            vp_addr=vp_addr,
            sp_addr=sp_addr,
            tx_delay=tx_delay,
            trace=trace,
        )

        with self._lock:
            if self._sdk is not None and self._key == key and not self._sdk.closed:
                return self._sdk

            new_sdk = create_sdk(
                config_path,
                config=config,
                port=port,
                baudrate=baudrate,
                vp_addr=vp_addr,
                sp_addr=sp_addr,
                tx_delay=tx_delay,
                trace=trace,
            )
            old_sdk = self._sdk
            self._sdk = new_sdk
            self._key = key

        if old_sdk is not None:
            old_sdk.close()
        return new_sdk

    def get_expression_service(
        self,
        config_path: str | Path | None = "t5l_config.json",
        *,
        config: DgusSdkConfig | None = None,
        port: str | None = None,
        baudrate: int | None = None,
        vp_addr: int | None = None,
        sp_addr: int | None = None,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ) -> ExpressionSwitcher:
        return self.get_sdk(
            config_path,
            config=config,
            port=port,
            baudrate=baudrate,
            vp_addr=vp_addr,
            sp_addr=sp_addr,
            tx_delay=tx_delay,
            trace=trace,
        ).expression_service

    def close(self) -> None:
        with self._lock:
            sdk = self._sdk
            self._sdk = None
            self._key = None

        if sdk is not None:
            sdk.close()


default_container = T5LServiceContainer()


def _apply_overrides(
    config: DgusSdkConfig,
    *,
    port: str | None = None,
    baudrate: int | None = None,
    vp_addr: int | None = None,
    sp_addr: int | None = None,
) -> DgusSdkConfig:
    serial = config.serial
    animation_icon: AnimationIconConfig = config.animation_icon

    if port is not None or baudrate is not None:
        serial = replace(
            serial,
            port=port if port is not None else serial.port,
            baudrate=baudrate if baudrate is not None else serial.baudrate,
        )

    if vp_addr is not None or sp_addr is not None:
        animation_icon = replace(
            animation_icon,
            vp_addr=vp_addr if vp_addr is not None else animation_icon.vp_addr,
            sp_addr=sp_addr if sp_addr is not None else animation_icon.sp_addr,
        )

    return replace(config, serial=serial, animation_icon=animation_icon)


def create_sdk(
    config_path: str | Path | None = "t5l_config.json",
    *,
    config: DgusSdkConfig | None = None,
    port: str | None = None,
    baudrate: int | None = None,
    vp_addr: int | None = None,
    sp_addr: int | None = None,
    tx_delay: float = 0.05,
    trace: TraceCallback | None = None,
) -> T5LDgusSdk:
    resolved = config if config is not None else load_config(config_path)
    resolved = _apply_overrides(
        resolved,
        port=port,
        baudrate=baudrate,
        vp_addr=vp_addr,
        sp_addr=sp_addr,
    )
    return T5LDgusSdk(resolved, tx_delay=tx_delay, trace=trace)


def get_sdk(
    config_path: str | Path | None = "t5l_config.json",
    *,
    port: str | None = None,
    baudrate: int | None = None,
    vp_addr: int | None = None,
    sp_addr: int | None = None,
    tx_delay: float = 0.05,
    trace: TraceCallback | None = None,
) -> T5LDgusSdk:
    return default_container.get_sdk(
        config_path,
        port=port,
        baudrate=baudrate,
        vp_addr=vp_addr,
        sp_addr=sp_addr,
        tx_delay=tx_delay,
        trace=trace,
    )


def get_expression_service(
    config_path: str | Path | None = "t5l_config.json",
    *,
    port: str | None = None,
    baudrate: int | None = None,
    vp_addr: int | None = None,
    sp_addr: int | None = None,
    tx_delay: float = 0.05,
    trace: TraceCallback | None = None,
) -> ExpressionSwitcher:
    return default_container.get_expression_service(
        config_path,
        port=port,
        baudrate=baudrate,
        vp_addr=vp_addr,
        sp_addr=sp_addr,
        tx_delay=tx_delay,
        trace=trace,
    )


def close_sdk() -> None:
    default_container.close()

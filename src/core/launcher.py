from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

from .auxiliary_services import (
    AuxiliaryServiceHost,
    AuxiliaryServiceSnapshot,
)
from .config_loader import ConfigLoadError, load_application_settings
from .config_validation import (
    ConfigurationReport,
    StartupOptions,
    validate_startup_configuration,
)
from .logging_config import configure_logging
from .settings import ApplicationSettings


if TYPE_CHECKING:
    from ..application import ApplicationServices


logger = logging.getLogger(__name__)


def build_auxiliary_service_host(
    args: argparse.Namespace,
    settings: ApplicationSettings,
    services: "ApplicationServices",
) -> AuxiliaryServiceHost:
    """Compose enabled optional services around the shared application."""
    auxiliary_services = []
    options = resolve_startup_options(args, settings)
    if options.websocket_enabled:
        from ..robot_server.ws_server import RobotWebSocketServer

        auth_token = settings.secrets.websocket_auth_token
        if not auth_token:
            logger.warning(
                "未配置 WEBSOCKET_AUTH_TOKEN；WebSocket 仅提供公开只读接口，所有写操作均会被拒绝"
            )
        auxiliary_services.append(
            RobotWebSocketServer(
                services=services,
                host=options.websocket_host,
                port=options.websocket_port,
                auth_token=auth_token,
                control_lease_seconds=(settings.server.websocket_control_lease_seconds),
                max_message_size_bytes=(settings.server.websocket_max_message_size_bytes),
                max_requests_per_second=(settings.server.websocket_max_requests_per_second),
                max_concurrent_requests=(settings.server.websocket_max_concurrent_requests),
                max_queued_messages=(settings.server.websocket_max_queued_messages),
                send_timeout_seconds=(settings.server.websocket_send_timeout_seconds),
                slow_send_threshold_seconds=(settings.server.websocket_slow_send_threshold_seconds),
                allowed_origins=settings.server.websocket_allowed_origins,
                tls_certificate_path=(settings.server.websocket_tls_certificate_path),
                tls_private_key_path=(settings.server.websocket_tls_private_key_path),
                reverse_proxy_mode=(settings.server.websocket_reverse_proxy_mode),
                teleoperation_command_timeout_seconds=(
                    settings.server.teleoperation_command_timeout_seconds
                ),
            )
        )

    return AuxiliaryServiceHost(
        tuple(auxiliary_services),
        start_timeout_seconds=(settings.server.auxiliary_service_start_timeout_seconds),
        stop_timeout_seconds=(settings.server.auxiliary_service_stop_timeout_seconds),
    )


def resolve_startup_options(
    args: argparse.Namespace,
    settings: ApplicationSettings,
) -> StartupOptions:
    return StartupOptions(
        simulation=bool(getattr(args, "simulation", False) or settings.runtime.simulation_mode),
        websocket_enabled=bool(
            settings.server.websocket_enabled and not getattr(args, "disable_websocket", False)
        ),
        websocket_host=(getattr(args, "websocket_host", None) or settings.server.websocket_host),
        websocket_port=(
            getattr(args, "websocket_port", None)
            if getattr(args, "websocket_port", None) is not None
            else settings.server.websocket_port
        ),
        log_level=(getattr(args, "log_level", None) or settings.logging.level),
    )


def run_gui(args: argparse.Namespace, settings: ApplicationSettings) -> int:
    """Run the GUI and optional network services in one process."""
    from PyQt6.QtWidgets import QApplication

    from ..application import create_application_services
    from ..gui.main_window import MainWindow

    services = create_application_services(
        settings,
        simulation=args.simulation,
    )
    auxiliary_host = build_auxiliary_service_host(
        args,
        settings,
        services,
    )
    try:
        app = QApplication([sys.argv[0]])
        app.setStyle("Fusion")
        window = MainWindow(services)
        window.show()
        _start_auxiliary_services(auxiliary_host)
        return app.exec()
    finally:
        _shutdown_application(auxiliary_host, services)


def _start_auxiliary_services(host: AuxiliaryServiceHost) -> None:
    try:
        snapshots = host.start()
    except Exception:
        logger.exception("附加服务宿主启动失败；GUI 将继续运行")
        return
    for snapshot in snapshots:
        _log_service_snapshot(snapshot)


def _log_service_snapshot(snapshot: AuxiliaryServiceSnapshot) -> None:
    if snapshot.running:
        logger.info(
            "附加服务已启动: %s %s",
            snapshot.name,
            snapshot.endpoint,
        )
        return
    logger.warning(
        "附加服务不可用: %s state=%s error=%s",
        snapshot.name,
        snapshot.state.value,
        snapshot.error,
    )


def _shutdown_application(
    host: AuxiliaryServiceHost,
    services: "ApplicationServices",
) -> None:
    try:
        snapshots = host.stop()
        for snapshot in snapshots:
            if snapshot.error:
                logger.warning(
                    "附加服务关闭异常: %s: %s",
                    snapshot.name,
                    snapshot.error,
                )
    except Exception:
        logger.exception("附加服务宿主关闭失败")

    try:
        services.localization.close()
    except Exception:
        logger.exception("定位服务关闭失败")

    try:
        errors = services.devices.shutdown_all()
    except Exception:
        logger.exception("设备运行时关闭失败")
        return
    for device_id, error in errors.items():
        logger.warning("设备关闭异常: %s: %s", device_id, error)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="机器人控制系统")
    parser.add_argument(
        "--websocket-host",
        help="WebSocket 监听地址，默认读取配置",
    )
    parser.add_argument(
        "--websocket-port",
        type=int,
        help="WebSocket 监听端口，默认读取配置",
    )
    parser.add_argument(
        "--disable-websocket",
        action="store_true",
        help="本次启动不启用 WebSocket 附加服务",
    )
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="模拟模式，不连接硬件",
    )
    parser.add_argument(
        "--log-level",
        help="日志级别，默认读取配置",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="只校验启动配置和数据路径，不启动 GUI 或硬件",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        settings = load_application_settings()
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    options = resolve_startup_options(args, settings)
    args.simulation = options.simulation
    try:
        configure_logging(settings.logging, level_override=options.log_level)
    except (OSError, ValueError) as exc:
        print(f"日志初始化失败: {exc}", file=sys.stderr)
        return 2
    report = validate_startup_configuration(settings, options)
    _log_configuration_report(report)
    if report.errors:
        logger.error("启动已中止：配置校验发现 %d 个错误", len(report.errors))
        return 2
    if args.check_config:
        logger.info("启动配置校验通过")
        return 0
    logger.info(
        "启动 GUI 应用: mode=%s websocket=%s",
        "simulation" if args.simulation else "hardware",
        (
            "disabled"
            if args.disable_websocket or not settings.server.websocket_enabled
            else "enabled"
        ),
    )
    return run_gui(args, settings)


def _log_configuration_report(report: ConfigurationReport) -> None:
    for issue in report.warnings:
        logger.warning(
            "配置警告 [%s] %s: %s",
            issue.code,
            issue.field,
            issue.message,
        )
    for issue in report.errors:
        logger.error(
            "配置错误 [%s] %s: %s",
            issue.code,
            issue.field,
            issue.message,
        )


if __name__ == "__main__":
    raise SystemExit(main())

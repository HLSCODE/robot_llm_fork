from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Protocol

from .auxiliary_services import (
    AuxiliaryServiceHost,
    AuxiliaryServiceSnapshot,
)
from ..configuration.config_loader import (
    ConfigLoadError,
    configuration_source_paths,
    load_application_settings,
)
from ..configuration.config_validation import (
    ConfigurationReport,
    StartupOptions,
    validate_startup_configuration,
)
from ..observability.logging_config import configure_logging
from ..configuration.settings import ApplicationSettings


if TYPE_CHECKING:
    from ..application import ApplicationServices


logger = logging.getLogger(__name__)


class _QtThreadHandle(Protocol):
    def isRunning(self) -> bool: ...

    def quit(self) -> None: ...

    def wait(self, milliseconds: int = ...) -> bool: ...


class _GuiShutdownHandle(Protocol):
    def shutdown_after_event_loop(self) -> None: ...


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

        security_enabled = settings.server.websocket_security_enabled
        auth_token = settings.secrets.websocket_auth_token
        if not security_enabled:
            logger.warning(
                "WEBSOCKET_SECURITY_ENABLED=false；WebSocket TLS、认证和 Origin 限制已关闭"
            )
        elif not auth_token:
            logger.warning(
                "未配置 WEBSOCKET_AUTH_TOKEN；WebSocket 仅提供公开只读接口，所有写操作均会被拒绝"
            )
        auxiliary_services.append(
            RobotWebSocketServer(
                services=services,
                host=options.websocket_host,
                port=options.websocket_port,
                security_enabled=security_enabled,
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
    websocket_port_value = getattr(args, "websocket_port", None)
    if websocket_port_value is None:
        websocket_port = settings.server.websocket_port
    elif isinstance(websocket_port_value, bool) or not isinstance(
        websocket_port_value,
        int,
    ):
        raise ValueError("websocket_port must be an integer")
    else:
        websocket_port = websocket_port_value
    return StartupOptions(
        simulation=bool(getattr(args, "simulation", False) or settings.runtime.simulation_mode),
        websocket_enabled=bool(
            settings.server.websocket_enabled and not getattr(args, "disable_websocket", False)
        ),
        websocket_host=(getattr(args, "websocket_host", None) or settings.server.websocket_host),
        websocket_port=websocket_port,
        log_level=(getattr(args, "log_level", None) or settings.logging.level),
    )


def run_gui(args: argparse.Namespace, settings: ApplicationSettings) -> int:
    """Run the GUI and optional network services in one process."""
    from PySide6.QtCore import QThread, QTimer, Qt

    from ..application import create_application_services
    from ..gui.application_lifecycle import (
        GUI_STARTUP_FAILURE_EXIT_CODE,
        GuiStartupError,
        create_gui_application,
        install_gui_application_lifecycle,
    )
    from ..gui.controllers.main_window import MainWindow
    from ..gui.theme import ThemeController, ThemeMode
    from ..gui.workbench_layout import QSettingsWorkbenchLayoutStore
    from ..gui.controllers.startup import (
        GuiAuxiliaryServiceStartupWorker,
        GuiAuxiliaryStartupResultReceiver,
    )
    from ..gui.views import StartupProgressCard

    try:
        app = create_gui_application([sys.argv[0]])
    except GuiStartupError as exc:
        logger.error("GUI 启动环境不可用: %s", exc)
        return GUI_STARTUP_FAILURE_EXIT_CODE
    gui_lifecycle = install_gui_application_lifecycle(app)
    app.setStyle("Fusion")
    theme_controller = ThemeController(
        app,
        ThemeMode.parse(settings.gui.theme),
    )
    startup_card = StartupProgressCard()
    startup_card.exit_requested.connect(app.quit)
    presentation = startup_card.show_if_available()
    if not presentation.allowed:
        logger.error("GUI 启动环境不可用: %s", presentation.reason)
        app.quit()
        return GUI_STARTUP_FAILURE_EXIT_CODE
    app.processEvents()

    services = None
    auxiliary_host = None
    auxiliary_startup_thread = None
    auxiliary_startup_worker = None
    auxiliary_startup_receiver = None
    window = None
    reveal_timer: QTimer | None = None
    try:
        startup_card.set_progress(
            8,
            "正在检查任务数据...",
            "",
        )
        services = create_application_services(
            settings,
            simulation=args.simulation,
        )
        startup_card.set_progress(
            16,
            "应用服务已就绪",
            "",
        )
        auxiliary_host = build_auxiliary_service_host(
            args,
            settings,
            services,
        )
        app.processEvents()
        presentation = gui_lifecycle.presentation_status()
        if not presentation.allowed:
            logger.error("GUI 启动环境在服务初始化期间失效: %s", presentation.reason)
            startup_card.close()
            app.quit()
            return GUI_STARTUP_FAILURE_EXIT_CODE
        window = MainWindow(
            services,
            theme_controller,
            layout_store=QSettingsWorkbenchLayoutStore(),
        )
        app.aboutToQuit.connect(
            window.prepare_shutdown,
            Qt.ConnectionType.DirectConnection,
        )
        window.startup_progress_changed.connect(startup_card.set_progress)

        def reveal_main_window(message: str) -> None:
            nonlocal reveal_timer
            startup_card.set_progress(100, message, "")

            def reveal() -> None:
                nonlocal reveal_timer
                reveal_timer = None
                presentation = gui_lifecycle.presentation_status(window)
                if not presentation.allowed:
                    logger.info(
                        "已取消迟到的主窗口显示请求: %s",
                        presentation.reason,
                    )
                    return
                window.show()
                window.raise_()
                window.activateWindow()
                startup_card.close()

            if reveal_timer is not None:
                reveal_timer.stop()
                reveal_timer.deleteLater()
            reveal_timer = QTimer(window)
            reveal_timer.setSingleShot(True)
            reveal_timer.timeout.connect(reveal)
            reveal_timer.start(180)

        def start_auxiliary_services(success: bool, message: str) -> None:
            nonlocal auxiliary_startup_thread
            nonlocal auxiliary_startup_worker
            nonlocal auxiliary_startup_receiver
            if not success:
                startup_card.mark_failed(message)
                return
            startup_card.set_progress(
                97,
                "正在启动附加服务...",
                "",
            )
            thread = QThread(app)
            thread.setObjectName("GuiAuxiliaryServiceStartupThread")
            worker = GuiAuxiliaryServiceStartupWorker(auxiliary_host.start)
            worker.moveToThread(thread)

            def services_started(snapshots: object) -> None:
                if not isinstance(snapshots, tuple) or not all(
                    isinstance(snapshot, AuxiliaryServiceSnapshot)
                    for snapshot in snapshots
                ):
                    services_failed("附加服务返回了无效的启动快照")
                    return
                for snapshot in snapshots:
                    _log_service_snapshot(snapshot)
                reveal_main_window(message)

            def services_failed(error: str) -> None:
                logger.error("附加服务宿主启动失败；GUI 将继续运行: %s", error)
                reveal_main_window(f"{message}，附加服务不可用")

            def auxiliary_thread_finished() -> None:
                nonlocal auxiliary_startup_thread, auxiliary_startup_worker
                auxiliary_startup_thread = None
                auxiliary_startup_worker = None

            receiver = GuiAuxiliaryStartupResultReceiver(
                services_started,
                services_failed,
                auxiliary_thread_finished,
                app,
            )

            thread.started.connect(worker.run)
            worker.completed.connect(
                receiver.handle_completed,
                Qt.ConnectionType.QueuedConnection,
            )
            worker.failed.connect(
                receiver.handle_failed,
                Qt.ConnectionType.QueuedConnection,
            )
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.completed.connect(
                thread.quit,
                Qt.ConnectionType.DirectConnection,
            )
            worker.failed.connect(
                thread.quit,
                Qt.ConnectionType.DirectConnection,
            )
            thread.finished.connect(
                receiver.handle_thread_finished,
                Qt.ConnectionType.QueuedConnection,
            )
            thread.finished.connect(thread.deleteLater)
            auxiliary_startup_thread = thread
            auxiliary_startup_worker = worker
            auxiliary_startup_receiver = receiver
            thread.start()

        window.startup_finished.connect(start_auxiliary_services)
        return app.exec()
    except Exception as exc:
        logger.exception("GUI 初始化失败")
        startup_card.mark_failed(f"启动失败：{exc}")
        return app.exec()
    finally:
        _shutdown_gui_runtime(
            auxiliary_startup_thread=auxiliary_startup_thread,
            window=window,
            auxiliary_host=auxiliary_host,
            services=services,
        )


def _shutdown_gui_runtime(
    *,
    auxiliary_startup_thread: _QtThreadHandle | None,
    window: _GuiShutdownHandle | None,
    auxiliary_host: AuxiliaryServiceHost | None,
    services: "ApplicationServices | None",
) -> None:
    """Release independently owned GUI resources even if one cleanup step fails."""
    try:
        _stop_auxiliary_startup_thread(auxiliary_startup_thread)
    except Exception:
        logger.exception("附加服务启动线程关闭失败")

    if window is not None:
        try:
            window.shutdown_after_event_loop()
        except Exception:
            logger.exception("GUI 后台资源关闭失败")

    try:
        from ..gui.application_lifecycle import join_gui_background_threads

        join_gui_background_threads()
    except Exception:
        logger.exception("GUI 短生命周期后台线程关闭失败")

    if auxiliary_host is not None and services is not None:
        _shutdown_application(auxiliary_host, services)


def _stop_auxiliary_startup_thread(thread: _QtThreadHandle | None) -> None:
    if thread is None:
        return
    try:
        is_running = thread.isRunning()
    except RuntimeError:
        logger.debug("附加服务启动线程的 Qt 对象已释放")
        return
    if not is_running:
        return
    thread.quit()
    if not thread.wait(10_000):
        logger.warning(
            "附加服务启动线程未在 10 秒内退出；继续等待资源释放，"
            "避免在线程运行时销毁 Qt 对象"
        )
        thread.wait()


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
        services.external_localization.close()
    except Exception:
        logger.exception("定位服务关闭失败")

    try:
        asyncio.run(services.llm.close())
    except Exception:
        logger.exception("LLM Registry 关闭失败")

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
        "--config",
        help="TOML 配置文件路径，默认使用 config/config.toml",
    )
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
        settings = load_application_settings(args.config)
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
    _log_configuration_sources(args.config)
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


def _log_configuration_sources(config_path: str | None) -> None:
    try:
        sources = configuration_source_paths(config_path)
    except ConfigLoadError as exc:
        logger.warning("无法读取配置来源信息: %s", exc)
        return
    if not sources:
        logger.warning("未找到本机配置文件，当前使用类型化默认值与环境变量")
        return
    logger.info("配置入口已加载: %s", sources[0])
    for source in sources[1:]:
        logger.info("配置子文件已加载: %s", source)


if __name__ == "__main__":
    raise SystemExit(main())

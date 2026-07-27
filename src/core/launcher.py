"""
统一启动器 - 根据环境变量选择启动 GUI 或 WebSocket Server

环境变量:
    RUN_MODE=gui    → PyQt6 图形界面
    RUN_MODE=server → WebSocket 服务（默认）

用法:
    python run.py
    RUN_MODE=gui python run.py
    RUN_MODE=server python run.py --port 9000
"""
import sys
import os
import argparse
import logging
from ..core.config_loader import Config
from ..device_runtime.ids import BODY_AXIS, MOBILE_BASE, NECK, ROBOT_SYSTEM


def setup_logging(level: str = "INFO") -> None:
    """配置日志（控制台 + 文件）"""
    import os
    from datetime import datetime
    
    # 创建 log 文件夹
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log")
    os.makedirs(log_dir, exist_ok=True)
    
    # 日志文件名：按日期
    log_file = os.path.join(log_dir, f"server_{datetime.now().strftime('%Y%m%d')}.log")
    
    # 配置日志
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),  # 控制台输出
            logging.FileHandler(log_file, encoding='utf-8'),  # 文件输出
        ]
    )


def run_gui(args, config):
    """启动 GUI 模式"""
    from PyQt6.QtWidgets import QApplication
    from ..application import create_application_services
    from ..gui.main_window import MainWindow

    services = create_application_services(
        config,
        simulation=args.simulation,
    )
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = MainWindow(services)
    window.show()

    sys.exit(app.exec())


def run_server(args, config=None):
    """启动 WebSocket Server 模式"""
    # 启动 WebSocket 服务
    from ..application import create_application_services
    from ..robot_server.ws_server import RobotWebSocketServer

    # 优先使用命令行参数，其次使用 config.env 配置，最后使用默认值
    host = args.host if args.host != "0.0.0.0" else (config.WEBSOCKET_HOST if config else "0.0.0.0")
    port = args.port if args.port != 8765 else (config.WEBSOCKET_PORT if config else 8765)
    services = create_application_services(
        config,
        simulation=args.simulation,
    )

    server = RobotWebSocketServer(
        services=services,
        host=host,
        port=port,
    )

    print("=" * 50)
    print(f"机器人 WebSocket 控制服务")
    print(f"地址：ws://{host}:{port}")
    print(f"模式：{'模拟' if args.simulation else '硬件'}")
    print("=" * 50)

    try:
        for device_id in (ROBOT_SYSTEM, BODY_AXIS, NECK, MOBILE_BASE):
            try:
                services.devices.initialize(device_id)
                print(f"设备初始化成功：{device_id}")
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "设备初始化失败：%s: %s",
                    device_id,
                    exc,
                )
        server.run()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        services.devices.shutdown_all()


def main():
    """主函数 - 根据环境变量选择运行模式"""
    parser = argparse.ArgumentParser(description="机器人控制系统")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认：0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认：8765)")
    parser.add_argument("--simulation", action="store_true", help="模拟模式，不连接硬件")
    parser.add_argument("--log-level", default="INFO", help="日志级别 (默认：INFO)")
    args = parser.parse_args()

    setup_logging(args.log_level)

    # 加载配置
    run_mode = "server"  # 默认值
    config = None
    try:
        from .config_loader import Config
        config = Config.get_instance()  # 使用 get_instance() 确保实例已创建

        # 从配置加载器读取 RUN_MODE 和 SIMULATION_MODE
        run_mode = config.RUN_MODE.lower()
        if config.SIMULATION_MODE:
            args.simulation = True
            print("config.env 中 SIMULATION_MODE=True，启用模拟模式")

        print(f"config.env 中 RUN_MODE={run_mode.upper()}")
    except Exception as e:
        print(f"加载配置失败：{e}，使用默认值")
        run_mode = os.environ.get("RUN_MODE", "server").lower()

    env_run_mode = os.environ.get("RUN_MODE")
    if env_run_mode:
        run_mode = env_run_mode.lower()
        print(f"环境变量覆盖 RUN_MODE={run_mode.upper()}")

    # 根据 RUN_MODE 选择运行模式
    if run_mode == "gui":
        print("启动模式：GUI")
        run_gui(args, config)
    else:
        print("启动模式：WebSocket Server")
        run_server(args, config)


if __name__ == '__main__':
    main()

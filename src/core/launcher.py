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



def setup_logging(level: str = "INFO") -> None:
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _init_robot_controller(simulation: bool = False):
    """初始化机械臂控制器，模拟模式下返回 None"""
    if simulation:
        return None

    try:
        from ..arm_sdk import RobotController
        if RobotController is None:
            raise ImportError("RobotController SDK unavailable")
        print("正在初始化机械臂...")
        robot_controller = RobotController()

        robot1 = robot_controller.init_robot1()
        if robot1 is not None:
            print("  Robot1 初始化成功")
        else:
            print("  Robot1 初始化失败")

        robot2 = robot_controller.init_robot2()
        if robot2 is not None:
            print("  Robot2 初始化成功")
        else:
            print("  Robot2 初始化失败")

        return robot_controller
    except ImportError as e:
        print(f"机械臂模块导入失败：{e}")
    except Exception as e:
        print(f"机械臂初始化异常：{e}")

    return None


def _init_body_controller(simulation: bool = False):
    """初始化身体升降平台控制器，模拟模式下返回 None"""
    if simulation:
        return None

    try:
        from ..devices import ModbusMotor
        print("正在初始化身体控制器...")
        body_controller = ModbusMotor(port="/dev/body", baudrate=115200, slave_id=1, timeout=1)
        print("  身体控制器初始化成功")
        return body_controller
    except ImportError as e:
        print(f"身体模块导入失败：{e}")
    except Exception as e:
        print(f"身体初始化异常：{e}")

    return None


def _init_neck_controller(simulation: bool = False):
    """初始化PWM颈部舵机控制器，模拟模式下返回 None"""
    if simulation:
        return None

    try:
        from ..devices import PWMNeckController
        if PWMNeckController is None:
            raise ImportError("PWMNeckController 模块不可用")
        print("正在初始化 PWM 颈部舵机...")
        neck_controller = PWMNeckController()  # 无参：从 config.env 自动读取配置
        return neck_controller
    except ImportError as e:
        print(f"PWM 颈部舵机模块导入失败：{e}")
    except Exception as e:
        print(f"PWM 颈部舵机初始化异常：{e}")

    return None


def _init_move_controller(simulation: bool = False):
    """初始化底盘移动控制器，模拟模式下返回 None"""
    if simulation:
        return None

    try:
        from ..base_move.move_controller import RobotMoveController
        print("正在初始化底盘移动控制器...")
        move_controller = RobotMoveController()
        move_controller.connect()
        return move_controller
    except ImportError as e:
        print(f"底盘移动模块导入失败：{e}")
    except Exception as e:
        print(f"底盘移动初始化异常：{e}")

    return None


def run_gui():
    """启动 GUI 模式"""
    from PyQt6.QtWidgets import QApplication
    from ..gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


def run_server(args, config=None):
    """启动 WebSocket Server 模式"""
    # 启动 WebSocket 服务
    from ..robot_server.ws_server import RobotWebSocketServer

    # 优先使用命令行参数，其次使用 config.env 配置，最后使用默认值
    host = args.host if args.host != "0.0.0.0" else (config.WEBSOCKET_HOST if config else "0.0.0.0")
    port = args.port if args.port != 8765 else (config.WEBSOCKET_PORT if config else 8765)

    server = RobotWebSocketServer(
        robot_controller=_init_robot_controller(args.simulation),
        body_controller=_init_body_controller(args.simulation),
        neck_controller=_init_neck_controller(args.simulation),
        move_controller=_init_move_controller(args.simulation),
        host=host,
        port=port,
    )

    print("=" * 50)
    print(f"机器人 WebSocket 控制服务")
    print(f"地址：ws://{host}:{port}")
    print(f"模式：{'模拟' if args.simulation else '硬件'}")
    print("=" * 50)

    try:
        server.run()
    except KeyboardInterrupt:
        print("\n服务已停止")


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
        run_gui()
    else:
        print("启动模式：WebSocket Server")
        run_server(args, config)


if __name__ == '__main__':
    main()

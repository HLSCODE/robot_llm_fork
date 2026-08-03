"""Smoke-test one optional dependency group without external I/O or hardware access."""

from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Callable, Sequence

SmokeCheck = Callable[[], None]


def _check_server() -> None:
    import numpy
    import websockets

    from src.robot_server.ws_server import RobotWebSocketServer

    server = RobotWebSocketServer(services=object())  # type: ignore[arg-type]
    if server.endpoint != "ws://127.0.0.1:8765/":
        raise RuntimeError(f"unexpected default WebSocket endpoint: {server.endpoint}")
    if not numpy.__version__ or not websockets.__version__:
        raise RuntimeError("server dependency version metadata is unavailable")


def _check_gui() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtWidgets import QApplication, QWidget

    from src.gui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.close()
    if application is None or MainWindow.__name__ != "MainWindow":
        raise RuntimeError("GUI import smoke check did not initialize correctly")


def _check_hardware() -> None:
    modules = (
        "serial",
        "pyrealsense2",
        "Robotic_Arm.rm_robot_interface",
        "src.arm_sdk.controller",
        "src.cameras.realsense_manager",
    )
    for module_name in modules:
        importlib.import_module(module_name)


SMOKE_CHECKS: dict[str, SmokeCheck] = {
    "gui": _check_gui,
    "server": _check_server,
    "hardware": _check_hardware,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an optional dependency group without connecting external resources."
    )
    parser.add_argument("extra", choices=tuple(SMOKE_CHECKS))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    SMOKE_CHECKS[arguments.extra]()
    print(f"Optional dependency smoke passed: {arguments.extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

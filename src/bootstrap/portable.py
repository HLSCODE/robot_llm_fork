"""Entry-point setup shared by the two Windows portable executables."""

from __future__ import annotations

import argparse
import io
import logging
import os
from pathlib import Path
import sys

from ..configuration.runtime_paths import application_root, is_portable


def prepare_environment() -> Path:
    root = application_root()
    if not is_portable():
        return root
    os.chdir(root)
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    # Keep downloaded models with this installation; respect explicit overrides.
    os.environ.setdefault("MODELSCOPE_CACHE", str(root / "models" / "modelscope"))
    os.environ.setdefault("HF_HOME", str(root / "models" / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(root / "models" / "torch"))
    # A frozen application cannot pip-install new runtime dependencies.
    os.environ["YOLO_AUTOINSTALL"] = "false"
    return root


def initializer_main() -> int:
    # The parent initializer decodes worker output as UTF-8. Windows redirected
    # stdio otherwise uses the active ANSI code page instead of console Unicode.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    prepare_environment()
    if sys.argv[1:] == ["--portable-smoke-check"]:
        return smoke_check()
    if sys.argv[1:2] == ["--prepare-asr-worker"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--project-root", type=Path, required=True)
        args = parser.parse_args(sys.argv[2:])
        os.chdir(args.project_root)
        from ..configuration.config_loader import load_application_settings
        from .initialization import prepare_asr_models

        logging.basicConfig(level=logging.INFO)
        prepare_asr_models(
            load_application_settings(args.project_root / "config" / "config.toml").voice,
            log=lambda message: print(message, flush=True),
        )
        return 0
    from .initialization_cli import main

    return main()


def smoke_check() -> int:
    """Verify frozen resources and Qt plugins without opening any devices."""
    from importlib.metadata import version
    from PySide6.QtWidgets import QApplication, QWidget
    from ..application.builtin_data import _BUILTIN_CATALOG_ROOT
    from ..gui.controllers.main_window import MainWindow
    from ..devices.robots.tianji.driver import TianjiRobotDriver

    assert MainWindow is not None and TianjiRobotDriver is not None
    if not tuple(_BUILTIN_CATALOG_ROOT.rglob("*.json")):
        raise RuntimeError("便携包缺少内置数据资源")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication([])
    widget = QWidget()
    widget.show()
    app.processEvents()
    widget.close()
    print(f"Portable smoke check passed: {version('robot-llm')}")
    return 0


def gui_main() -> int:
    root = prepare_environment()
    # Windowed bootloaders have no stdio; retain early startup failures on disk.
    (root / "logs").mkdir(parents=True, exist_ok=True)
    with (root / "logs" / "portable-startup.log").open(
        "a", encoding="utf-8", buffering=1
    ) as startup_log:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        if sys.stdout is None:
            sys.stdout = startup_log
        if sys.stderr is None:
            sys.stderr = startup_log
        try:
            from ..configuration.config_initializer import initialize_configuration
            from .launcher import main

            initialize_configuration(root)
            return main()
        except Exception:
            import traceback

            traceback.print_exc(file=startup_log)
            raise
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

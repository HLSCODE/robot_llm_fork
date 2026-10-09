import multiprocessing
from pathlib import Path
import sys

from src.bootstrap.portable import gui_main, initializer_main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    entry = initializer_main if Path(sys.executable).stem == "robot-init" else gui_main
    raise SystemExit(entry())

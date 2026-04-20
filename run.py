#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# RUN_MODE=gui    → PyQt6 图形界面
# RUN_MODE=server → WebSocket 服务（默认）
# 启动模式由 src.core.launcher 根据环境变量自动选择

if __name__ == '__main__':
    from src.core import main
    main()

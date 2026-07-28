#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# GUI 是应用主宿主；WebSocket 等可选网络服务随 GUI 共享同一运行时。

if __name__ == '__main__':
    from src.core import main
    raise SystemExit(main())

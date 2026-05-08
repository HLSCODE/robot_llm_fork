# -*- coding: utf-8 -*-
"""
ADP 吐液（全部吐出）
"""

import sys
import os

# 将项目根目录添加到系统路径
project_root = '/home/maic/robot_llm_fork'
sys.path.insert(0, project_root)

# 然后使用相对的模块路径进行导入
from src.devices.adp import ADP

ADP_PORT = '/dev/ttyUSB2'


def main():
    print("=" * 50)
    print("ADP 吐液（全部吐出）")
    print("=" * 50)

    print(f"\n串口: {ADP_PORT}")

    adp = ADP(port=ADP_PORT)

    print("\n正在吐液...")
    ret = adp.dispense_all()

    if ret:
        print("吐液成功!")
    else:
        print("吐液失败")

    adp.close()


if __name__ == "__main__":
    main()

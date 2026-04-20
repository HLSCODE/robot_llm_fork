# -*- coding: utf-8 -*-
"""
ADP 吸液
"""
from ..devices.adp import ADP

ADP_PORT = '/dev/hand'
ABSORB_VOLUME = 500 # 吸液体积（微升）


def main():
    print("=" * 50)
    print("ADP 吸液")
    print("=" * 50)

    print(f"\n串口: {ADP_PORT}")
    print(f"吸液体积: {ABSORB_VOLUME} ul")

    adp = ADP(port=ADP_PORT)

    print("\n正在吸液...")
    ret = adp.absorb(ABSORB_VOLUME)

    if ret:
        print("吸液成功!")
    else:
        print("吸液失败")

    adp.close()


if __name__ == "__main__":
    main()

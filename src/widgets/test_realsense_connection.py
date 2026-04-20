# -*- coding: utf-8 -*-
"""
RealSense 连接测试脚本 - 对标 GUI_0.py 的 pipeline 初始化逻辑

运行方式:
    cd /path/to/repo
    python src/widgets/test_realsense_connection.py

排除问题：
  - 如果此脚本能正常打印帧信息  -> frame_grabber.py 封装有问题
  - 如果此脚本也超时           -> 硬件/驱动/USB 带宽问题
"""

import time
import pyrealsense2 as rs

# 在此填写目标相机的 SN（从 Step 1 列表中确认）
# 例如：TARGET_SN = "153122077516"
TARGET_SN = "153122077516"  # None 表示 auto-select


def main():
    # 1. 列出所有 RealSense 设备
    print("=" * 50)
    print("Step 1: 列出 RealSense 设备")
    print("=" * 50)
    ctx = rs.context()
    devices = list(ctx.devices)
    if not devices:
        print("未检测到任何 RealSense 设备！")
        return

    device_sns = []
    for i, dev in enumerate(devices):
        sn = dev.get_info(rs.camera_info.serial_number)
        device_sns.append(sn)
        print(
            f"  [{i}] {dev.get_info(rs.camera_info.name)}  "
            f"SN={sn}"
        )

    # 2. 确认目标 SN 是否存在
    print()
    print("=" * 50)
    print("Step 2: 确认目标设备")
    print("=" * 50)
    if TARGET_SN:
        if TARGET_SN not in device_sns:
            print(f"  警告: 目标 SN={TARGET_SN} 未在列表中找到！")
            print(f"  将 fallback 到 auto-select")
            TARGET_SN = None
        else:
            print(f"  目标 SN={TARGET_SN} 确认存在")
    else:
        print(f"  未指定 SN，使用 auto-select（可能受设备枚举顺序影响）")

    # 3. 启动 pipeline
    print()
    print("=" * 50)
    print("Step 3: 启动 pipeline")
    print("=" * 50)
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)

    if TARGET_SN:
        config.enable_device(TARGET_SN)
        print(f"  已绑定设备 SN={TARGET_SN}")

    try:
        profile = pipeline.start(config)
        print("  pipeline.start() 成功")
    except Exception as e:
        print(f"  pipeline.start() 失败: {e}")
        return

    # 4. 预热 1 秒（对应 GUI_0.py 第 409 行）
    print()
    print("=" * 50)
    print("Step 4: 预热 1 秒")
    print("=" * 50)
    time.sleep(1)
    print("  预热完成")

    # 5. wait_for_frames() 无参数取帧
    print()
    print("=" * 50)
    print("Step 5: wait_for_frames() 取帧")
    print("=" * 50)
    print("  等待 frames ... (最多等待 10 秒)")

    try:
        frames = pipeline.wait_for_frames(10000)
        color = frames.get_color_frame()
        depth = frames.get_depth_frame()
    except Exception as e:
        print(f"  取帧失败: {e}")
        pipeline.stop()
        return

    if not color or not depth:
        print("  帧为空！")
        pipeline.stop()
        return

    # 6. 打印结果
    print()
    print("=" * 50)
    print("Step 6: 结果")
    print("=" * 50)
    print(f"  color: {color.width} x {color.height}  format={color.profile.format()}")
    center_dist = depth.get_distance(color.width // 2, color.height // 2)
    print(f"  depth @ center ({color.width//2}, {color.height//2}): {center_dist:.3f} m")
    print()
    print("  SUCCESS: RealSense 连接正常！")

    pipeline.stop()


if __name__ == "__main__":
    main()

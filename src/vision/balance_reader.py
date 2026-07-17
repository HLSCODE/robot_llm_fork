# -*- coding: utf-8 -*-
"""
天平数字读取模块。

利用 USB 摄像头（通过 OpenCVCameraManager）拍摄天平屏幕，
经七段数码管识别/OCR 识别后返回数值。

依赖（需额外安装）:
    ssocr           — 七段数码管专用识别工具（推荐）
    easyocr>=1.7    — OCR 兜底

安装:
    sudo apt install ssocr
    pip install easyocr

使用方式:
    from vision.balance_reader import read_balance

    value = read_balance()          # 默认读取一次
    value = read_balance(tries=3)   # 连续读 3 次取均值（天平稳定中）
    # 返回值: 123.45 或 None（读取失败）
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)

try:
    import easyocr

    _EASYOCR_AVAILABLE = True
except ImportError:
    _EASYOCR_AVAILABLE = False
    logger.warning("easyocr 未安装，将仅在 ssocr 可用时读取天平")

_SSOCR_AVAILABLE = shutil.which("ssocr") is not None
if not _SSOCR_AVAILABLE:
    logger.warning("ssocr 未安装，七段数码管识别不可用 (sudo apt install ssocr)")


# ------------------------------------------------------------------
# 全局 OCR reader 缓存（EasyOCR 第一次加载较慢）
# ------------------------------------------------------------------
_reader = None


def _get_reader():
    """获取或初始化 EasyOCR reader（全局单例，避免重复加载模型）。"""
    global _reader
    if _reader is None and _EASYOCR_AVAILABLE:
        # ['en'] 即可覆盖数字 + 小数点 + 负号
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


# ------------------------------------------------------------------
# 核心接口
# ------------------------------------------------------------------


def read_balance(
    camera_name: str | None = None,
    tries: int = 1,
    delay: float = 0.3,
    roi: tuple[int, int, int, int] | None = None,
    allowed_chars: str = r"[\d.\-]",
) -> float | None:
    """从天平屏幕读取数值。

    流程:
        1. 通过 OpenCVCameraManager 获取一帧画面
        2. 如果指定了 roi（感兴趣区域），先裁剪画面
        3. 优先用 ssocr 识别七段数码管，失败时回退 EasyOCR
        4. 提取符合数值格式的内容（数字 + 小数点 + 负号）
        5. 转换为 float

    Args:
        camera_name: 相机名称（默认取第一路 USB 摄像头）
        tries:       重复读取次数（天平数值跳动时，取多次均值）
        delay:       每次重试间隔（秒）
        roi:         感兴趣区域 (x, y, w, h)，用于裁剪画面
                     避免识别到画面中其他无关数字。
                     例如 roi=(100, 200, 300, 100) 表示从 (100,200) 起
                     宽 300、高 100 的区域。
        allowed_chars: OCR 结果过滤规则，默认只保留数字、小数点、负号

    Returns:
        float 值（成功）或 None（失败）

    Examples:
        >>> read_balance()
        123.45
        >>> read_balance(tries=3)              # 多次取均值
        123.46
        >>> read_balance(roi=(50, 100, 200, 80))  # 只识别天平屏幕区域
        123.45
    """
    if not _SSOCR_AVAILABLE and not _EASYOCR_AVAILABLE:
        logger.error("ssocr/easyocr 均不可用，无法读取天平")
        return None

    reader = _get_reader() if _EASYOCR_AVAILABLE else None

    values: list[float] = []

    for i in range(tries):
        try:
            frame = _capture_frame(camera_name, retries=5)
            if frame is None:
                logger.warning("第 %d 次尝试: 取帧失败", i + 1)
                time.sleep(delay)
                continue

            value = _ocr_frame(reader, frame, roi, allowed_chars)
            if value is not None:
                values.append(value)
                logger.info("第 %d 次读取: %.3f", i + 1, value)
            else:
                logger.warning("第 %d 次尝试: 未识别到有效数字", i + 1)

            time.sleep(delay)

        except Exception as e:
            logger.warning("第 %d 次读取异常: %s", i + 1, e)
            time.sleep(delay)

    if not values:
        logger.error("天平读取失败: %d 次尝试均未识别到有效值", tries)
        return None

    # 多次读取取均值（剔除明显异常值）
    if len(values) >= 3:
        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        filtered = [v for v in values if abs(v - mean) < 2 * std]
        if filtered:
            return round(sum(filtered) / len(filtered), 3)

    return round(sum(values) / len(values), 3)


# ------------------------------------------------------------------
# 内部方法
# ------------------------------------------------------------------


def _capture_frame(camera_name: str | None = None, retries: int = 5):
    """从 OpenCVCameraManager 取一帧彩色图 (numpy array, BGR)。

    相机刚启动时采集线程可能还没产出第一帧，会重试多次。

    Args:
        camera_name: 相机名称
        retries:     重试次数（每次间隔 0.2 秒）

    Returns:
        numpy array (BGR) 或 None
    """
    from src.cameras.camera_factory import get_camera_manager

    mgr = get_camera_manager()
    if mgr is None:
        logger.error("相机管理器未启动")
        return None

    # 优先尝试 get_latest_raw_frames（RealSense 专用），回退到 JPEG 解码
    raw = getattr(mgr, "get_latest_raw_frames", None)
    if raw:
        result = raw(camera_name)
        if result and result[0] is not None:
            return result[0]  # color frame

    # 回退: 取 JPEG 解码（带重试，等待相机产出第一帧）
    for attempt in range(retries):
        jpegs = mgr.get_latest_jpegs()
        if jpegs:
            # 如果指定了相机名，按名称匹配
            if camera_name:
                for serial, name, jpg in jpegs:
                    if name == camera_name:
                        return _jpeg_to_array(jpg)
                logger.warning("未找到相机 '%s'，使用第一路", camera_name)

            # 默认取第一路
            import cv2
            import numpy as np

            arr = np.frombuffer(jpegs[0][2], dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if attempt < retries - 1:
            time.sleep(0.2)

    logger.error("相机无可用帧（重试 %d 次后仍失败）", retries)
    return None


def _jpeg_to_array(jpg_bytes: bytes):
    """将 JPEG bytes 解码为 numpy array (BGR)。"""
    import cv2
    import numpy as np

    arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _ocr_frame(reader, frame, roi: tuple[int, int, int, int] | None, allowed_chars: str) -> float | None:
    """对一帧画面（或其中指定区域）执行 OCR 并提取数值。

    策略:
        1. 如果指定了 roi，先裁剪画面到感兴趣区域（避免其他数字干扰）
        2. 优先使用 ssocr 识别七段数码管，失败时回退 EasyOCR
        3. 按 allowed_chars 过滤，提取合法的数值字符串
        4. 对多个预处理版本的结果做聚合，取最稳定的合法数值
    """
    # 裁剪 ROI（可选）
    if roi is not None:
        x, y, w, h = roi
        # 边界保护
        h_img, w_img = frame.shape[:2]
        x = max(0, min(x, w_img - 1))
        y = max(0, min(y, h_img - 1))
        w = min(w, w_img - x)
        h = min(h, h_img - y)
        frame = frame[y : y + h, x : x + w]
        logger.debug("ROI 裁剪: (%d, %d, %d, %d) → 实际裁剪后 %dx%d", x, y, w, h, frame.shape[1], frame.shape[0])

    if _SSOCR_AVAILABLE:
        value = _ssocr_frame(frame, allowed_chars)
        if value is not None:
            logger.debug("ssocr 读取成功: %.6f", value)
            return value
        logger.debug("ssocr 未读到有效数字，回退 EasyOCR")

    if reader is None:
        return None

    candidates: list[tuple[float, str, float]] = []  # (置信度, 原始 token, 数值)
    allowlist = _regex_to_easyocr_allowlist(allowed_chars)

    for image in _build_balance_ocr_inputs(frame):
        try:
            results = reader.readtext(
                image,
                allowlist=allowlist,
                decoder="greedy",
                paragraph=False,
            )
        except TypeError:
            # 兼容较旧 EasyOCR 版本。
            results = reader.readtext(image)

        for bbox, text, confidence in results:
            # 清理文本: 只保留数字、小数点、负号
            cleaned = "".join(re.findall(allowed_chars, text.strip()))
            if not cleaned:
                continue

            # 处理多个连续匹配的情况（如 "12345-67" → 取每个有效值）
            for token in re.findall(r"-?\d+\.?\d*", cleaned):
                try:
                    value = float(token)
                    candidates.append((confidence, token, value))
                except ValueError:
                    continue

    if not candidates:
        return None

    # 多个预处理版本可能给出同一结果；用聚合分数避免某一张噪声图的高置信误读。
    scores: dict[str, tuple[float, int, float]] = {}
    for confidence, token, value in candidates:
        total_confidence, count, _ = scores.get(token, (0.0, 0, value))
        scores[token] = (total_confidence + confidence, count + 1, value)

    ranked = sorted(
        scores.items(),
        key=lambda item: (item[1][1], item[1][0] / item[1][1], item[1][0]),
        reverse=True,
    )
    logger.debug("OCR 候选: %s", candidates)

    return ranked[0][1][2]


def _ssocr_frame(frame, allowed_chars: str) -> float | None:
    """使用 ssocr 读取七段数码管；失败时返回 None，由 EasyOCR 兜底。"""
    import cv2

    candidates: list[tuple[int, str, float]] = []

    with tempfile.TemporaryDirectory(prefix="balance_ssocr_") as tmp_dir:
        for index, image in enumerate(_build_ssocr_inputs(frame)):
            path = f"{tmp_dir}/balance_{index}.png"
            cv2.imwrite(path, image)

            try:
                result = subprocess.run(
                    ["ssocr", path],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("ssocr 执行失败: %s", exc)
                continue

            if result.returncode != 0:
                logger.debug("ssocr 返回失败: %s", result.stderr.strip())
                continue

            text = result.stdout.strip()
            for token in _extract_numeric_tokens(text, allowed_chars):
                try:
                    value = float(token)
                except ValueError:
                    continue
                candidates.append((len(token), token, value))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    logger.debug("ssocr 候选: %s", candidates)
    return candidates[0][2]


def _extract_numeric_tokens(text: str, allowed_chars: str) -> list[str]:
    """从 OCR 输出中提取数值 token。"""
    cleaned = "".join(re.findall(allowed_chars, text.strip()))
    if not cleaned:
        return []
    return re.findall(r"-?\d+\.?\d*", cleaned)


def _regex_to_easyocr_allowlist(allowed_chars: str) -> str | None:
    """将默认过滤规则转换为 EasyOCR allowlist，减少 0/8 这类相近字符误判。"""
    if allowed_chars == r"[\d.\-]":
        return "0123456789.-"
    return None


def _build_ssocr_inputs(frame):
    """生成 ssocr 输入图；ssocr 对七段显示更敏感，尝试多种前景极性和阈值。"""
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    scale = 4 if max(h, w) < 320 else 2
    enlarged = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(clahe, (3, 3), 0)

    _, otsu_dark = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, otsu_light = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive_dark = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        7,
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    otsu_dark = cv2.morphologyEx(otsu_dark, cv2.MORPH_OPEN, kernel)
    adaptive_dark = cv2.morphologyEx(adaptive_dark, cv2.MORPH_OPEN, kernel)

    return [
        enlarged,
        clahe,
        otsu_dark,
        otsu_light,
        adaptive_dark,
        cv2.bitwise_not(adaptive_dark),
        np.pad(otsu_dark, 8, mode="constant", constant_values=0),
    ]


def _build_balance_ocr_inputs(frame):
    """生成适合天平 LCD/数码管的 OCR 输入图。

    0 被识别成 8 往往是因为反光或残影让中间横段变暗。这里会生成多张
    增强/二值化图，并对二值图做轻微开运算，尽量去掉细小伪横线。
    """
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    scale = 3 if max(h, w) < 320 else 2
    enlarged = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(clahe, (3, 3), 0)

    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(otsu) > 127:
        otsu = cv2.bitwise_not(otsu)

    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        7,
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    otsu_open = cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel)
    adaptive_open = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel)

    def to_rgb(gray_image):
        return cv2.cvtColor(gray_image, cv2.COLOR_GRAY2RGB)

    return [
        cv2.cvtColor(enlarged, cv2.COLOR_BGR2RGB),
        to_rgb(clahe),
        to_rgb(otsu),
        to_rgb(otsu_open),
        to_rgb(adaptive_open),
    ]


# ------------------------------------------------------------------
# 辅助：交互式标定 ROI
# ------------------------------------------------------------------


def calibrate_roi(camera_name: str | None = None):
    """交互式标定 ROI — 用鼠标在画面上框选天平屏幕区域。

    使用 tkinter + Pillow 显示画面，鼠标拖拽选框，
    释放鼠标后自动确认选区。

    Returns:
        (x, y, w, h) 或 None（取消）
    """
    import cv2
    import tkinter as tk
    from tkinter import messagebox
    from PIL import Image, ImageTk

    frame = _capture_frame(camera_name, retries=10)
    if frame is None:
        logger.error("无法获取相机画面")
        return None

    # BGR → RGB → PIL Image
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    # 如果图片太大，等比缩放到屏幕适合的大小
    max_w, max_h = 1000, 700
    scale = min(max_w / pil_img.width, max_h / pil_img.height, 1.0)
    if scale < 1.0:
        display_img = pil_img.resize((int(pil_img.width * scale), int(pil_img.height * scale)), Image.LANCZOS)
    else:
        display_img = pil_img
        scale = 1.0

    # ── 构建 tkinter 窗口 ──
    root = tk.Tk()
    root.title("ROI 标定 — 鼠标拖拽框选天平屏幕，松开确认，右键/ESC 取消")

    rect_id = None
    start_x = start_y = 0
    roi_result: list[tuple[int, int, int, int] | None] = [None]

    canvas = tk.Canvas(root, width=display_img.width, height=display_img.height)
    canvas.pack()

    photo = ImageTk.PhotoImage(display_img)
    canvas.create_image(0, 0, anchor=tk.NW, image=photo)

    def on_press(event):
        nonlocal start_x, start_y, rect_id
        start_x, start_y = event.x, event.y
        if rect_id:
            canvas.delete(rect_id)
        rect_id = canvas.create_rectangle(start_x, start_y, start_x, start_y,
                                          outline="red", width=2)

    def on_drag(event):
        nonlocal rect_id
        if rect_id:
            canvas.delete(rect_id)
        rect_id = canvas.create_rectangle(start_x, start_y, event.x, event.y,
                                          outline="red", width=2)

    def on_release(event):
        nonlocal rect_id
        x1, y1 = min(start_x, event.x), min(start_y, event.y)
        x2, y2 = max(start_x, event.x), max(start_y, event.y)
        w, h = x2 - x1, y2 - y1
        if w < 10 or h < 10:
            return  # 选区太小，忽略

        # 转换回原始坐标
        orig_x = round(x1 / scale)
        orig_y = round(y1 / scale)
        orig_w = round(w / scale)
        orig_h = round(h / scale)

        roi_result[0] = (orig_x, orig_y, orig_w, orig_h)
        root.quit()

    def on_cancel(event=None):
        roi_result[0] = None
        root.quit()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", on_cancel)  # 右键取消
    root.bind("<Escape>", on_cancel)       # ESC 取消

    # 窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    root.mainloop()
    root.destroy()

    if roi_result[0] is None:
        logger.info("ROI 标定已取消")
        return None

    logger.info("ROI 标定结果: (x=%d, y=%d, w=%d, h=%d)", *roi_result[0])
    return roi_result[0]


# ------------------------------------------------------------------
# CLI 快速测试
# ------------------------------------------------------------------

_ROI_FILE = "balance_roi.json"


def _load_saved_roi() -> tuple[int, int, int, int] | None:
    """从本地文件加载已保存的 ROI。"""
    import json
    import os

    if not os.path.exists(_ROI_FILE):
        return None
    try:
        with open(_ROI_FILE) as f:
            data = json.load(f)
        roi = (data["x"], data["y"], data["w"], data["h"])
        logger.info("已加载保存的 ROI: (x=%d, y=%d, w=%d, h=%d)", *roi)
        return roi
    except Exception as e:
        logger.warning("加载 ROI 文件失败: %s", e)
        return None


def _save_roi(roi: tuple[int, int, int, int]):
    """保存 ROI 到本地文件。"""
    import json

    with open(_ROI_FILE, "w") as f:
        json.dump({"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]}, f)
    logger.info("ROI 已保存至 %s: (x=%d, y=%d, w=%d, h=%d)", _ROI_FILE, *roi)


def main():
    """CLI 入口：运行交互式 ROI 标定后连续读取天平数值。"""
    import argparse

    parser = argparse.ArgumentParser(description="天平数字读取")
    parser.add_argument("--roi", type=str, help="ROI 区域 x,y,w,h，如 '100,200,300,100'")
    parser.add_argument("--calibrate", action="store_true", help="交互式标定 ROI 并保存")
    parser.add_argument("--tries", type=int, default=3, help="读取次数（默认 3）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    roi = None
    if args.roi:
        parts = [int(x.strip()) for x in args.roi.split(",")]
        if len(parts) == 4:
            roi = tuple(parts)  # type: ignore
    elif args.calibrate:
        roi = calibrate_roi()
        if roi is not None:
            _save_roi(roi)
    else:
        # 默认尝试加载之前保存的 ROI
        roi = _load_saved_roi()

    val = read_balance(tries=args.tries, roi=roi)
    if val is not None:
        print(f"天平读数: {val}")
    else:
        print("读取失败")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""极简天平读数：拍一张摄像头画面，交给视觉大模型读取数字。"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request

API_KEY = os.getenv("VVEAI_API_KEY", "")
BASE_URL = os.getenv("VVEAI_BASE_URL", "https://api.vveai.com/v1").rstrip("/")
MODEL = os.getenv("VVEAI_MODEL", "doubao-seed-1-8-251228")


def capture_image(camera_index: int = 12):
    import cv2

    cap = cv2.VideoCapture(camera_index)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"无法读取摄像头 index={camera_index}")
        return frame
    finally:
        cap.release()


def image_to_data_url(frame) -> str:
    import cv2

    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("图片编码失败")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def ask_model(image_url: str) -> str:
    if not API_KEY:
        raise RuntimeError("请先设置环境变量 VVEAI_API_KEY")

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "读取图中天平显示的数值，只返回数字。"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 16,
    }

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_value(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        raise RuntimeError(f"模型返回中没有数字: {text}")
    return float(match.group(0))


def read_balance(camera_index: int = 12) -> float:
    frame = capture_image(camera_index)
    reply = ask_model(image_to_data_url(frame))
    return parse_value(reply)


if __name__ == "__main__":
    value = read_balance()
    print(value)

# 